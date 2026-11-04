"""Durable task-workspace reservations anchored to the power interlock."""

import json
import re
from urllib.parse import quote

from aiohttp import ClientResponseError

from .interlock import Interlock, InterlockError
from .workspace_security import workspace_security


class WorkspaceBusy(InterlockError):
    """The previous pod still exists; this is a normal handoff delay."""


def workspace_identity(pod, claim, namespace, instance, storage_class):
    """Validate a persisted sandbox pod and its single task claim without side effects."""
    if (not all(isinstance(value, str) and value for value in (namespace, instance, storage_class))
            or not isinstance(pod, dict) or not isinstance(claim, dict)):
        raise ValueError("invalid workspace context")
    pod_meta, claim_meta = pod.get("metadata"), claim.get("metadata")
    pod_spec, claim_spec = pod.get("spec"), claim.get("spec")
    if not all(isinstance(value, dict) for value in (pod_meta, claim_meta, pod_spec, claim_spec)):
        raise ValueError("incomplete workspace objects")
    if not workspace_security(pod_spec):
        raise ValueError("sandbox workspace security context is incompatible")
    if any(meta.get("namespace") != namespace or meta.get("deletionTimestamp")
           or not all(isinstance(meta.get(key), str) and meta[key] for key in ("name", "uid"))
           for meta in (pod_meta, claim_meta)):
        raise ValueError("invalid workspace object identity")
    pod_labels, claim_labels = pod_meta.get("labels"), claim_meta.get("labels")
    if not isinstance(pod_labels, dict) or not isinstance(claim_labels, dict):
        raise ValueError("workspace labels are missing")
    task_key = claim_labels.get("dark-factory.io/task-key")
    if (not isinstance(task_key, str) or not task_key or not task_key.isascii()
            or claim_meta["name"] != f"task-{task_key.lower()}"):
        raise ValueError("invalid workspace task identity")
    for labels in (pod_labels, claim_labels):
        if (labels.get("app.kubernetes.io/managed-by") != "dark-factory"
                or labels.get("dark-factory.io/instance") != instance
                or labels.get("dark-factory.io/task-key") != task_key):
            raise ValueError("workspace labels do not match")
    if (claim_spec.get("storageClassName") != storage_class
            or claim_spec.get("accessModes") != ["ReadWriteOncePod"]):
        raise ValueError("workspace claim is incompatible")
    volumes = pod_spec.get("volumes")
    if not isinstance(volumes, list) or not all(isinstance(volume, dict) for volume in volumes):
        raise ValueError("sandbox volumes are invalid")
    for volume in volumes:
        sources = set(volume) - {"name"}
        if len(sources) != 1:
            raise ValueError("sandbox volume source is ambiguous")
        source = next(iter(sources))
        if source not in {"persistentVolumeClaim", "image", "emptyDir", "csi"} or not isinstance(volume[source], dict):
            raise ValueError("sandbox volume source is unsupported")
        if source == "csi" and (volume[source].get("driver") != "csi.cert-manager.io"
                                or volume[source].get("readOnly") is not True):
            raise ValueError("sandbox CSI volume is not the read-only certificate volume")
    claims = [volume for volume in volumes if "persistentVolumeClaim" in volume]
    if (len(claims) != 1 or claims[0].get("name") != "task-data"
            or not isinstance(claims[0]["persistentVolumeClaim"], dict)
            or claims[0]["persistentVolumeClaim"].get("claimName") != claim_meta["name"]):
        raise ValueError("sandbox must reference exactly its own task claim")
    return claim_meta["name"], claim_meta["uid"], pod_meta["name"], pod_meta["uid"]


class WorkspaceReservations:
    def __init__(self, interlock, nodes):
        self.interlock = interlock
        if (not isinstance(nodes, (list, tuple)) or not nodes
                or not all(isinstance(node, str) and node for node in nodes)
                or len(set(nodes)) != len(nodes)):
            raise ValueError("workspace worker pool must be nonempty and distinct")
        self.nodes = tuple(nodes)

    @staticmethod
    def _records(state):
        raw = state["data"].get("workspace_records")
        if not isinstance(raw, str):
            raise InterlockError("workspace reservation state is missing")
        def unique_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate reservation key")
                result[key] = value
            return result

        try:
            records = json.loads(raw, object_pairs_hook=unique_keys)
        except ValueError as exc:
            raise InterlockError("workspace reservation state is invalid") from exc
        if not isinstance(records, dict):
            raise InterlockError("workspace reservation state is invalid")
        for claim_name, record in records.items():
            if (not isinstance(claim_name, str) or not claim_name or not isinstance(record, dict)
                    or set(record) != {"claim_uid", "pod_name", "pod_uid", "possible_nodes"}
                    or not all(isinstance(record[key], str) and record[key]
                               for key in ("claim_uid", "pod_name", "pod_uid"))):
                raise InterlockError("workspace reservation state is invalid")
            nodes = record["possible_nodes"]
            if (not isinstance(nodes, list) or not nodes
                    or not all(isinstance(node, str) and node for node in nodes)
                    or len(set(nodes)) != len(nodes)):
                raise InterlockError("workspace reservation state is invalid")
        return records

    async def owner(self, claim_name):
        state = await self.interlock.read()
        return self._records(state).get(claim_name)

    async def matches(self, claim_name, claim_uid, pod_name, pod_uid):
        if not all(isinstance(value, str) and value for value in (claim_name, claim_uid, pod_name, pod_uid)):
            return False
        record = await self.owner(claim_name)
        return record == {"claim_uid": claim_uid, "pod_name": pod_name, "pod_uid": pod_uid,
                          "possible_nodes": list(self.nodes)}

    async def reserve(self, claim_name, claim_uid, pod_name, pod_uid, *, previous_gone=None):
        if not all(isinstance(value, str) and value for value in (claim_name, claim_uid, pod_name, pod_uid)):
            raise ValueError("workspace and pod identities are required")
        state = await self.interlock.read()
        if (
            self.interlock.lost or self.interlock.epoch is None or state["owner"] != self.interlock.owner
            or state["epoch"] != self.interlock.epoch or state["anchor"] != self.interlock.anchor
            or state["phase"] != "releasing"
        ):
            raise InterlockError("workspace reservation requires the current release owner")
        records = self._records(state)
        desired = {"claim_uid": claim_uid, "pod_name": pod_name, "pod_uid": pod_uid,
                   "possible_nodes": list(self.nodes)}
        previous = records.get(claim_name)
        if previous is not None:
            if previous == desired:
                return
            if previous["possible_nodes"] != list(self.nodes):
                raise InterlockError("workspace worker pool changed")
            if previous_gone is None or await previous_gone(previous) is not True:
                raise WorkspaceBusy("previous workspace pod still exists")
        records[claim_name] = desired
        state["data"]["workspace_records"] = json.dumps(records, sort_keys=True, separators=(",", ":"))
        await self.interlock._replace(state, epoch=self.interlock.epoch, owner=self.interlock.owner,
                                      phase="releasing")


class WorkspaceVerifier:
    """Resolve workspace identity, reserve before release, and check admission read-only."""

    def __init__(self, client, namespace, instance, storage_class, nodes):
        if not all(isinstance(value, str) and value for value in (namespace, instance, storage_class)):
            raise ValueError("workspace admission context is required")
        self.client = client
        self.namespace, self.instance, self.storage_class = namespace, instance, storage_class
        self.reservations = WorkspaceReservations(Interlock(client), nodes)

    async def identity(self, pod):
        metadata = pod.get("metadata") if isinstance(pod, dict) else None
        labels = metadata.get("labels") if isinstance(metadata, dict) else None
        key = labels.get("dark-factory.io/task-key") if isinstance(labels, dict) else None
        if not isinstance(key, str) or re.fullmatch(r"[A-Z][A-Z0-9]{1,9}-[1-9][0-9]{0,19}", key) is None:
            raise ValueError("invalid sandbox task identity")
        name = "task-" + key.lower()
        claim = await self.client._get(
            f"/api/v1/namespaces/{quote(self.namespace, safe='')}/persistentvolumeclaims/{quote(name, safe='')}"
        )
        return workspace_identity(pod, claim, self.namespace, self.instance, self.storage_class)

    async def check(self, pod):
        return await self.reservations.matches(*(await self.identity(pod)))

    async def previous_gone(self, previous):
        name, uid = previous.get("pod_name"), previous.get("pod_uid")
        if not all(isinstance(value, str) and value for value in (name, uid)):
            raise ValueError("previous pod identity is invalid")
        try:
            pod = await self.client._get(
                f"/api/v1/namespaces/{quote(self.namespace, safe='')}/pods/{quote(name, safe='')}"
            )
        except ClientResponseError as exc:
            if exc.status == 404:
                return True
            raise
        metadata = pod.get("metadata") if isinstance(pod, dict) else None
        if (not isinstance(metadata, dict) or metadata.get("name") != name
                or metadata.get("namespace") != self.namespace
                or not isinstance(metadata.get("uid"), str) or not metadata["uid"]):
            raise ValueError("previous pod response is invalid")
        return metadata["uid"] != uid

    async def reserve(self, pod, epoch, owner, anchor):
        if (type(epoch) is not int or epoch < 1
                or not isinstance(owner, str) or not owner or not isinstance(anchor, str) or not anchor):
            raise ValueError("release ownership identity is required")
        identity = await self.identity(pod)
        interlock = Interlock(self.client)
        interlock.epoch, interlock.owner, interlock.anchor = epoch, owner, anchor
        reservations = WorkspaceReservations(interlock, self.reservations.nodes)
        await reservations.reserve(*identity, previous_gone=self.previous_gone)
