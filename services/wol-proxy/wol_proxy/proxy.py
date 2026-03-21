import asyncio
import logging
import subprocess
import time

from aiohttp import ClientSession, ClientTimeout, web

from .config import BackendConfig
from .wol import send_wol_packet

log = logging.getLogger("wol-proxy")


class ProxyBackend:
    def __init__(self, cfg: BackendConfig) -> None:
        self.cfg = cfg
        self._last_activity = time.monotonic()
        self._is_awake = False
        self._wake_lock = asyncio.Lock()
        self._cache: dict[str, str] = dict(cfg.cached_path_defaults)
        self._cached_paths: set[str] = set(cfg.cached_paths)

    # -- Health check -------------------------------------------------------

    async def check_health(self) -> bool:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(self.cfg.target_host, self.cfg.target_port),
                timeout=2,
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (OSError, asyncio.TimeoutError):
            return False

    # -- Wake and wait ------------------------------------------------------

    async def wake_and_wait(self) -> None:
        async with self._wake_lock:
            if await self.check_health():
                self.mark_awake()
                return

            await send_wol_packet(
                self.cfg.wol_mac,
                self.cfg.name,
                self.cfg.wol_host,
                self.cfg.ssh_user,
                self.cfg.ssh_key_path,
                self.cfg.wol_broadcast,
            )
            deadline = time.monotonic() + self.cfg.wake_timeout_seconds
            log.info(
                "[%s] Waiting for backend to wake (timeout: %ds)",
                self.cfg.name,
                self.cfg.wake_timeout_seconds,
            )

            while time.monotonic() < deadline:
                await asyncio.sleep(2)
                if await self.check_health():
                    log.info("[%s] Backend is now awake", self.cfg.name)
                    self.mark_awake()
                    await asyncio.sleep(2)  # let it fully initialise
                    return

            raise RuntimeError(
                f"Backend did not wake within {self.cfg.wake_timeout_seconds}s"
            )

    def mark_awake(self) -> None:
        if not self._is_awake:
            log.info("[%s] Backend detected as awake", self.cfg.name)
            self._is_awake = True

    # -- Suspend ------------------------------------------------------------

    async def suspend(self) -> None:
        if not self.cfg.ssh_user:
            log.info("[%s] No SSH user configured, skipping suspend", self.cfg.name)
            return

        log.info("[%s] Suspending backend via SSH", self.cfg.name)
        proc = await asyncio.create_subprocess_exec(
            "ssh",
            "-i",
            self.cfg.ssh_key_path,
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "ConnectTimeout=10",
            f"{self.cfg.ssh_user}@{self.cfg.target_host}",
            "sudo",
            "systemctl",
            "suspend",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        output, _ = await proc.communicate()
        # SSH connection drop is expected when the machine suspends
        if proc.returncode in (0, 255):
            log.info("[%s] Backend suspended", self.cfg.name)
            self._is_awake = False
        else:
            log.error(
                "[%s] Suspend failed (rc=%s): %s",
                self.cfg.name,
                proc.returncode,
                output.decode(errors="replace"),
            )

    # -- Activity tracking --------------------------------------------------

    def touch(self) -> None:
        self._last_activity = time.monotonic()

    @property
    def idle_seconds(self) -> float:
        return time.monotonic() - self._last_activity

    # -- Idle watcher -------------------------------------------------------

    async def idle_watcher(self) -> None:
        if self.cfg.idle_timeout_minutes <= 0:
            log.info("[%s] Idle timeout disabled", self.cfg.name)
            return

        timeout = self.cfg.idle_timeout_minutes * 60
        while True:
            await asyncio.sleep(60)
            if not self._is_awake:
                continue

            log.info(
                "[%s] Idle for %.0fm / %.0fm",
                self.cfg.name,
                self.idle_seconds / 60,
                self.cfg.idle_timeout_minutes,
            )
            if self.idle_seconds >= timeout:
                log.info(
                    "[%s] Backend idle for %.0fm, suspending",
                    self.cfg.name,
                    self.idle_seconds / 60,
                )
                await self.suspend()

    # -- HTTP reverse proxy -------------------------------------------------

    async def handle_request(self, request: web.Request) -> web.StreamResponse:
        path = request.path
        is_cacheable = path in self._cached_paths and request.method == "GET"

        if not await self.check_health():
            # Serve cached response for lightweight polling endpoints
            if is_cacheable:
                if path in self._cache:
                    log.info(
                        "[%s] Serving cached %s (backend asleep)", self.cfg.name, path
                    )
                    return web.Response(
                        text=self._cache[path],
                        content_type="application/json",
                    )

            # Non-cacheable request: wake the backend
            self.touch()
            log.info("[%s] Backend not responding, attempting wake", self.cfg.name)
            try:
                await self.wake_and_wait()
            except RuntimeError as exc:
                return web.Response(status=503, text=f"Failed to wake GPU node: {exc}")
        else:
            self.mark_awake()
            self.touch()

        target = (
            f"http://{self.cfg.target_host}:{self.cfg.target_port}{request.path_qs}"
        )

        timeout = ClientTimeout(total=None, sock_read=300)
        async with ClientSession(timeout=timeout) as session:
            async with session.request(
                method=request.method,
                url=target,
                headers={
                    k: v
                    for k, v in request.headers.items()
                    if k.lower() not in ("host", "transfer-encoding")
                },
                data=await request.read(),
            ) as upstream:
                # For cacheable endpoints, read full body and cache it
                if is_cacheable and upstream.status == 200:
                    body = await upstream.read()
                    self._cache[path] = body.decode(errors="replace")
                    return web.Response(
                        status=upstream.status,
                        body=body,
                        content_type="application/json",
                    )

                resp = web.StreamResponse(
                    status=upstream.status,
                    headers={
                        k: v
                        for k, v in upstream.headers.items()
                        if k.lower()
                        not in (
                            "transfer-encoding",
                            "content-encoding",
                            "content-length",
                        )
                    },
                )

                content_length = upstream.headers.get("content-length")
                if content_length:
                    resp.content_length = int(content_length)

                await resp.prepare(request)
                try:
                    async for chunk in upstream.content.iter_any():
                        self.touch()
                        await resp.write(chunk)
                    await resp.write_eof()
                except (ConnectionResetError, ConnectionError):
                    log.info("[%s] Client disconnected during streaming", self.cfg.name)
                return resp
