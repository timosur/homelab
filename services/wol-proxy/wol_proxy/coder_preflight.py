"""Internal authenticated start preflight for Coder workspaces."""

import asyncio
import hmac
import logging
import time
import uuid
from pathlib import Path

from aiohttp import ClientError, web

from .interlock import InterlockError

log = logging.getLogger("wol-proxy")


def register(app, proxy, token_path, *, timeout, interval=1):
    async def start(request):
        try:
            token = Path(token_path).read_bytes().strip()
            authorization = request.headers.get("Authorization", "").encode("utf-8")
            if len(token) < 32 or not hmac.compare_digest(authorization, b"Bearer " + token):
                return web.Response(status=403, text="Forbidden")
        except (OSError, ValueError):
            return web.Response(status=503, text="Preflight authentication unavailable")
        try:
            body = await request.json()
            workspace = body["workspace_id"]
            if (not isinstance(body, dict) or set(body) != {"workspace_id"}
                    or not isinstance(workspace, str) or str(uuid.UUID(workspace)) != workspace):
                raise ValueError("invalid workspace")
        except (KeyError, TypeError, ValueError):
            return web.Response(status=400, text="Invalid workspace identity")

        coordinator = proxy._coordinator
        if coordinator is None:
            return web.Response(status=503, text="Power coordination unavailable")
        log.info("Coder start preflight accepted for workspace %s", workspace)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                async with proxy._activity_lock:
                    if coordinator.accepting and await proxy._sandbox_monitor.nodes_schedulable():
                        await coordinator.interlock.coder_reservation(workspace, reserve=True)
                        proxy.touch()
                        return web.json_response({"status": "ready"})
                    proxy._wake_requested = True
                    proxy.touch()
            except (ClientError, InterlockError, OSError, RuntimeError, ValueError, KeyError, TimeoutError):
                coordinator.accepting = False
                return web.Response(status=503, text="Pool state unavailable; retry")
            await asyncio.sleep(interval)
        return web.Response(status=503, text="Pool readiness timed out; retry")

    app.router.add_post("/coder/preflight", start)
