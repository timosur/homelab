import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from test_interlock import FakeKubernetes
from wol_proxy.coordinator import SleepCoordinator, leases_advanced
from wol_proxy.interlock import Interlock
from wol_proxy.pool import PoolMonitor, PoolPowerClient, wake_all
from wol_proxy.power_client import PowerClientError


def monitor(node):
    return SimpleNamespace(
        node=node, namespace="sandboxes", instance="factory",
        node_ready=AsyncMock(return_value=True), node_lease_revision=AsyncMock(return_value="lease-1"),
        verify_admission=AsyncMock(), any_workload=AsyncMock(return_value=False),
        has_gated_pods=AsyncMock(return_value=False), release_gates=AsyncMock(),
    )


class PoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.first = monitor("desktop")
        self.second = monitor("gpu")
        self.monitor = PoolMonitor((self.first, self.second))
        self.clients = [SimpleNamespace(call=AsyncMock()), SimpleNamespace(call=AsyncMock())]
        self.power = PoolPowerClient(self.clients)
        self.kubernetes = FakeKubernetes()
        self.interlock = Interlock(self.kubernetes)
        self.proxy = SimpleNamespace(
            _wake_requested=False, _active_requests=0, _activity_lock=asyncio.Lock(),
            idle_seconds=0, cfg=SimpleNamespace(idle_timeout_minutes=5), touch=lambda: None,
        )
        self.wake = AsyncMock()
        self.coordinator = SleepCoordinator(self.proxy, self.monitor, self.interlock, self.power, self.wake)

    async def ready(self):
        await self.coordinator.tick()
        self.first.node_lease_revision.return_value = "lease-2"
        self.second.node_lease_revision.return_value = "lease-2"
        await self.coordinator.tick()
        self.assertTrue(self.coordinator.accepting)

    async def test_both_hosts_require_fence_and_fresh_leases_before_release(self):
        self.first.any_workload.return_value = True
        self.first.has_gated_pods.return_value = True
        await self.coordinator.tick()
        for client in self.clients:
            self.assertEqual(client.call.await_args.args[0], "fence")
        self.first.node_lease_revision.return_value = "lease-2"
        await self.coordinator.tick()
        self.assertFalse(self.coordinator.accepting)
        self.first.release_gates.assert_not_awaited()
        self.second.node_lease_revision.return_value = "lease-2"
        await self.coordinator.tick()
        self.assertTrue(self.coordinator.accepting)
        self.first.release_gates.assert_awaited_once()

    async def test_unready_worker_keeps_gate_closed_and_wakes_pool(self):
        self.second.node_ready.return_value = False
        self.first.any_workload.return_value = True
        await self.coordinator.tick()
        self.assertFalse(self.coordinator.accepting)
        self.wake.assert_awaited_once()
        for client in self.clients:
            client.call.assert_not_awaited()
        self.first.release_gates.assert_not_awaited()

    async def test_failed_fence_blocks_gate_release(self):
        self.first.any_workload.return_value = True
        self.clients[1].call.side_effect = PowerClientError("gpu unavailable")
        with self.assertRaises(PowerClientError):
            await self.coordinator.tick()
        self.assertFalse(self.coordinator.accepting)
        self.first.release_gates.assert_not_awaited()

    async def test_idle_sleep_is_fenced_on_both_hosts(self):
        await self.ready()
        self.proxy.idle_seconds = 301
        await self.coordinator.tick()
        for client in self.clients:
            self.assertEqual([call.args[0] for call in client.call.await_args_list], ["fence", "suspend"])
        self.assertEqual(self.interlock.epoch, 2)
        self.assertEqual(self.kubernetes.data["phase"], "suspending")

    async def test_partial_suspend_failure_retires_epoch_and_keeps_gate_closed(self):
        await self.ready()
        self.clients[1].call.side_effect = PowerClientError("unknown suspend outcome")
        self.proxy.idle_seconds = 301
        await self.coordinator.tick()
        self.assertEqual(self.interlock.epoch, 2)
        self.assertFalse(self.coordinator.accepting)
        self.assertEqual(self.kubernetes.data["phase"], "suspending")
        self.first.release_gates.assert_not_awaited()

    async def test_sandbox_or_http_activity_keeps_both_workers_awake(self):
        await self.ready()
        self.proxy.idle_seconds = 301
        self.first.any_workload.return_value = True
        await self.coordinator.tick()
        self.first.any_workload.return_value = False
        self.proxy._active_requests = 1
        await self.coordinator.tick()
        for client in self.clients:
            self.assertEqual(client.call.await_count, 1)

    async def test_wake_attempts_all_hosts_even_if_one_fails(self):
        first = AsyncMock(side_effect=OSError("failed"))
        second = AsyncMock()
        with self.assertRaises(OSError):
            await wake_all((first, second))
        second.assert_awaited_once()

    def test_requires_distinct_workers_and_all_fresh_leases(self):
        with self.assertRaises(ValueError):
            PoolMonitor((self.first, self.first))
        with self.assertRaises(ValueError):
            PoolPowerClient(())
        self.assertFalse(leases_advanced(("a", "b"), ("c", "b")))
        self.assertTrue(leases_advanced(("a", "b"), ("c", "d")))
        with self.assertRaises(ValueError):
            leases_advanced(("a", "b"), ("c",))
