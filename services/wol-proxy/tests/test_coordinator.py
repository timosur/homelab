import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from aiohttp import web

from test_interlock import FakeKubernetes
from wol_proxy.config import NodeGroupBackend, NodeGroupConfig
from wol_proxy.coordinator import SleepCoordinator
from wol_proxy.interlock import Interlock, InterlockError
from wol_proxy.power_client import PowerClientError
from wol_proxy.power_guard import GuardError, PowerGuard
from wol_proxy.proxy import NodeGroupProxy


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = FakeKubernetes()
        self.interlock = Interlock(self.client)
        self.monitor = SimpleNamespace(
            any_workload=AsyncMock(return_value=False), node_ready=AsyncMock(return_value=True),
            node_lease_revision=AsyncMock(side_effect=["lease-1", "lease-2"]),
            has_gated_pods=AsyncMock(return_value=False), release_gates=AsyncMock(),
            verify_admission=AsyncMock(),
        )
        self.proxy = SimpleNamespace(
            _wake_requested=False, _active_requests=0, _activity_lock=asyncio.Lock(),
            idle_seconds=0, cfg=SimpleNamespace(idle_timeout_minutes=5), touch=lambda: None,
        )
        self.power = SimpleNamespace(call=AsyncMock())
        self.wake = AsyncMock()
        self.coordinator = SleepCoordinator(self.proxy, self.monitor, self.interlock, self.power, self.wake)

    async def ready(self):
        await self.coordinator.tick()
        await self.coordinator.tick()
        self.assertTrue(self.coordinator.accepting)

    async def test_sleep_retires_epoch_before_any_recovery(self):
        await self.ready()
        self.proxy.idle_seconds = 301
        await self.coordinator.tick()
        self.assertFalse(self.coordinator.accepting)
        self.assertEqual(self.client.data["phase"], "suspending")
        self.assertEqual(self.interlock.epoch, 2)
        self.assertEqual(self.power.call.await_args_list[-1].args[0], "suspend")

    async def test_uncertain_ssh_still_retires_suspend_epoch(self):
        await self.ready()
        self.proxy.idle_seconds = 301
        self.power.call.side_effect = PowerClientError("timeout")
        await self.coordinator.tick()
        self.assertEqual(self.interlock.epoch, 2)
        self.assertEqual(self.client.data["phase"], "suspending")

    async def test_failed_epoch_retirement_never_refences_old_epoch(self):
        await self.ready()
        self.proxy.idle_seconds = 301
        original_retire = self.interlock.retire_suspend_epoch
        attempts = 0

        async def retry_retire():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("API unavailable")
            await original_retire()

        self.interlock.retire_suspend_epoch = retry_retire
        with self.assertRaises(OSError):
            await self.coordinator.tick()
        self.assertEqual(self.interlock.epoch, 1)
        self.monitor.node_lease_revision.side_effect = ["lease-3"]
        await self.coordinator.tick()
        self.assertEqual(self.interlock.epoch, 2)
        self.assertEqual(self.power.call.await_args_list[-1].args[:2], ("fence", 2))

    async def test_delayed_suspend_is_rejected_after_new_host_fence(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = PowerGuard(Path(directory) / "guard", runner=Mock(return_value=subprocess.CompletedProcess([], 0)))
            guard.initialize()
            delayed = []

            async def power_call(operation, epoch, owner, anchor):
                if operation == "fence":
                    return guard.fence(epoch, owner, anchor)
                delayed.append((epoch, owner, anchor))
                raise PowerClientError("response lost while request is delayed")

            self.power.call.side_effect = power_call
            self.monitor.node_lease_revision.side_effect = ["lease-1", "lease-2", "lease-3"]
            await self.ready()
            self.proxy.idle_seconds = 301
            await self.coordinator.tick()
            self.assertEqual(self.interlock.epoch, 2)
            await self.coordinator.tick()  # Host accepts the new epoch before the delayed request.
            with self.assertRaises(GuardError):
                guard.suspend(*delayed[0])

    async def test_active_sandbox_prevents_sleep(self):
        await self.ready()
        self.proxy.idle_seconds = 301
        self.monitor.any_workload.return_value = True
        await self.coordinator.tick()
        self.assertEqual(self.client.data["phase"], "idle")
        self.assertEqual(self.power.call.await_count, 1)

    async def test_uncertain_gate_patch_blocks_takeover(self):
        await self.ready()
        self.monitor.any_workload.return_value = True
        self.monitor.has_gated_pods.return_value = True
        self.monitor.release_gates.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            await self.coordinator.tick()
        self.assertEqual(self.client.data["phase"], "releasing")
        with self.assertRaises(InterlockError):
            await Interlock(self.client).claim()

    async def test_takeover_is_blocked_while_gate_patch_is_in_flight(self):
        await self.ready()
        self.monitor.any_workload.return_value = True
        self.monitor.has_gated_pods.return_value = True
        started, finish = asyncio.Event(), asyncio.Event()

        async def pending_patch(*args):
            started.set()
            await finish.wait()

        self.monitor.release_gates.side_effect = pending_patch
        tick = asyncio.create_task(self.coordinator.tick())
        await started.wait()
        with self.assertRaises(InterlockError):
            await Interlock(self.client).claim()
        finish.set()
        await tick
        self.assertEqual(self.client.data["phase"], "idle")

    async def test_new_workload_after_suspend_transition_cancels_sleep(self):
        await self.ready()
        self.proxy.idle_seconds = 301
        self.monitor.any_workload.side_effect = [False, True]
        await self.coordinator.tick()
        self.assertEqual(self.client.data["phase"], "idle")
        self.assertEqual(self.power.call.await_count, 1)

    async def test_recent_http_activity_rechecks_idle_after_lock(self):
        await self.ready()
        self.proxy.idle_seconds = 301
        checked = asyncio.Event()

        async def no_workload():
            checked.set()
            return False

        self.monitor.any_workload.side_effect = no_workload
        await self.proxy._activity_lock.acquire()
        tick = asyncio.create_task(self.coordinator.tick())
        await asyncio.wait_for(checked.wait(), timeout=1)
        self.proxy.idle_seconds = 0
        self.proxy._activity_lock.release()
        await tick
        self.assertEqual(self.client.data["phase"], "idle")
        self.assertEqual(self.power.call.await_count, 1)

    async def test_missing_admission_fails_closed(self):
        await self.ready()
        self.monitor.verify_admission.side_effect = ValueError("missing webhook")
        self.proxy.idle_seconds = 301
        with self.assertRaises(ValueError):
            await self.coordinator.tick()
        self.assertFalse(self.coordinator.accepting)
        self.assertEqual(self.power.call.await_count, 1)

    async def test_admission_disappearing_after_suspend_reservation_stays_closed(self):
        await self.ready()
        self.proxy.idle_seconds = 301
        self.monitor.verify_admission.side_effect = [None, ValueError("webhook removed")]
        with self.assertRaises(ValueError):
            await self.coordinator.tick()
        self.assertEqual(self.client.data["phase"], "suspending")
        self.assertEqual(self.power.call.await_count, 1)
        self.assertFalse(self.coordinator.accepting)

    async def test_pending_work_waits_for_host_fence_and_fresh_node_lease(self):
        self.monitor.node_lease_revision.side_effect = ["lease-1", "lease-2", "lease-3", "lease-3", "lease-4"]
        await self.ready()
        self.proxy.idle_seconds = 301
        await self.coordinator.tick()
        self.monitor.any_workload.return_value = True
        self.monitor.has_gated_pods.return_value = True
        self.power.call.side_effect = PowerClientError("host still sleeping")
        with self.assertRaises(PowerClientError):
            await self.coordinator.tick()
        self.monitor.release_gates.assert_not_awaited()
        self.wake.assert_awaited_once()
        self.power.call.side_effect = None
        await self.coordinator.tick()  # Host accepts the newer fence.
        await self.coordinator.tick()  # Same lease: still not safe.
        self.monitor.release_gates.assert_not_awaited()
        await self.coordinator.tick()  # New kubelet lease permits release.
        self.monitor.release_gates.assert_awaited_once()
        self.assertEqual(self.client.data["phase"], "idle")

    async def test_active_http_prevents_suspend_and_owner_takeover(self):
        await self.ready()
        await self.interlock.http_change(1)
        self.proxy._active_requests = 1
        self.proxy.idle_seconds = 301
        await self.coordinator.tick()
        self.assertEqual(self.client.data["phase"], "idle")
        with self.assertRaises(InterlockError):
            await Interlock(self.client).claim()

    async def test_http_request_is_durable_until_response_finishes(self):
        await self.interlock.claim()
        config = NodeGroupConfig(name="desktop", listen_port=8080, wol_mac="00:11:22:33:44:55",
                                 backends=[NodeGroupBackend(hostname="app.example", target="backend:80")])
        proxy = NodeGroupProxy(config)
        proxy._coordinator = SimpleNamespace(accepting=True, interlock=self.interlock)
        started, finish = asyncio.Event(), asyncio.Event()

        async def handler(request):
            started.set()
            await finish.wait()
            return web.Response(text="ok")

        proxy._handle_request = handler
        request = SimpleNamespace(path="/", host="app.example", method="GET", headers={})
        response = asyncio.create_task(proxy.handle_request(request))
        await started.wait()
        self.assertEqual(self.client.data["http_active"], "1")
        with self.assertRaises(InterlockError):
            await Interlock(self.client).claim()
        finish.set()
        self.assertEqual((await response).status, 200)
        self.assertEqual(self.client.data["http_active"], "0")

    async def test_status_does_not_claim_ready_before_interlock_is_ready(self):
        config = NodeGroupConfig(name="desktop", listen_port=8080, wol_mac="00:11:22:33:44:55")
        proxy = NodeGroupProxy(config)
        proxy._state = "ready"
        proxy._coordinator = SimpleNamespace(accepting=False)
        request = SimpleNamespace(path="/wol-proxy/status")
        response = await proxy.handle_request(request)
        self.assertIn('"state": "sleeping"', response.text)
