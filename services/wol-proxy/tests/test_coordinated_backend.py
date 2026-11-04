import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp import web

from test_interlock import FakeKubernetes
from wol_proxy.config import BackendConfig, NodeGroupBackend, NodeGroupConfig
from wol_proxy.interlock import Interlock, InterlockError
from wol_proxy.proxy import NodeGroupProxy, ProxyBackend


class CoordinatedBackendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = FakeKubernetes()
        self.interlock = Interlock(self.client)
        await self.interlock.claim()
        self.group = NodeGroupProxy(NodeGroupConfig(
            name="gpu", listen_port=8080, wol_mac="00:11:22:33:44:55",
            backends=[NodeGroupBackend(hostname="app.example", target="backend:80")],
        ))
        self.coordinator = SimpleNamespace(accepting=True, interlock=self.interlock, proxy=self.group)
        self.group._coordinator = self.coordinator
        self.backend = ProxyBackend(BackendConfig(
            name="ollama", listen_port=11434, target_host="gpu", target_port=11434,
            wol_mac="00:11:22:33:44:55", cached_paths=["/api/tags"],
            cached_path_defaults={"/api/tags": '{"models":[]}'},
        ), coordinator=self.coordinator)
        self.request = SimpleNamespace(path="/api/generate", host="app.example", method="POST", headers={})

    async def test_reservation_covers_backend_request_and_blocks_takeover(self):
        started, finish = asyncio.Event(), asyncio.Event()

        async def handler(request):
            started.set()
            await finish.wait()
            return web.Response(text="ok")

        self.backend._handle_request = handler
        task = asyncio.create_task(self.backend.handle_request(self.request))
        await started.wait()
        self.assertEqual(self.client.data["http_active"], "1")
        self.assertEqual(self.group._active_requests, 1)
        with self.assertRaises(InterlockError):
            await Interlock(self.client).claim()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.client.data["http_active"], "0")
        self.assertEqual(self.group._active_requests, 0)

    async def test_backend_and_node_group_share_one_reservation(self):
        starts = [asyncio.Event(), asyncio.Event()]
        finishes = [asyncio.Event(), asyncio.Event()]

        async def handler(request):
            starts[request.index].set()
            await finishes[request.index].wait()
            return web.Response(text="ok")

        self.backend._handle_request = handler
        self.group._handle_request = handler
        backend_request = SimpleNamespace(**vars(self.request), index=0)
        group_request = SimpleNamespace(**vars(self.request), index=1)
        tasks = [asyncio.create_task(self.backend.handle_request(backend_request)),
                 asyncio.create_task(self.group.handle_request(group_request))]
        await asyncio.gather(*(event.wait() for event in starts))
        self.assertEqual(self.client.data["http_active"], "1")
        self.assertEqual(self.group._active_requests, 2)
        finishes[0].set()
        await tasks[0]
        self.assertEqual(self.client.data["http_active"], "1")
        finishes[1].set()
        await tasks[1]
        self.assertEqual(self.client.data["http_active"], "0")

    async def test_unavailable_coordinator_caches_polling_and_requests_wake_for_work(self):
        self.coordinator.accepting = False
        self.backend._handle_request = AsyncMock()
        cached = SimpleNamespace(path="/api/tags", method="GET")
        self.assertEqual((await self.backend.handle_request(cached)).status, 200)
        self.assertFalse(self.group._wake_requested)
        self.assertEqual((await self.backend.handle_request(self.request)).status, 503)
        self.assertTrue(self.group._wake_requested)
        self.backend._handle_request.assert_not_awaited()

    async def test_reservation_failure_stops_request_and_fails_closed(self):
        self.interlock.http_change = AsyncMock(side_effect=OSError("API unavailable"))
        self.backend._handle_request = AsyncMock()
        self.assertEqual((await self.backend.handle_request(self.request)).status, 503)
        self.assertFalse(self.coordinator.accepting)
        self.assertTrue(self.group._wake_requested)
        self.backend._handle_request.assert_not_awaited()

    async def test_release_failure_disables_coordinator(self):
        self.interlock.http_change = AsyncMock(side_effect=[None, OSError("API unavailable")])
        self.backend._handle_request = AsyncMock(return_value=web.Response(text="ok"))
        with self.assertLogs("wol-proxy", level="ERROR"):
            self.assertEqual((await self.backend.handle_request(self.request)).status, 200)
        self.assertFalse(self.coordinator.accepting)
        self.assertEqual(self.group._proxied_requests, 0)

    async def test_coordinated_backend_cannot_use_legacy_suspend(self):
        self.backend.suspend = AsyncMock(wraps=self.backend.suspend)
        await self.backend.idle_watcher()
        self.backend.suspend.assert_not_awaited()
        with self.assertRaises(RuntimeError):
            await self.backend.suspend()
