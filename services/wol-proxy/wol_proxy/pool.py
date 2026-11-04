"""Conservative shared fencing for multiple sandbox workers.

Every worker must be ready and fenced before the common gate can open. Any
workload or proxied activity keeps the entire pool awake.
"""

import asyncio


async def wake_all(wake_callbacks):
    callbacks = tuple(wake_callbacks)
    if not callbacks:
        raise ValueError("sandbox worker pool cannot be empty")
    results = await asyncio.gather(*(wake() for wake in callbacks), return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result


class PoolMonitor:
    def __init__(self, monitors):
        self.monitors = tuple(monitors)
        if not self.monitors:
            raise ValueError("sandbox worker pool cannot be empty")
        first = self.monitors[0]
        if (
            len({monitor.node for monitor in self.monitors}) != len(self.monitors)
            or any(monitor.namespace != first.namespace or monitor.instance != first.instance for monitor in self.monitors)
        ):
            raise ValueError("sandbox workers must be distinct and share a namespace and instance")

    async def verify_admission(self):
        return await self.monitors[0].verify_admission()

    async def any_workload(self):
        return await self.monitors[0].any_workload()

    async def active(self):
        return await self.monitors[0].active()

    async def can_bind(self, name, uid):
        return await self.monitors[0].can_bind(name, uid)

    async def can_release(self, pod):
        return await self.monitors[0].can_release(pod)

    async def can_delete_workspace(self, claim):
        return await self.monitors[0].can_delete_workspace(claim)

    async def has_gated_pods(self):
        return await self.monitors[0].has_gated_pods()

    async def release_gates(self, epoch, owner, anchor):
        return await self.monitors[0].release_gates(epoch, owner, anchor)

    async def node_ready(self):
        ready = await asyncio.gather(*(monitor.node_ready() for monitor in self.monitors))
        if not all(type(value) is bool for value in ready):
            raise ValueError("node readiness is unavailable")
        return all(ready)

    async def node_lease_revision(self):
        revisions = await asyncio.gather(*(monitor.node_lease_revision() for monitor in self.monitors))
        if not all(isinstance(value, str) and value for value in revisions):
            raise ValueError("node lease renewal is unavailable")
        return tuple(revisions)


class PoolPowerClient:
    def __init__(self, clients):
        self.clients = tuple(clients)
        if not self.clients:
            raise ValueError("power client pool cannot be empty")

    async def call(self, operation, epoch, owner, anchor):
        if operation not in ("fence", "suspend"):
            raise ValueError("invalid pool power operation")
        # Stop on the first failure. A partial fence or suspend is uncertain;
        # the coordinator keeps the shared gate closed and retires the epoch.
        for client in self.clients:
            await client.call(operation, epoch, owner, anchor)
