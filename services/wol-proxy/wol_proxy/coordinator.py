"""Serialize sandbox gate release with fenced desktop sleep."""

import asyncio
import time

from aiohttp import ClientResponseError

from .interlock import InterlockError
from .power_client import PowerClientError
from .workspace import WorkspaceBusy
from .coder_admission import GATE as CODER_GATE, workspace_id
from .admission import gates


def leases_advanced(previous, current):
    """Require a fresh lease from every worker in the fenced pool."""
    if isinstance(previous, str) and isinstance(current, str):
        return bool(previous and current and previous != current)
    if (
        not isinstance(previous, tuple) or not isinstance(current, tuple)
        or not previous or len(previous) != len(current)
        or not all(isinstance(value, str) and value for value in previous + current)
    ):
        raise ValueError("node lease revisions are unavailable or inconsistent")
    return all(old != new for old, new in zip(previous, current))


class SleepCoordinator:
    def __init__(self, proxy, monitor, interlock, power, wake, *, clock=time.monotonic):
        self.proxy, self.monitor, self.interlock, self.power, self.wake = proxy, monitor, interlock, power, wake
        self.clock = clock
        self.accepting = False
        self.fenced_epoch = None
        self.lease_revision = None
        self.last_wake = float("-inf")
        self.needs_retire = False

    async def _wake_if_needed(self, demand):
        if demand and self.clock() - self.last_wake >= 15:
            self.last_wake = self.clock()
            await self.wake()

    async def tick(self):
        try:
            await self._tick()
        except BaseException:
            # Unknown API/SSH outcomes never authorize pod release or suspension.
            self.accepting = False
            self.fenced_epoch = None
            self.lease_revision = None
            raise

    async def _tick(self):
        await self.monitor.verify_admission()
        state = await self.interlock.claim()
        if self.needs_retire:
            # The previous SSH request may still arrive at the desktop. Never
            # fence or reopen gates with the epoch that authorized that request.
            await self.interlock.retire_suspend_epoch()
            self.needs_retire = False
            state["epoch"] = self.interlock.epoch
        active = await self.monitor.any_workload() or bool(state["coder_starts"])
        demand = active or self.proxy._wake_requested or self.proxy._active_requests > 0
        if active:
            self.proxy.touch()

        if not await self.monitor.node_ready():
            self.accepting = False
            self.fenced_epoch = None
            self.lease_revision = None
            await self._wake_if_needed(demand)
            return

        if self.fenced_epoch != self.interlock.epoch:
            self.accepting = False
            try:
                await self.power.call("fence", self.interlock.epoch, self.interlock.owner, self.interlock.anchor)
            except PowerClientError:
                await self._wake_if_needed(demand)
                raise
            self.fenced_epoch = self.interlock.epoch
            self.lease_revision = await self.monitor.node_lease_revision()
            return

        if not self.accepting:
            revision = await self.monitor.node_lease_revision()
            if not leases_advanced(self.lease_revision, revision):
                return
            if not await self.monitor.node_ready():
                self.fenced_epoch = None
                self.lease_revision = None
                return
            if state["phase"] == "suspending":
                await self.interlock.transition("suspending", "idle")
                state["phase"] = "idle"
            self.accepting = True
            self.proxy._wake_requested = False
            self.proxy.touch()

        if state["phase"] != "idle":
            raise InterlockError("interlock is not idle")

        if active and await self.monitor.has_gated_pods():
            async with self.proxy._activity_lock:
                await self.interlock.transition("idle", "releasing")
                try:
                    await self.monitor.release_gates(self.interlock.epoch, self.interlock.owner, self.interlock.anchor)
                except WorkspaceBusy:
                    # No PATCH was attempted for the busy workspace; retry on a later tick.
                    await self.interlock.transition("releasing", "idle")
                    return
                except ClientResponseError as exc:
                    # A definite rejection leaves no PATCH in flight. Uncertain outcomes
                    # must remain persisted until an operator fences the old request.
                    if 400 <= exc.status < 500 and exc.status not in (408, 429):
                        await self.interlock.transition("releasing", "idle")
                    raise
                await self.interlock.transition("releasing", "idle")
                if getattr(self.proxy.cfg, "coder_workspace_namespace", ""):
                    # A later create is gated too, so observation of this ungated
                    # pod safely transfers protection from the reservation to the pod.
                    for pod in await self.monitor.coder_pods():
                        if not any(gate.get("name") == CODER_GATE for gate in gates(pod)):
                            await self.interlock.coder_reservation(workspace_id(pod), reserve=False)
            return

        if active or self.proxy.idle_seconds < self.proxy.cfg.idle_timeout_minutes * 60:
            return

        async with self.proxy._activity_lock:
            if (
                self.proxy._active_requests or self.proxy._wake_requested
                or self.proxy.idle_seconds < self.proxy.cfg.idle_timeout_minutes * 60
            ):
                return
            await self.interlock.transition("idle", "suspending")
            self.accepting = False
            # Once the phase is published no controller may release another gate.
            # An ungated, already-created pod or a new proxied request prevents sleep.
            await self.monitor.verify_admission()
            if await self.monitor.any_workload() or self.proxy._active_requests or self.proxy._wake_requested:
                await self.interlock.transition("suspending", "idle")
                self.accepting = True
                return

        self.fenced_epoch = None
        self.lease_revision = None
        self.needs_retire = True
        try:
            try:
                await self.power.call("suspend", self.interlock.epoch, self.interlock.owner, self.interlock.anchor)
            except PowerClientError:
                # An SSH error does not prove the host failed to enqueue the request.
                pass
        finally:
            # Retire even on unexpected SSH failures: a delayed request must not
            # become valid after we release gates. Restart also advances the epoch.
            await self.interlock.retire_suspend_epoch()
            self.needs_retire = False
