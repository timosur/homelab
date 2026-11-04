import base64
import copy
import json
import unittest

from test_workspace_identity import fixtures
from wol_proxy.admission import CONTROLLER, GATE, review


def admission_request(pod, *, operation="CREATE", old=None):
    return {"request": {
        "uid": "request", "namespace": "sandboxes", "operation": operation,
        "resource": {"group": "", "version": "v1", "resource": "pods"},
        "object": pod, "oldObject": old, "userInfo": {"username": CONTROLLER},
    }}


class WorkspaceModeAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.pod, _ = fixtures()
        self.pod["spec"]["schedulingGates"] = [{"name": GATE}]

    def result(self, pod, *, operation="CREATE", old=None, mutate=False):
        return review(admission_request(pod, operation=operation, old=old), "sandboxes", mutate=mutate)["response"]

    def test_upstream_pod_gets_policy_without_changing_security_context(self):
        del self.pod["spec"]["securityContext"]["fsGroupChangePolicy"]
        original = copy.deepcopy(self.pod)
        result = self.result(self.pod, mutate=True)
        self.assertTrue(result["allowed"])
        self.assertEqual(json.loads(base64.b64decode(result["patch"])), [{
            "op": "add", "path": "/spec/securityContext/fsGroupChangePolicy", "value": "OnRootMismatch",
        }])
        self.assertEqual(self.pod, original)
        self.assertFalse(self.result(self.pod)["allowed"])

    def test_null_policy_is_replaced_by_mutating_admission(self):
        self.pod["spec"]["securityContext"]["fsGroupChangePolicy"] = None
        result = self.result(self.pod, mutate=True)
        self.assertTrue(result["allowed"])
        self.assertEqual(json.loads(base64.b64decode(result["patch"]))[0]["value"], "OnRootMismatch")
        self.assertFalse(self.result(self.pod)["allowed"])

    def test_gate_and_policy_mutations_are_combined(self):
        del self.pod["spec"]["securityContext"]["fsGroupChangePolicy"]
        del self.pod["spec"]["schedulingGates"]
        result = self.result(self.pod, mutate=True)
        patch = json.loads(base64.b64decode(result["patch"]))
        self.assertTrue(result["allowed"])
        self.assertEqual({item["path"] for item in patch},
                         {"/spec/securityContext/fsGroupChangePolicy", "/spec/schedulingGates"})

    def test_valid_policy_is_idempotent(self):
        for mutate in (True, False):
            with self.subTest(mutate=mutate):
                result = self.result(self.pod, mutate=mutate)
                self.assertTrue(result["allowed"])
                self.assertNotIn("patch", result)

    def test_invalid_security_context_or_explicit_always_is_rejected(self):
        changes = (
            lambda p: p["spec"]["securityContext"].update(fsGroupChangePolicy="Always"),
            lambda p: p["spec"]["securityContext"].update(fsGroup=42),
            lambda p: p["spec"]["securityContext"].pop("fsGroup"),
            lambda p: p["spec"]["securityContext"].update(runAsUser=0),
            lambda p: p["spec"]["securityContext"].update(runAsGroup=0),
            lambda p: p["spec"]["securityContext"].update(runAsNonRoot=False),
            lambda p: p["spec"].pop("securityContext"),
            lambda p: p["spec"].update(securityContext="bad"),
        )
        for change in changes:
            for mutate in (True, False):
                with self.subTest(change=change, mutate=mutate):
                    pod = copy.deepcopy(self.pod)
                    change(pod)
                    self.assertFalse(self.result(pod, mutate=mutate)["allowed"])

    def test_container_identity_overrides_cannot_weaken_nonroot_context(self):
        for field in ("containers", "initContainers", "ephemeralContainers"):
            for override in ({"runAsUser": 0}, {"runAsGroup": 0}, {"runAsNonRoot": False},
                             {"runAsUser": True}, {"runAsGroup": "1000"}):
                with self.subTest(field=field, override=override):
                    pod = copy.deepcopy(self.pod)
                    pod["spec"][field] = [{"name": "probe", "securityContext": override}]
                    self.assertFalse(self.result(pod, mutate=True)["allowed"])
                    self.assertFalse(self.result(pod)["allowed"])
        self.pod["spec"]["containers"] = [{"name": "agent", "securityContext": {
            "runAsUser": 1000, "runAsGroup": 1000, "runAsNonRoot": True,
        }}]
        self.assertTrue(self.result(self.pod)["allowed"])

    def test_update_cannot_weaken_policy_even_if_labels_are_removed(self):
        old = copy.deepcopy(self.pod)
        for change in (
            lambda p: p["spec"]["securityContext"].pop("fsGroupChangePolicy"),
            lambda p: p["spec"]["securityContext"].update(fsGroupChangePolicy="Always"),
            lambda p: p["spec"]["securityContext"].update(fsGroup=42),
            lambda p: p["spec"].pop("securityContext"),
        ):
            with self.subTest(change=change):
                new = copy.deepcopy(old)
                new["metadata"]["labels"] = {}
                change(new)
                self.assertFalse(self.result(new, operation="UPDATE", old=old)["allowed"])

    def test_unlabelled_task_claim_cannot_bypass_policy(self):
        self.pod["metadata"]["labels"] = {}
        self.pod["spec"]["securityContext"]["fsGroupChangePolicy"] = "Always"
        self.assertFalse(self.result(self.pod, mutate=True)["allowed"])

    def test_unrelated_pod_is_unchanged(self):
        pod = {"metadata": {"name": "unrelated"}, "spec": {"schedulingGates": [{"name": GATE}]}}
        for mutate in (True, False):
            result = self.result(pod, mutate=mutate)
            self.assertTrue(result["allowed"])
            self.assertNotIn("patch", result)
