import asyncio
import logging
import signal
import ssl
import sys

from aiohttp import web

from .config import load_config
from .admission import admission_app
from .coder_admission import register as register_coder_admission
from .coder_preflight import register as register_coder_preflight
from .coder_trust import publish_trust
from .proxy import NodeGroupProxy, ProxyBackend

log = logging.getLogger("wol-proxy")


def build_proxies(config):
    groups = [NodeGroupProxy(group) for group in config.node_groups]
    names = [backend.name for backend in config.backends]
    if len(set(names)) != len(names):
        raise ValueError("backend names must be distinct")
    managed = {}
    claimed_hosts = set()
    claimed_macs = set()
    for group in groups:
        pool = group.cfg.sandbox_pool
        if pool is None:
            continue
        backend_names = pool.backends
        if (
            not isinstance(backend_names, list)
            or not all(isinstance(name, str) and name for name in backend_names)
            or len(set(backend_names)) != len(backend_names)
        ):
            raise ValueError("sandbox pool backend names must be distinct strings")
        hosts = {worker.suspend_host for worker in pool.workers}
        macs = {worker.wol_mac.lower() for worker in pool.workers}
        if claimed_hosts & hosts or claimed_macs & macs:
            raise ValueError("sandbox pools cannot share a power host")
        claimed_hosts.update(hosts)
        claimed_macs.update(macs)
        expected = {backend.name for backend in config.backends
                    if backend.target_host in hosts or backend.wol_mac.lower() in macs}
        if set(backend_names) != expected or any(name in managed for name in backend_names):
            raise ValueError("sandbox pool must coordinate every matching backend exactly once")
        if any(
            not any(backend.target_host == worker.suspend_host and backend.wol_mac.lower() == worker.wol_mac.lower()
                    for worker in pool.workers)
            for backend in config.backends if backend.name in backend_names
        ):
            raise ValueError("coordinated backend host and MAC must match the same sandbox worker")
        for name in backend_names:
            managed[name] = group
    for group in groups:
        if group.cfg.sandbox_pool is None and (
            group.cfg.suspend_host in claimed_hosts or group.cfg.wol_mac.lower() in claimed_macs
        ):
            raise ValueError("uncoordinated node group overlaps a sandbox pool")
    backends = [
        ProxyBackend(backend, coordinator=managed[backend.name]._coordinator, sleep_managed=True)
        if backend.name in managed else ProxyBackend(backend)
        for backend in config.backends
    ]
    return groups, backends


async def reload_admission_tls(context: ssl.SSLContext) -> None:
    while True:
        await asyncio.sleep(300)
        try:
            context.load_cert_chain("/run/dark-factory-admission/tls.crt", "/run/dark-factory-admission/tls.key")
        except (OSError, ssl.SSLError) as exc:
            log.warning("Cannot reload admission certificate: %s", exc)


def build_admission_app(group):
    cfg = group.cfg
    nodes = (tuple(worker.node for worker in cfg.sandbox_pool.workers)
             if cfg.sandbox_pool is not None else cfg.kubernetes_node)
    monitor = group._sandbox_monitor
    app = admission_app(
        cfg.sandbox_namespace, nodes, monitor.can_bind, monitor.can_release,
        workspace_instance=cfg.sandbox_instance,
        workspace_storage_class=cfg.sandbox_workspace_storage_class,
        workspace_delete_actor=cfg.sandbox_workspace_delete_actor,
        can_delete=monitor.can_delete_workspace,
    )
    if cfg.coder_workspace_namespace:
        register_coder_admission(app, cfg.coder_workspace_namespace, nodes, monitor.can_release_coder, monitor.coder_binding_pod)
        register_coder_preflight(app, group, cfg.coder_preflight_token_path, timeout=cfg.wake_timeout_seconds)
    return app


async def run(config_path: str) -> None:
    config = load_config(config_path)
    if not config.backends and not config.node_groups:
        log.error("No backends or node groups configured")
        sys.exit(1)

    runners: list[web.AppRunner] = []
    tasks: list[asyncio.Task] = []
    groups, backends = build_proxies(config)
    admission_groups = [group for group in groups if group.cfg.sandbox_admission_enabled]
    if len(admission_groups) > 1:
        raise ValueError("only one sandbox admission group is supported")
    if admission_groups:
        group = admission_groups[0]
        namespace = group.cfg.sandbox_namespace
        if not namespace:
            raise ValueError("sandbox admission requires a namespace")
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain("/run/dark-factory-admission/tls.crt", "/run/dark-factory-admission/tls.key")
        admission_runner = web.AppRunner(build_admission_app(group))
        await admission_runner.setup()
        await web.TCPSite(admission_runner, "0.0.0.0", 9443, ssl_context=tls).start()
        runners.append(admission_runner)
        tasks.append(asyncio.create_task(reload_admission_tls(tls)))
        if group.cfg.coder_workspace_namespace:
            monitor = group._sandbox_monitor
            primary = monitor.monitors[0] if group.cfg.sandbox_pool is not None else monitor
            tasks.append(asyncio.create_task(publish_trust(
                primary, group.cfg.coder_workspace_namespace, "/run/dark-factory-admission/ca.crt",
            )))

    for backend in backends:
        backend_cfg = backend.cfg
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

    for ng_proxy in groups:
        ng_cfg = ng_proxy.cfg
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
