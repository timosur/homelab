import asyncio
import logging
import signal
import sys

from aiohttp import web

from .config import load_config
from .proxy import ProxyBackend

log = logging.getLogger("wol-proxy")


async def run(config_path: str) -> None:
    config = load_config(config_path)
    if not config.backends:
        log.error("No backends configured")
        sys.exit(1)

    runners: list[web.AppRunner] = []
    tasks: list[asyncio.Task] = []

    for backend_cfg in config.backends:
        backend = ProxyBackend(backend_cfg)

        tasks.append(asyncio.create_task(backend.idle_watcher()))

        app = web.Application()
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
