"""Durable, optimistic ownership for pod release and desktop suspension."""

import uuid


class InterlockError(Exception):
    pass


class Interlock:
    def __init__(self, client, namespace="wol-proxy", name="dark-factory-interlock"):
        self.client = client
        self.namespace = namespace
        self.name = name
        self.path = f"/api/v1/namespaces/{namespace}/configmaps/{name}"
        self.owner = uuid.uuid4().hex
        self.epoch = None
        self.anchor = None
        self.lost = False

    async def read(self):
        obj = await self.client._get(self.path)
        data, meta = obj.get("data"), obj.get("metadata")
        if not isinstance(data, dict) or not isinstance(meta, dict):
            raise InterlockError("interlock state is missing")
        epoch, owner, phase, http_active = data.get("epoch"), data.get("owner"), data.get("phase"), data.get("http_active")
        revision, anchor = meta.get("resourceVersion"), meta.get("uid")
        if (
            not isinstance(epoch, str) or not epoch.isascii() or not epoch.isdigit()
            or not isinstance(http_active, str) or not http_active.isascii() or not http_active.isdigit()
            or not isinstance(owner, str) or phase not in ("idle", "releasing", "suspending")
            or not isinstance(revision, str) or not revision
            or not isinstance(anchor, str) or not anchor
        ):
            raise InterlockError("interlock state is invalid")
        return {"epoch": int(epoch), "owner": owner, "phase": phase, "http_active": int(http_active), "revision": revision, "anchor": anchor, "metadata": meta}

    async def _replace(self, state, *, epoch, owner, phase, http_active=None):
        await self.client._put(self.path, {
            "apiVersion": "v1", "kind": "ConfigMap",
            "metadata": state["metadata"],
            "data": {"epoch": str(epoch), "owner": owner, "phase": phase,
                     "http_active": str(state["http_active"] if http_active is None else http_active)},
        })

    async def claim(self):
        if self.lost:
            raise InterlockError("ownership was lost")
        state = await self.read()
        if state["phase"] == "releasing":
            raise InterlockError("gate release outcome is uncertain; operator recovery required")
        if state["http_active"] and state["owner"] != self.owner:
            raise InterlockError("previous proxy requests may still be active; operator recovery required")
        if self.epoch is not None:
            if state["owner"] != self.owner or state["epoch"] != self.epoch or state["anchor"] != self.anchor:
                self.lost = True
                raise InterlockError("ownership was lost")
            return state
        # A previous PUT response may have been lost. Reuse this process's claim.
        if state["owner"] == self.owner and state["epoch"] > 0:
            self.epoch = state["epoch"]
            self.anchor = state["anchor"]
            return state
        epoch = state["epoch"] + 1
        await self._replace(state, epoch=epoch, owner=self.owner, phase=state["phase"])
        self.epoch = epoch
        self.anchor = state["anchor"]
        state.update(epoch=epoch, owner=self.owner)
        return state

    async def transition(self, expected, target):
        state = await self.read()
        if self.epoch is None or state["owner"] != self.owner or state["epoch"] != self.epoch or state["anchor"] != self.anchor:
            self.lost = True
            raise InterlockError("ownership was lost")
        if state["phase"] != expected:
            raise InterlockError(f"interlock is {state['phase']}, not {expected}")
        if target == "suspending" and state["http_active"]:
            raise InterlockError("proxy requests are still active")
        await self._replace(state, epoch=self.epoch, owner=self.owner, phase=target)

    async def retire_suspend_epoch(self):
        """Fence a suspend request that might still be in transit after SSH ends."""
        state = await self.read()
        if self.epoch is None or state["owner"] != self.owner or state["epoch"] != self.epoch or state["anchor"] != self.anchor:
            self.lost = True
            raise InterlockError("ownership was lost")
        if state["phase"] != "suspending":
            raise InterlockError("no suspend to fence")
        next_epoch = self.epoch + 1
        await self._replace(state, epoch=next_epoch, owner=self.owner, phase="suspending")
        self.epoch = next_epoch

    async def http_change(self, amount):
        if amount not in (-1, 1):
            raise ValueError("invalid HTTP activity change")
        state = await self.read()
        if self.epoch is None or state["owner"] != self.owner or state["epoch"] != self.epoch or state["anchor"] != self.anchor:
            self.lost = True
            raise InterlockError("ownership was lost")
        if state["phase"] == "suspending" or state["http_active"] + amount < 0:
            raise InterlockError("HTTP activity cannot change in the current state")
        await self._replace(state, epoch=self.epoch, owner=self.owner, phase=state["phase"], http_active=state["http_active"] + amount)
