import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from test_interlock import FakeKubernetes
from wol_proxy.coordinator import SleepCoordinator, leases_advanced
from wol_proxy.interlock import Interlock, InterlockError
from wol_proxy.pool import PoolMonitor, PoolPowerClient, wake_all
from wol_proxy.power_client import PowerClientError
from wol_proxy.power_guard import GuardError, PowerGuard


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

    async def test_idle_pool_suspends_ready_worker_when_peer_is_unready(self):
        for unavailable in (0, 1):
            with self.subTest(unavailable=unavailable):
                await self.asyncSetUp()
                monitors = (self.first, self.second)
                monitors[unavailable].node_ready.return_value = False
                self.proxy.idle_seconds = 301
                await self.coordinator.tick()
                self.clients[unavailable].call.assert_not_awaited()
                self.assertEqual(
                    [call.args[0] for call in self.clients[1 - unavailable].call.await_args_list],
                    ["fence", "suspend"],
                )
                self.assertFalse(self.coordinator.accepting)
                self.assertEqual(self.kubernetes.data["phase"], "suspending")
                self.assertEqual(self.interlock.epoch, 2)

    async def test_idle_pool_continues_after_peer_power_failure(self):
        for failed in (0, 1):
            for operation in ("fence", "suspend"):
                with self.subTest(failed=failed, operation=operation):
                    await self.asyncSetUp()

                    async def fail_selected(op, *_):
                        if op == operation:
                            raise PowerClientError("host unavailable")

                    self.clients[failed].call.side_effect = fail_selected
                    self.proxy.idle_seconds = 301
                    await self.coordinator.tick()
                    self.assertEqual(
                        [call.args[0] for call in self.clients[1 - failed].call.await_args_list],
                        ["fence", "suspend"],
                    )
                    self.assertEqual(
                        [call.args[0] for call in self.clients[failed].call.await_args_list],
                        ["fence"] if operation == "fence" else ["fence", "suspend"],
                    )
                    self.assertFalse(self.coordinator.accepting)
                    self.assertEqual(self.kubernetes.data["phase"], "suspending")
                    self.assertEqual(self.interlock.epoch, 2)

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
            self.assertEqual([call.args[0] for call in client.call.await_args_list], ["fence", "fence", "suspend"])
        self.assertEqual(self.interlock.epoch, 2)
        self.assertEqual(self.kubernetes.data["phase"], "suspending")

    async def test_partial_suspend_failure_retires_epoch_and_keeps_gate_closed(self):
        await self.ready()

        async def fail_suspend(operation, *_):
            if operation == "suspend":
                raise PowerClientError("unknown suspend outcome")

        self.clients[1].call.side_effect = fail_suspend
        self.proxy.idle_seconds = 301
        await self.coordinator.tick()
        self.assertEqual(self.interlock.epoch, 2)
        self.assertFalse(self.coordinator.accepting)
        self.assertEqual(self.kubernetes.data["phase"], "suspending")
        self.first.release_gates.assert_not_awaited()

    async def test_no_ready_worker_closes_admission_without_power_attempts(self):
        await self.ready()
        for monitor in (self.first, self.second):
            monitor.node_ready.return_value = False
        self.proxy.idle_seconds = 301
        await self.coordinator.tick()
        self.assertFalse(self.coordinator.accepting)
        self.assertEqual(self.kubernetes.data["phase"], "idle")
        for client in self.clients:
            self.assertEqual([call.args[0] for call in client.call.await_args_list], ["fence"])

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

    async def test_degraded_sleep_respects_protected_activity(self):
        for kind in ("workload", "reservation", "http", "durable-http", "wake", "recent"):
            with self.subTest(kind=kind):
                await self.asyncSetUp()
                self.first.node_ready.return_value = False
                self.proxy.idle_seconds = 301
                if kind == "workload":
                    self.first.any_workload.return_value = True
                elif kind == "reservation":
                    await self.interlock.claim()
                    await self.interlock.coder_reservation("11111111-1111-1111-1111-111111111111", reserve=True)
                elif kind == "http":
                    self.proxy._active_requests = 1
                elif kind == "durable-http":
                    await self.interlock.claim()
                    await self.interlock.http_change(1)
                elif kind == "wake":
                    self.proxy._wake_requested = True
                else:
                    self.proxy.idle_seconds = 299
                await self.coordinator.tick()
                for client in self.clients:
                    client.call.assert_not_awaited()
                self.assertFalse(self.coordinator.accepting)
                self.assertEqual(self.kubernetes.data["phase"], "idle")

    async def test_new_workload_after_sleep_transition_cancels_degraded_sleep(self):
        self.first.node_ready.return_value = False
        self.proxy.idle_seconds = 301
        self.first.any_workload.side_effect = [False, True]
        await self.coordinator.tick()
        self.assertEqual(self.kubernetes.data["phase"], "idle")
        self.assertFalse(self.coordinator.accepting)
        for client in self.clients:
            client.call.assert_not_awaited()

    async def test_degraded_sleep_fails_closed_if_admission_is_unavailable(self):
        self.first.node_ready.return_value = False
        self.proxy.idle_seconds = 301
        self.first.verify_admission.side_effect = [None, ValueError("webhook removed")]
        with self.assertRaises(ValueError):
            await self.coordinator.tick()
        self.assertEqual(self.kubernetes.data["phase"], "suspending")
        self.assertFalse(self.coordinator.accepting)
        for client in self.clients:
            client.call.assert_not_awaited()

    async def test_degraded_sleep_does_not_repeat_before_retry_interval(self):
        now = [100.0]
        self.coordinator.clock = lambda: now[0]
        self.first.node_ready.return_value = False
        self.proxy.idle_seconds = 301
        await self.coordinator.tick()
        self.assertEqual(self.clients[1].call.await_count, 2)
        await self.coordinator.tick()
        self.assertEqual(self.clients[1].call.await_count, 2)
        now[0] += 60
        await self.coordinator.tick()
        self.assertEqual(self.clients[1].call.await_count, 4)

    async def test_restarted_coordinator_fences_delayed_degraded_suspend(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = PowerGuard(Path(directory) / "guard", runner=Mock(return_value=subprocess.CompletedProcess([], 0)))
            guard.initialize()
            old = await self.interlock.claim()
            guard.fence(old["epoch"], old["owner"], old["anchor"])
            await self.interlock.transition("idle", "suspending")
            delayed = (old["epoch"], old["owner"], old["anchor"])

            async def guard_call(operation, epoch, owner, anchor):
                try:
                    return getattr(guard, operation)(epoch, owner, anchor)
                except GuardError as exc:
                    raise PowerClientError("guard rejected operation") from exc

            self.clients[1].call.side_effect = guard_call
            self.first.node_ready.return_value = False
            self.proxy.idle_seconds = 301
            restarted = Interlock(self.kubernetes)
            self.coordinator = SleepCoordinator(self.proxy, self.monitor, restarted, self.power, self.wake)
            await self.coordinator.tick()
            self.assertEqual(restarted.epoch, 3)
            self.assertEqual(self.kubernetes.data["phase"], "suspending")
            with self.assertRaises(GuardError):
                guard.suspend(*delayed)
            self.assertFalse(self.coordinator.accepting)

    async def test_request_during_partial_suspend_cannot_begin_protected_activity(self):
        started, finish = asyncio.Event(), asyncio.Event()

        async def pending_suspend(operation, *_):
            if operation == "suspend":
                started.set()
                await finish.wait()

        self.clients[1].call.side_effect = pending_suspend
        self.first.node_ready.return_value = False
        self.proxy.idle_seconds = 301
        tick = asyncio.create_task(self.coordinator.tick())
        await asyncio.wait_for(started.wait(), timeout=1)
        self.assertFalse(self.coordinator.accepting)
        self.assertEqual(self.kubernetes.data["phase"], "suspending")
        with self.assertRaises(InterlockError):
            await self.interlock.coder_reservation("11111111-1111-1111-1111-111111111111", reserve=True)
        with self.assertRaises(InterlockError):
            await self.interlock.http_change(1)
        self.proxy._wake_requested = True
        finish.set()
        await tick
        await self.coordinator.tick()
        self.wake.assert_awaited_once()
        self.first.release_gates.assert_not_awaited()

    async def test_recovery_requires_all_fences_and_fresh_leases_after_partial_sleep(self):
        self.first.node_ready.return_value = False
        self.proxy.idle_seconds = 301
        await self.coordinator.tick()
        self.first.node_ready.return_value = True
        self.first.any_workload.return_value = True
        self.first.has_gated_pods.return_value = True
        self.clients[1].call.side_effect = PowerClientError("suspend remains unresolved")
        with self.assertRaises(PowerClientError):
            await self.coordinator.tick()
        self.first.release_gates.assert_not_awaited()
        self.assertEqual(self.kubernetes.data["phase"], "suspending")
        self.clients[1].call.side_effect = None
        await self.coordinator.tick()
        await self.coordinator.tick()
        self.first.release_gates.assert_not_awaited()
        self.first.node_lease_revision.return_value = "lease-2"
        await self.coordinator.tick()
        self.first.release_gates.assert_not_awaited()
        self.second.node_lease_revision.return_value = "lease-2"
        await self.coordinator.tick()
        self.first.release_gates.assert_awaited_once()
        self.assertEqual(self.kubernetes.data["phase"], "idle")

    async def test_retirement_failure_is_retried_before_recovery(self):
        self.first.node_ready.return_value = False
        self.proxy.idle_seconds = 301
        original_retire = self.interlock.retire_suspend_epoch
        self.interlock.retire_suspend_epoch = AsyncMock(side_effect=[OSError("API unavailable"), None])
        with self.assertRaises(OSError):
            await self.coordinator.tick()
        self.assertTrue(self.coordinator.needs_retire)
        self.assertEqual(self.kubernetes.data["phase"], "suspending")
        self.interlock.retire_suspend_epoch = original_retire
        self.proxy._wake_requested = True
        await self.coordinator.tick()
        self.assertEqual(self.interlock.epoch, 2)
        self.assertFalse(self.coordinator.accepting)
        self.wake.assert_awaited_once()

    async def test_takeover_fences_suspend_still_in_flight_from_old_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = PowerGuard(Path(directory) / "guard", runner=Mock(return_value=subprocess.CompletedProcess([], 0)))
            guard.initialize()
            started, finish = asyncio.Event(), asyncio.Event()

            async def guard_call(operation, epoch, owner, anchor):
                if operation == "suspend" and epoch == 1:
                    started.set()
                    await finish.wait()
                try:
                    return getattr(guard, operation)(epoch, owner, anchor)
                except GuardError as exc:
                    raise PowerClientError("guard rejected operation") from exc

            self.clients[1].call.side_effect = guard_call
            self.first.node_ready.return_value = False
            self.proxy.idle_seconds = 301
            old_tick = asyncio.create_task(self.coordinator.tick())
            await asyncio.wait_for(started.wait(), timeout=1)

            new_proxy = SimpleNamespace(
                _wake_requested=False, _active_requests=0, _activity_lock=asyncio.Lock(),
                idle_seconds=301, cfg=SimpleNamespace(idle_timeout_minutes=5), touch=lambda: None,
            )
            new_interlock = Interlock(self.kubernetes)
            replacement = SleepCoordinator(new_proxy, self.monitor, new_interlock, self.power, self.wake)
            await replacement.tick()
            self.assertEqual(new_interlock.epoch, 3)
            self.assertFalse(replacement.accepting)
            finish.set()
            with self.assertRaises(InterlockError):
                await old_tick
            self.assertEqual(self.kubernetes.data["phase"], "suspending")
            self.assertEqual(guard.status()["epoch"], 2)

    async def test_invalid_worker_readiness_never_authorizes_sleep(self):
        self.first.node_ready.return_value = None
        self.proxy.idle_seconds = 301
        with self.assertRaises(ValueError):
            await self.coordinator.tick()
        self.assertEqual(self.kubernetes.data["phase"], "idle")
        self.assertFalse(self.coordinator.accepting)
        for client in self.clients:
            client.call.assert_not_awaited()

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
