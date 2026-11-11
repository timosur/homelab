import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from wol_proxy.coder_preflight import register
from wol_proxy.coordinator import SleepCoordinator
from wol_proxy.interlock import Interlock
from test_interlock import FakeKubernetes

WORKSPACE = "11111111-1111-1111-1111-111111111111"


class PreflightTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        path = Path(self.directory.name) / "token"
        self.token = "x" * 40
        path.write_text(self.token)
        self.coordinator = SimpleNamespace(accepting=True, interlock=SimpleNamespace(coder_reservation=AsyncMock()))
        self.proxy = SimpleNamespace(_coordinator=self.coordinator, _activity_lock=asyncio.Lock(),
                                     _sandbox_monitor=SimpleNamespace(nodes_schedulable=AsyncMock(return_value=True)),
                                     _wake_requested=False, touch=lambda: None)
        app = web.Application()
        register(app, self.proxy, str(path), timeout=0.03, interval=0.005)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def post(self, token=None, body=None):
        return await self.client.post("/coder/preflight", headers={"Authorization": f"Bearer {token or self.token}"},
                                      json=body or {"workspace_id": WORKSPACE})

    async def test_authorized_preflight_persists_reservation(self):
        result = await self.post()
        self.assertEqual(result.status, 200)
        self.coordinator.interlock.coder_reservation.assert_awaited_once_with(WORKSPACE, reserve=True)

    async def test_denied_caller_does_not_wake_or_reserve(self):
        result = await self.post(token="wrong")
        self.assertEqual(result.status, 403)
        self.assertFalse(self.proxy._wake_requested)
        self.coordinator.interlock.coder_reservation.assert_not_awaited()

    async def test_timeout_then_retry(self):
        self.coordinator.accepting = False
        result = await self.post()
        self.assertEqual(result.status, 503)
        self.assertTrue(self.proxy._wake_requested)
        self.coordinator.interlock.coder_reservation.assert_not_awaited()
        self.coordinator.accepting = True
        result = await self.post()
        self.assertEqual(result.status, 200)
        self.coordinator.interlock.coder_reservation.assert_awaited_once()

    async def test_unknown_state_fails_without_reserving(self):
        self.proxy._sandbox_monitor.nodes_schedulable.side_effect = OSError("unknown")
        result = await self.post()
        self.assertEqual(result.status, 503)
        self.assertFalse(self.coordinator.accepting)
        self.coordinator.interlock.coder_reservation.assert_not_awaited()

    async def test_sleeping_pool_is_woken_fenced_and_reserved(self):
        kubernetes = FakeKubernetes()
        interlock = Interlock(kubernetes)
        monitor = SimpleNamespace(
            verify_admission=AsyncMock(), any_workload=AsyncMock(return_value=False),
            node_ready=AsyncMock(return_value=False), node_lease_revision=AsyncMock(side_effect=["lease-1", "lease-2"]),
            has_gated_pods=AsyncMock(return_value=False), nodes_schedulable=AsyncMock(return_value=True),
        )
        proxy = SimpleNamespace(_activity_lock=asyncio.Lock(), _sandbox_monitor=monitor, _wake_requested=False,
                                _active_requests=0, idle_seconds=0, cfg=SimpleNamespace(idle_timeout_minutes=5),
                                touch=lambda: None)
        async def wake():
            monitor.node_ready.return_value = True

        wake_pool = AsyncMock(side_effect=wake)
        power = SimpleNamespace(call=AsyncMock())
        proxy._coordinator = SleepCoordinator(proxy, monitor, interlock, power, wake_pool)
        app = web.Application()
        token_path = Path(self.directory.name) / "token"
        register(app, proxy, str(token_path), timeout=1, interval=0.001)
        async with TestClient(TestServer(app)) as client:
            pending = asyncio.create_task(client.post("/coder/preflight", headers={"Authorization": f"Bearer {self.token}"},
                                                      json={"workspace_id": WORKSPACE}))
            while not proxy._wake_requested:
                await asyncio.sleep(0)
            await proxy._coordinator.tick()
            await proxy._coordinator.tick()
            await proxy._coordinator.tick()
            result = await pending
        self.assertEqual(result.status, 200)
        wake_pool.assert_awaited_once()
        self.assertEqual(power.call.await_args.args[0], "fence")
        self.assertEqual((await interlock.read())["coder_starts"], [WORKSPACE])
