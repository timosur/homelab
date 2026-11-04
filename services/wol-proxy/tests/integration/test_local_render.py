"""Opt-in render check for local Dark Factory and wake manifests."""

import os
import subprocess
import unittest
from pathlib import Path

import yaml


@unittest.skipUnless(os.environ.get("DF_TEST_LOCAL_RENDER"), "set DF_TEST_LOCAL_RENDER for Kustomize render tests")
class LocalRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[4]
        cls.objects = []
        for directory in ("apps/dark-factory-wake", "apps/dark-factory", "apps/wol-proxy"):
            rendered = subprocess.run(["kubectl", "kustomize", str(root / directory)],
                                      check=True, capture_output=True, text=True, timeout=60)
            cls.objects.extend(obj for obj in yaml.safe_load_all(rendered.stdout) if obj)

    def find(self, kind, name):
        matches = [obj for obj in self.objects if obj["kind"] == kind and obj["metadata"]["name"] == name]
        self.assertEqual(len(matches), 1, (kind, name))
        return matches[0]

    def test_admission_hooks_cover_pods_claims_and_bindings(self):
        for kind, path, required in (
            ("MutatingWebhookConfiguration", "/mutate", {
                "pods": {"CREATE"}, "persistentvolumeclaims": {"CREATE", "UPDATE"},
            }),
            ("ValidatingWebhookConfiguration", "/validate", {
                "pods": {"CREATE", "UPDATE"}, "pods/binding": {"CREATE"}, "bindings": {"CREATE"},
                "persistentvolumeclaims": {"CREATE", "UPDATE", "DELETE"},
            }),
        ):
            with self.subTest(kind=kind):
                config = self.find(kind, "dark-factory-sandbox-gate")
                self.assertEqual(len(config["webhooks"]), 1)
                hook = config["webhooks"][0]
                self.assertEqual(hook["failurePolicy"], "Fail")
                self.assertEqual(hook["sideEffects"], "None")
                self.assertEqual(hook["namespaceSelector"]["matchLabels"], {
                    "kubernetes.io/metadata.name": "dark-factory-sandboxes",
                })
                self.assertEqual(hook["clientConfig"]["service"], {
                    "name": "dark-factory-admission", "namespace": "wol-proxy", "path": path, "port": 443,
                })
                for resource, operations in required.items():
                    self.assertTrue(any("" in rule["apiGroups"] and "v1" in rule["apiVersions"]
                                        and resource in rule["resources"] and operations <= set(rule["operations"])
                                        and rule["scope"] == "Namespaced" for rule in hook["rules"]))

    def test_interlock_and_sandbox_permissions_are_scoped(self):
        interlock = self.find("ConfigMap", "dark-factory-interlock")
        self.assertEqual(interlock["data"]["workspace_records"], "{}")
        role = self.find("Role", "dark-factory-interlock")
        self.assertEqual(role["metadata"]["namespace"], "wol-proxy")
        self.assertEqual(role["rules"], [{"apiGroups": [""], "resources": ["configmaps"],
                                        "resourceNames": ["dark-factory-interlock"], "verbs": ["get", "update"]}])
        binding = self.find("RoleBinding", "dark-factory-interlock")
        self.assertEqual(binding["subjects"], [{"kind": "ServiceAccount", "name": "wol-proxy", "namespace": "wol-proxy"}])
        reader = self.find("Role", "dark-factory-wake-reader")
        self.assertEqual(reader["metadata"]["namespace"], "dark-factory-sandboxes")
        for resource, verbs in (("pods", {"get", "list", "patch"}), ("persistentvolumeclaims", {"get"})):
            self.assertTrue(any(resource in rule["resources"] and verbs <= set(rule["verbs"])
                                for rule in reader["rules"]))

    def test_runtime_class_and_admission_service_render(self):
        runtime = self.find("RuntimeClass", "kata-dark-factory")
        self.assertEqual(runtime["handler"], "kata")
        self.assertEqual(runtime["scheduling"]["nodeSelector"], {
            "dark-factory.home.timosur.com/sandbox-worker": "true",
        })
        self.assertEqual({item["key"] for item in runtime["scheduling"]["tolerations"]}, {"availability", "gpu"})
        service = self.find("Service", "dark-factory-admission")
        self.assertEqual(service["metadata"]["namespace"], "wol-proxy")
        self.assertEqual(service["spec"]["selector"], {"app": "wol-proxy"})
        self.assertTrue(any(port["port"] == 443 and port["targetPort"] == "admission"
                            for port in service["spec"]["ports"]))


if __name__ == "__main__":
    unittest.main()
