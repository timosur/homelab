import copy
import os
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import ClientResponseError
from aiohttp.test_utils import TestClient, TestServer

from test_interlock import FakeKubernetes
from test_workspace_identity import fixtures
from wol_proxy.admission import CONTROLLER, GATE, RELEASE_TOKEN, admission_app
from wol_proxy.interlock import Interlock, InterlockError
from wol_proxy.sandboxes import SandboxMonitor
from wol_proxy.workspace import WorkspaceVerifier


class WorkspaceGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api = FakeKubernetes()
        self.api.data["workspace_records"] = "{}"
        self.interlock = Interlock(self.api)
        await self.interlock.claim()
        await self.interlock.transition("idle", "releasing")
        self.pod, self.claim = fixtures()
        self.old_pods = {}
        self.pod["metadata"]["resourceVersion"] = "7"
        self.pod["spec"]["schedulingGates"] = [{"name": GATE}]
        with patch.dict(os.environ, {"KUBERNETES_SERVICE_HOST": "kubernetes"}):
            self.monitor = SandboxMonitor("sandboxes", "factory", "desktop")

        async def get(path, **kwargs):
            if path == "/api/v1/namespaces/sandboxes/pods":
                return {"items": [copy.deepcopy(self.pod)]}
            prefix = "/api/v1/namespaces/sandboxes/pods/"
            if path.startswith(prefix):
                name = path[len(prefix):]
                if name == self.pod["metadata"]["name"]:
                    return copy.deepcopy(self.pod)
                if name in self.old_pods:
                    return copy.deepcopy(self.old_pods[name])
                raise ClientResponseError(None, (), status=404)
            if path == "/api/v1/namespaces/sandboxes/persistentvolumeclaims/task-df-1":
                return copy.deepcopy(self.claim)
            if path == self.interlock.path:
                return await self.api._get(path)
            raise AssertionError(f"unexpected path: {path}")

        self.monitor._get = AsyncMock(side_effect=get)
        self.monitor._put = AsyncMock(side_effect=self.api._put)
        self.monitor._patch = AsyncMock()
        verifier = WorkspaceVerifier(self.monitor, "sandboxes", "factory", "dark-factory-nfs", ("desktop", "gpu"))
        self.monitor.workspace_check = verifier.check
        self.monitor.workspace_reserve = verifier.reserve
        self.client = TestClient(TestServer(admission_app(
            "sandboxes", ("desktop", "gpu"), self.monitor.can_bind, self.monitor.can_release,
        )))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def test_reservation_is_persisted_before_release_and_reused_for_checks(self):
        async def patch_after_reservation(path, patch_body):
            self.assertTrue(await self.monitor.workspace_check(self.pod))

        self.monitor._patch.side_effect = patch_after_reservation
        await self.monitor.release_gates(self.interlock.epoch, self.interlock.owner, self.interlock.anchor)
        self.monitor._patch.assert_awaited_once()
        candidate = copy.deepcopy(self.pod)
        candidate["spec"]["schedulingGates"] = []
        candidate["metadata"]["annotations"] = {
            RELEASE_TOKEN: f"{self.interlock.epoch}:{self.interlock.owner}:{self.interlock.anchor}",
        }
        self.monitor._put.reset_mock()
        self.assertTrue(await self.monitor.can_release(candidate))
        self.pod = candidate
        self.assertTrue(await self.monitor.can_bind("run-df-1-1", "pod-uid"))
        self.assertFalse(await self.monitor.can_bind("run-df-1-1", "old-uid"))
        self.monitor._put.assert_not_awaited()

    async def test_missing_state_or_incompatible_claim_prevents_any_gate_patch(self):
        self.api.data.pop("workspace_records")
        with self.assertRaises(InterlockError):
            await self.monitor.release_gates(self.interlock.epoch, self.interlock.owner, self.interlock.anchor)
        self.monitor._patch.assert_not_awaited()
        self.api.data["workspace_records"] = "{}"
        self.claim["spec"]["storageClassName"] = "homelab-iscsi"
        with self.assertRaises(ValueError):
            await self.monitor.release_gates(self.interlock.epoch, self.interlock.owner, self.interlock.anchor)
        self.monitor._patch.assert_not_awaited()

    async def test_uncertain_gate_patch_keeps_workspace_reserved(self):
        self.monitor._patch.side_effect = OSError("response lost")
        with self.assertRaises(OSError):
            await self.monitor.release_gates(self.interlock.epoch, self.interlock.owner, self.interlock.anchor)
        self.assertTrue(await self.monitor.workspace_check(self.pod))
        self.old_pods[self.pod["metadata"]["name"]] = copy.deepcopy(self.pod)
        self.pod["metadata"]["name"] = "run-df-1-2"
        self.pod["metadata"]["uid"] = "replacement-pod"
        await self.monitor.release_gates(self.interlock.epoch, self.interlock.owner, self.interlock.anchor)
        self.assertEqual(self.monitor._patch.await_count, 1)

    async def test_later_run_receives_same_claim_only_after_old_pod_disappears(self):
        await self.monitor.release_gates(self.interlock.epoch, self.interlock.owner, self.interlock.anchor)
        self.old_pods[self.pod["metadata"]["name"]] = copy.deepcopy(self.pod)
        self.pod["metadata"].update(name="run-df-1-2", uid="pod-2", resourceVersion="8")
        await self.monitor.release_gates(self.interlock.epoch, self.interlock.owner, self.interlock.anchor)
        self.assertFalse(await self.monitor.workspace_check(self.pod))
        self.old_pods.clear()
        await self.monitor.release_gates(self.interlock.epoch, self.interlock.owner, self.interlock.anchor)
        self.assertTrue(await self.monitor.workspace_check(self.pod))

    async def test_claim_replacement_never_authorizes_release_or_binding(self):
        await self.monitor.release_gates(self.interlock.epoch, self.interlock.owner, self.interlock.anchor)
        self.claim["metadata"]["uid"] = "replacement-claim"
        self.pod["spec"]["schedulingGates"] = []
        self.pod["metadata"]["annotations"] = {
            RELEASE_TOKEN: f"{self.interlock.epoch}:{self.interlock.owner}:{self.interlock.anchor}",
        }
        self.assertFalse(await self.monitor.can_release(self.pod))
        self.assertFalse(await self.monitor.can_bind("run-df-1-1", "pod-uid"))

    async def test_next_run_can_reserve_fresh_pvc_after_previous_pod_is_gone(self):
        await self.monitor.release_gates(self.interlock.epoch, self.interlock.owner, self.interlock.anchor)
        self.old_pods[self.pod["metadata"]["name"]] = copy.deepcopy(self.pod)
        self.pod["metadata"].update(name="run-df-1-2", uid="pod-2", resourceVersion="8")
        self.claim["metadata"]["uid"] = "new-claim-uid"
        await self.monitor.release_gates(self.interlock.epoch, self.interlock.owner, self.interlock.anchor)
        self.assertFalse(await self.monitor.workspace_check(self.pod))
        self.old_pods.clear()
        await self.monitor.release_gates(self.interlock.epoch, self.interlock.owner, self.interlock.anchor)
        self.assertTrue(await self.monitor.workspace_check(self.pod))

    def binding(self, *, uid="pod-uid", legacy=False, dry_run=False):
        return {"request": {
            "uid": "binding-request", "namespace": "sandboxes", "operation": "CREATE",
            "resource": {"group": "", "version": "v1", "resource": "bindings" if legacy else "pods"},
            "subResource": "" if legacy else "binding", "name": "run-df-1-1", "dryRun": dry_run,
            "object": {"metadata": {"name": "run-df-1-1", "uid": uid},
                       "target": {"kind": "Node", "name": "gpu"}},
        }}

    def release(self, *, user=CONTROLLER, dry_run=False):
        new = copy.deepcopy(self.pod)
        new["spec"]["schedulingGates"] = []
        new["metadata"]["annotations"] = {
            RELEASE_TOKEN: f"{self.interlock.epoch}:{self.interlock.owner}:{self.interlock.anchor}",
        }
        return {"request": {
            "uid": "release-request", "namespace": "sandboxes", "operation": "UPDATE",
            "resource": {"group": "", "version": "v1", "resource": "pods"},
            "userInfo": {"username": user}, "dryRun": dry_run,
            "oldObject": copy.deepcopy(self.pod), "object": new,
        }}

    async def allowed(self, body):
        result = await self.client.post("/validate", json=body)
        self.assertEqual(result.status, 200)
        return (await result.json())["response"]["allowed"]

    async def test_dry_run_checks_reservation_without_writing_or_releasing(self):
        self.assertFalse(await self.allowed(self.release(dry_run=True)))
        await self.monitor.release_gates(self.interlock.epoch, self.interlock.owner, self.interlock.anchor)
        self.monitor._put.reset_mock()
        before = copy.deepcopy(self.api.data)
        self.assertFalse(await self.allowed(self.release(user="other", dry_run=True)))
        self.assertTrue(await self.allowed(self.release(dry_run=True)))
        self.assertFalse(await self.allowed(self.binding(dry_run=True)))  # still gated in API
        self.pod = self.release()["request"]["object"]
        self.assertTrue(await self.allowed(self.binding(dry_run=True)))
        self.assertFalse(await self.allowed(self.binding(legacy=True, dry_run=True)))
        self.assertEqual(self.api.data, before)
        self.monitor._put.assert_not_awaited()

    async def test_delayed_binding_for_old_uid_is_denied_after_handoff(self):
        await self.monitor.release_gates(self.interlock.epoch, self.interlock.owner, self.interlock.anchor)
        self.pod["spec"]["schedulingGates"] = []
        self.assertTrue(await self.allowed(self.binding()))
        self.pod["metadata"].update(uid="replacement-pod", resourceVersion="8")
        self.pod["spec"]["schedulingGates"] = [{"name": GATE}]
        await self.monitor.release_gates(self.interlock.epoch, self.interlock.owner, self.interlock.anchor)
        self.pod["spec"]["schedulingGates"] = []
        self.monitor._put.reset_mock()
        self.assertFalse(await self.allowed(self.binding(uid="pod-uid")))
        self.assertTrue(await self.allowed(self.binding(uid="replacement-pod")))
        self.monitor._put.assert_not_awaited()

    async def test_api_outage_fails_closed_for_gate_and_binding(self):
        await self.monitor.release_gates(self.interlock.epoch, self.interlock.owner, self.interlock.anchor)
        release = self.release()
        self.pod = copy.deepcopy(release["request"]["object"])
        self.monitor._put.reset_mock()
        self.monitor._get = AsyncMock(side_effect=ValueError("Kubernetes API unavailable"))
        for body in (release, self.binding()):
            with self.subTest(operation=body["request"]["operation"]):
                result = await self.client.post("/validate", json=body)
                self.assertEqual(result.status, 400)
        self.monitor._put.assert_not_awaited()
