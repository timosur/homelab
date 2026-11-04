import base64
import json
import unittest
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer

from wol_proxy.admission import CONTROLLER, GATE, admission_app, review, review_binding


def request(operation="CREATE", pod=None, old=None, user="agent-sandbox"):
    return {"request": {
        "uid": "request-1", "operation": operation, "namespace": "dark-factory-sandboxes",
        "resource": {"group": "", "version": "v1", "resource": "pods"},
        "object": pod or {"spec": {}}, "oldObject": old,
        "userInfo": {"username": user},
    }}


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.namespace = "dark-factory-sandboxes"

    def test_create_adds_gate(self):
        result = review(request(), self.namespace, mutate=True)["response"]
        self.assertTrue(result["allowed"])
        patch = json.loads(base64.b64decode(result["patch"]))
        self.assertEqual(patch, [{"op": "add", "path": "/spec/schedulingGates", "value": [{"name": GATE}]}])

    def test_create_preserves_existing_gates(self):
        pod = {"spec": {"schedulingGates": [{"name": "other.example/gate"}]}}
        result = review(request(pod=pod), self.namespace, mutate=True)["response"]
        patch = json.loads(base64.b64decode(result["patch"]))
        self.assertEqual(patch[0]["path"], "/spec/schedulingGates/-")

    def test_rejects_prebound_and_missing_gate(self):
        self.assertFalse(review(request(pod={"spec": {"nodeName": "desktop"}}), self.namespace, mutate=True)["response"]["allowed"])
        self.assertFalse(review(request(), self.namespace, mutate=False)["response"]["allowed"])

    def test_direct_node_assignment_on_update_is_rejected(self):
        old = {"spec": {"schedulingGates": [{"name": GATE}]}}
        new = {"spec": {"schedulingGates": [{"name": GATE}], "nodeName": "desktop"}}
        self.assertFalse(review(request("UPDATE", new, old, CONTROLLER), self.namespace, mutate=False)["response"]["allowed"])

    def test_only_controller_can_remove_its_gate(self):
        old = {"spec": {"schedulingGates": [{"name": GATE}, {"name": "other.example/gate"}]}}
        new = {"spec": {"schedulingGates": [{"name": "other.example/gate"}]}}
        self.assertFalse(review(request("UPDATE", new, old), self.namespace, mutate=False)["response"]["allowed"])
        self.assertTrue(review(request("UPDATE", new, old, CONTROLLER), self.namespace, mutate=False)["response"]["allowed"])
        self.assertFalse(review(request("UPDATE", {"spec": {}}, old, CONTROLLER), self.namespace, mutate=False)["response"]["allowed"])

    def test_unexpected_namespace_is_denied(self):
        self.assertFalse(review(request(), "different", mutate=True)["response"]["allowed"])

    def test_rejects_malformed_gate_list(self):
        pod = {"spec": {"schedulingGates": "bad"}}
        self.assertFalse(review(request(pod=pod), self.namespace, mutate=True)["response"]["allowed"])


class AdmissionHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.can_bind = AsyncMock(return_value=False)
        self.can_release = AsyncMock(return_value=False)
        self.client = TestClient(TestServer(admission_app("dark-factory-sandboxes", "desktop", self.can_bind, self.can_release)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_admission_review_response_preserves_uid(self):
        result = await self.client.post("/mutate", json=request())
        self.assertEqual(result.status, 200)
        body = await result.json()
        self.assertEqual(body["apiVersion"], "admission.k8s.io/v1")
        self.assertEqual(body["response"]["uid"], "request-1")
        self.assertTrue(body["response"]["allowed"])

    async def test_invalid_review_is_rejected(self):
        result = await self.client.post("/mutate", json={})
        self.assertEqual(result.status, 400)

    async def test_binding_of_gated_pod_is_denied(self):
        body = request(pod={"metadata": {"name": "run-1", "uid": "pod-uid"}, "target": {"kind": "Node", "name": "desktop"}})
        body["request"].update({"subResource": "binding", "name": "run-1"})
        result = await self.client.post("/validate", json=body)
        self.assertFalse((await result.json())["response"]["allowed"])
        self.can_bind.assert_awaited_once_with("run-1", "pod-uid")

    async def test_binding_is_allowed_only_after_gate_is_removed(self):
        self.can_bind.return_value = True
        body = request(pod={"metadata": {"name": "run-1", "uid": "pod-uid"}, "target": {"kind": "Node", "name": "desktop"}})
        body["request"].update({"subResource": "binding", "name": "run-1"})
        result = await self.client.post("/validate", json=body)
        self.assertTrue((await result.json())["response"]["allowed"])

    async def test_binding_to_mismatched_name_is_denied(self):
        body = request(pod={"metadata": {"name": "different"}, "target": {"kind": "Node", "name": "desktop"}})
        body["request"].update({"subResource": "binding", "name": "run-1"})
        result = await self.client.post("/validate", json=body)
        self.assertFalse((await result.json())["response"]["allowed"])
        self.can_bind.assert_not_awaited()

    async def test_legacy_binding_cannot_bypass_gate(self):
        body = request(pod={"metadata": {"name": "run-1", "uid": "pod-uid"}, "target": {"kind": "Node", "name": "desktop"}})
        body["request"]["resource"]["resource"] = "bindings"
        result = await self.client.post("/validate", json=body)
        self.assertFalse((await result.json())["response"]["allowed"])
        self.can_bind.assert_not_awaited()

    async def test_binding_without_uid_is_denied_on_both_apis(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                body = request(pod={"metadata": {"name": "run-1"}, "target": {"kind": "Node", "name": "desktop"}})
                if legacy:
                    body["request"]["resource"]["resource"] = "bindings"
                else:
                    body["request"].update({"subResource": "binding", "name": "run-1"})
                self.can_bind.return_value = True
                result = await self.client.post("/validate", json=body)
                self.assertFalse((await result.json())["response"]["allowed"])
                self.can_bind.assert_not_awaited()

    async def test_binding_cannot_change_workspace_labels(self):
        for key in ("dark-factory.io/task-key", "dark-factory.io/instance", "app.kubernetes.io/managed-by"):
            with self.subTest(key=key):
                body = request(pod={"metadata": {"name": "run-1", "uid": "pod-uid", "labels": {key: "changed"}},
                                    "target": {"kind": "Node", "name": "desktop"}})
                body["request"].update({"subResource": "binding", "name": "run-1"})
                self.can_bind.return_value = True
                result = await self.client.post("/validate", json=body)
                self.assertFalse((await result.json())["response"]["allowed"])
                self.can_bind.assert_not_awaited()

    async def test_controller_cannot_remove_gate_without_durable_reservation(self):
        old = {"spec": {"schedulingGates": [{"name": GATE}]}}
        new = {"metadata": {"annotations": {}}, "spec": {}}
        body = request("UPDATE", new, old, CONTROLLER)
        result = await self.client.post("/validate", json=body)
        self.assertFalse((await result.json())["response"]["allowed"])
        self.can_release.return_value = True
        result = await self.client.post("/validate", json=body)
        self.assertTrue((await result.json())["response"]["allowed"])

    async def test_binding_to_another_node_is_denied(self):
        body = request(pod={"metadata": {"name": "run-1"}, "target": {"kind": "Node", "name": "other"}})
        body["request"].update({"subResource": "binding", "name": "run-1"})
        result = await self.client.post("/validate", json=body)
        self.assertFalse((await result.json())["response"]["allowed"])
        self.can_bind.assert_not_awaited()

    async def test_explicit_worker_pool_accepts_only_designated_ungated_nodes(self):
        can_bind = AsyncMock(return_value=True)
        for node, allowed in (("desktop", True), ("gpu", True), ("other", False)):
            with self.subTest(node=node):
                body = request(pod={"metadata": {"name": "run-1", "uid": "pod-uid"}, "target": {"kind": "Node", "name": node}})
                body["request"].update({"subResource": "binding", "name": "run-1"})
                result = await review_binding(body, "dark-factory-sandboxes", ("desktop", "gpu"), can_bind)
                self.assertEqual(result["response"]["allowed"], allowed)
        can_bind.return_value = False
        body["request"]["object"]["target"]["name"] = "gpu"
        self.assertFalse((await review_binding(body, "dark-factory-sandboxes", ("desktop", "gpu"), can_bind))["response"]["allowed"])

    async def test_worker_pool_configuration_rejects_empty_or_duplicate_nodes(self):
        for nodes in ((), ("desktop", "desktop"), ("desktop", "")):
            with self.subTest(nodes=nodes), self.assertRaises(ValueError):
                admission_app("dark-factory-sandboxes", nodes)
