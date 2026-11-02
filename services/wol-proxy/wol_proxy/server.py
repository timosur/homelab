import asyncio
import logging
import signal
import ssl
import sys

from aiohttp import web

from .config import load_config
from .admission import admission_app
from .proxy import NodeGroupProxy, ProxyBackend
from .sandboxes import SandboxMonitor

log = logging.getLogger("wol-proxy")


async def reload_admission_tls(context: ssl.SSLContext) -> None:
    while True:
        await asyncio.sleep(300)
        try:
            context.load_cert_chain("/run/dark-factory-admission/tls.crt", "/run/dark-factory-admission/tls.key")
        except (OSError, ssl.SSLError) as exc:
            log.warning("Cannot reload admission certificate: %s", exc)


async def run(config_path: str) -> None:
    config = load_config(config_path)
    if not config.backends and not config.node_groups:
        log.error("No backends or node groups configured")
        sys.exit(1)

    runners: list[web.AppRunner] = []
    tasks: list[asyncio.Task] = []
    admission_groups = [group for group in config.node_groups if group.sandbox_admission_enabled]
    if len(admission_groups) > 1:
        raise ValueError("only one sandbox admission group is supported")
    if admission_groups:
        namespace = admission_groups[0].sandbox_namespace
        if not namespace:
            raise ValueError("sandbox admission requires a namespace")
        admission_monitor = SandboxMonitor(
            namespace, admission_groups[0].sandbox_instance, admission_groups[0].kubernetes_node
        )
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain("/run/dark-factory-admission/tls.crt", "/run/dark-factory-admission/tls.key")
        admission_runner = web.AppRunner(admission_app(
            namespace, admission_groups[0].kubernetes_node, admission_monitor.can_bind, admission_monitor.can_release,
        ))
        await admission_runner.setup()
        await web.TCPSite(admission_runner, "0.0.0.0", 9443, ssl_context=tls).start()
        runners.append(admission_runner)
        tasks.append(asyncio.create_task(reload_admission_tls(tls)))

    for backend_cfg in config.backends:
        backend = ProxyBackend(backend_cfg)
        await backend.check_initial_state()

        tasks.append(asyncio.create_task(backend.idle_watcher()))

        app = web.Application(client_max_size=100 * 1024 * 1024)
        app.router.add_route("*", "/{path_info:.*}", backend.handle_request)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", backend_cfg.listen_port)
        await site.start()
        runners.append(runner)

        log.info(
            "[%s] HTTP proxy listening on :%d -> %s:%d",
            backend_cfg.name,
            backend_cfg.listen_port,
            backend_cfg.target_host,
            backend_cfg.target_port,
        )

    for ng_cfg in config.node_groups:
        ng_proxy = NodeGroupProxy(ng_cfg)
        await ng_proxy.check_initial_state()
        tasks.append(asyncio.create_task(ng_proxy.idle_watcher()))
        tasks.append(asyncio.create_task(ng_proxy.sandbox_watcher()))

        app = web.Application(client_max_size=100 * 1024 * 1024)
        app.router.add_route("*", "/{path_info:.*}", ng_proxy.handle_request)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", ng_cfg.listen_port)
        await site.start()
        runners.append(runner)

        hostnames = ", ".join(b.hostname for b in ng_cfg.backends)
        log.info(
            "[%s] HTTP node-group proxy listening on :%d for: %s",
            ng_cfg.name,
            ng_cfg.listen_port,
            hostnames,
        )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    log.info("WoL Proxy started")
    await stop.wait()

    log.info("Shutting down...")
    for t in tasks:
        t.cancel()
    for r in runners:
        await r.cleanup()
    log.info("WoL Proxy stopped")
