import base64
import copy
import json
import unittest
from unittest.mock import AsyncMock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from wol_proxy.admission import CONTROLLER
from wol_proxy.coder_admission import GATE, PROVISIONER, register, review

WORKSPACE = "11111111-1111-1111-1111-111111111111"


def pod():
    return {"metadata": {"name": f"coder-{WORKSPACE}", "labels": {
        "coder.workspace.id": WORKSPACE, "app.kubernetes.io/name": "coder-workspace",
        "app.kubernetes.io/component": "development-workspace",
    }}, "spec": {}}


def request(operation="CREATE", value=None, old=None, user=PROVISIONER):
    return {"request": {"uid": "request-1", "operation": operation, "namespace": "coder",
                        "resource": {"group": "", "version": "v1", "resource": "pods"},
                        "object": value or pod(), "oldObject": old, "userInfo": {"username": user}}}


class CoderAdmissionTests(unittest.TestCase):
    def test_create_adds_gate_and_requires_it_at_validation(self):
        result = review(request(), "coder", mutate=True)["response"]
        self.assertEqual(json.loads(base64.b64decode(result["patch"])), [
            {"op": "add", "path": "/spec/schedulingGates", "value": [{"name": GATE}]},
        ])
        self.assertFalse(review(request(), "coder", mutate=False)["response"]["allowed"])
        gated = pod()
        gated["spec"]["schedulingGates"] = [{"name": GATE}]
        self.assertTrue(review(request(value=gated), "coder", mutate=False)["response"]["allowed"])

    def test_unrelated_coder_control_plane_pod_is_not_gated(self):
        control_plane = {"metadata": {"name": "coder-deployment-hash", "labels": {"app": "coder"}}, "spec": {}}
        result = review(request(value=control_plane, user="system:serviceaccount:kube-system:replicaset-controller"), "coder", mutate=True)["response"]
        self.assertTrue(result["allowed"])
        self.assertNotIn("patch", result)

    def test_provisioner_cannot_bypass_gate_by_omitting_identity(self):
        self.assertFalse(review(request(value={"metadata": {"name": "other"}, "spec": {}}), "coder", mutate=True)["response"]["allowed"])

    def test_create_rejects_prebinding_and_other_creator(self):
        value = pod()
        value["spec"]["nodeName"] = "homelab-gpu"
        self.assertFalse(review(request(value=value), "coder", mutate=True)["response"]["allowed"])
        self.assertFalse(review(request(user="other"), "coder", mutate=True)["response"]["allowed"])

    def test_gate_release_requires_controller_and_preserves_identity(self):
        old = pod()
        old["spec"]["schedulingGates"] = [{"name": GATE}]
        new = copy.deepcopy(old)
        new["spec"]["schedulingGates"] = []
        self.assertFalse(review(request("UPDATE", new, old), "coder", mutate=False)["response"]["allowed"])
        self.assertTrue(review(request("UPDATE", new, old, CONTROLLER), "coder", mutate=False)["response"]["allowed"])
        new["metadata"]["labels"].pop("coder.workspace.id")
        self.assertFalse(review(request("UPDATE", new, old, CONTROLLER), "coder", mutate=False)["response"]["allowed"])


class CoderAdmissionHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app = web.Application()
        self.can_release = AsyncMock(return_value=False)
        self.binding_pod = AsyncMock()
        register(app, "coder", ("homelab-amd-desktop", "homelab-gpu"), self.can_release, self.binding_pod)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_gate_release_requires_reservation_proof(self):
        old = pod()
        old["spec"]["schedulingGates"] = [{"name": GATE}]
        new = copy.deepcopy(old)
        new["spec"]["schedulingGates"] = []
        body = request("UPDATE", new, old, CONTROLLER)
        result = await self.client.post("/coder/validate", json=body)
        self.assertFalse((await result.json())["response"]["allowed"])
        self.can_release.return_value = True
        result = await self.client.post("/coder/validate", json=body)
        self.assertTrue((await result.json())["response"]["allowed"])

    async def test_binding_to_pool_requires_ungated_matching_pod(self):
        actual = pod()
        actual["metadata"]["uid"] = "pod-uid"
        actual["spec"]["schedulingGates"] = [{"name": GATE}]
        self.binding_pod.return_value = actual
        body = request(value={"metadata": {"name": f"coder-{WORKSPACE}", "uid": "pod-uid"},
                              "target": {"kind": "Node", "name": "homelab-gpu"}})
        body["request"].update(subResource="binding", name=f"coder-{WORKSPACE}")
        result = await self.client.post("/coder/validate", json=body)
        self.assertFalse((await result.json())["response"]["allowed"])
        actual["spec"]["schedulingGates"] = []
        result = await self.client.post("/coder/validate", json=body)
        self.assertTrue((await result.json())["response"]["allowed"])
        body["request"]["object"]["target"]["name"] = "other"
        result = await self.client.post("/coder/validate", json=body)
        self.assertFalse((await result.json())["response"]["allowed"])

    def test_update_rejects_node_assignment(self):
        old = pod()
        new = copy.deepcopy(old)
        new["spec"]["nodeName"] = "homelab-gpu"
        self.assertFalse(review(request("UPDATE", new, old, CONTROLLER), "coder", mutate=False)["response"]["allowed"])
