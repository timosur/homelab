import base64
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer
import yaml

from wol_proxy.admission import admission_app
from wol_proxy.config import load_config
from wol_proxy.server import build_admission_app


class WorkspaceAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.can_delete = AsyncMock(return_value=True)
        app = admission_app(
            "dark-factory-sandboxes", "desktop",
            workspace_instance="dark-factory", workspace_storage_class="dark-factory-nfs",
            workspace_delete_actor="system:serviceaccount:dark-factory:dark-factory-control-plane",
            can_delete=self.can_delete,
        )
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_upstream_task_claim_is_mutated_to_single_pod_access(self):
        claim = {
            "metadata": {"name": "task-df-1", "labels": {
                "app.kubernetes.io/managed-by": "dark-factory",
                "dark-factory.io/instance": "dark-factory",
                "dark-factory.io/task-key": "DF-1",
            }},
            "spec": {"accessModes": ["ReadWriteOnce"], "storageClassName": "dark-factory-nfs"},
        }
        review = {"request": {
            "uid": "claim-request", "namespace": "dark-factory-sandboxes", "operation": "CREATE",
            "resource": {"group": "", "version": "v1", "resource": "persistentvolumeclaims"},
            "object": claim,
        }}
        result = await self.client.post("/mutate", json=review)
        self.assertEqual(result.status, 200)
        response = (await result.json())["response"]
        self.assertTrue(response["allowed"])
        self.assertEqual(json.loads(base64.b64decode(response["patch"])), [
            {"op": "replace", "path": "/spec/accessModes", "value": ["ReadWriteOncePod"]},
        ])

    async def test_unrelated_claim_is_left_unchanged(self):
        review = {"request": {
            "uid": "other-claim", "namespace": "dark-factory-sandboxes", "operation": "CREATE",
            "resource": {"group": "", "version": "v1", "resource": "persistentvolumeclaims"},
            "object": {"metadata": {"name": "user-scratch"}, "spec": {
                "accessModes": ["ReadWriteMany"], "storageClassName": "other-storage",
            }},
        }}
        result = await self.client.post("/mutate", json=review)
        self.assertEqual(result.status, 200)
        response = (await result.json())["response"]
        self.assertTrue(response["allowed"])
        self.assertNotIn("patch", response)

    async def test_unrelated_claim_update_is_left_unchanged(self):
        claim = {"metadata": {"name": "user-scratch"}, "spec": {
            "accessModes": ["ReadWriteMany"], "storageClassName": "homelab-smb",
        }}
        review = {"request": {
            "uid": "other-update", "namespace": "dark-factory-sandboxes", "operation": "UPDATE",
            "resource": {"group": "", "version": "v1", "resource": "persistentvolumeclaims"},
            "oldObject": copy.deepcopy(claim), "object": claim,
        }}
        for endpoint in ("/mutate", "/validate"):
            with self.subTest(endpoint=endpoint):
                result = await self.client.post(endpoint, json=review)
                response = (await result.json())["response"]
                self.assertTrue(response["allowed"])
                self.assertNotIn("patch", response)

    async def test_new_task_claim_with_old_storage_is_rejected(self):
        for storage in ("homelab-smb", "homelab-iscsi"):
            for endpoint in ("/mutate", "/validate"):
                with self.subTest(storage=storage, endpoint=endpoint):
                    review = {"request": {
                        "uid": "old-storage", "namespace": "dark-factory-sandboxes", "operation": "CREATE",
                        "resource": {"group": "", "version": "v1", "resource": "persistentvolumeclaims"},
                        "object": {"metadata": {"name": "task-df-1", "labels": {
                            "app.kubernetes.io/managed-by": "dark-factory",
                            "dark-factory.io/instance": "dark-factory", "dark-factory.io/task-key": "DF-1",
                        }}, "spec": {"accessModes": ["ReadWriteOnce"], "storageClassName": storage}},
                    }}
                    result = await self.client.post(endpoint, json=review)
                    self.assertFalse((await result.json())["response"]["allowed"])

    async def test_protected_claim_deletion_is_blocked_until_safe_cleanup_exists(self):
        for metadata in ({"name": "task-df-1"}, {"name": "other", "labels": {
                "dark-factory.io/instance": "dark-factory"}}):
            with self.subTest(metadata=metadata):
                review = {"request": {
                    "uid": "delete-task", "namespace": "dark-factory-sandboxes", "operation": "DELETE",
                    "resource": {"group": "", "version": "v1", "resource": "persistentvolumeclaims"},
                    "oldObject": {"metadata": metadata, "spec": {}}, "object": None,
                }}
                result = await self.client.post("/validate", json=review)
                self.assertFalse((await result.json())["response"]["allowed"])

    async def test_only_authorized_compatible_unused_workspace_can_be_deleted(self):
        claim = {"metadata": {"name": "task-df-1", "uid": "claim-uid", "labels": {
            "app.kubernetes.io/managed-by": "dark-factory", "dark-factory.io/instance": "dark-factory",
            "dark-factory.io/task-key": "DF-1",
        }}, "spec": {"storageClassName": "dark-factory-nfs", "accessModes": ["ReadWriteOncePod"]}}
        review = {"request": {
            "uid": "delete-task", "namespace": "dark-factory-sandboxes", "operation": "DELETE",
            "resource": {"group": "", "version": "v1", "resource": "persistentvolumeclaims"},
            "userInfo": {"username": "system:serviceaccount:dark-factory:dark-factory-control-plane"},
            "oldObject": claim, "object": None,
        }}
        result = await self.client.post("/validate", json=review)
        self.assertTrue((await result.json())["response"]["allowed"])
        self.can_delete.assert_awaited_once_with(claim)
        for mutation in (
            lambda r: r["request"]["userInfo"].update(username="other"),
            lambda r: r["request"]["oldObject"]["metadata"].pop("uid"),
            lambda r: r["request"]["oldObject"]["metadata"]["labels"].update({"dark-factory.io/instance": "other"}),
            lambda r: r["request"]["oldObject"]["metadata"]["labels"].update({"dark-factory.io/task-key": "DF-2"}),
            lambda r: r["request"]["oldObject"]["spec"].update(storageClassName="homelab-iscsi"),
            lambda r: r["request"]["oldObject"]["spec"].update(storageClassName="homelab-smb"),
            lambda r: r["request"]["oldObject"]["spec"].update(accessModes=["ReadWriteOnce"]),
        ):
            with self.subTest(mutation=mutation):
                bad = copy.deepcopy(review)
                mutation(bad)
                self.can_delete.reset_mock()
                result = await self.client.post("/validate", json=bad)
                self.assertFalse((await result.json())["response"]["allowed"])
                self.can_delete.assert_not_awaited()
        self.can_delete.return_value = False
        result = await self.client.post("/validate", json=review)
        self.assertFalse((await result.json())["response"]["allowed"])
        self.can_delete.side_effect = ValueError("Kubernetes API unavailable")
        result = await self.client.post("/validate", json=review)
        self.assertEqual(result.status, 400)

    async def test_unrelated_claim_deletion_is_unchanged_and_missing_old_object_denied(self):
        review = {"request": {
            "uid": "delete-other", "namespace": "dark-factory-sandboxes", "operation": "DELETE",
            "resource": {"group": "", "version": "v1", "resource": "persistentvolumeclaims"},
            "oldObject": {"metadata": {"name": "scratch"}, "spec": {}}, "object": None,
        }}
        result = await self.client.post("/validate", json=review)
        self.assertTrue((await result.json())["response"]["allowed"])
        review["request"]["oldObject"] = None
        result = await self.client.post("/validate", json=review)
        self.assertFalse((await result.json())["response"]["allowed"])

    async def test_valid_single_pod_claim_passes_validation(self):
        review = {"request": {
            "uid": "claim-request", "namespace": "dark-factory-sandboxes", "operation": "CREATE",
            "resource": {"group": "", "version": "v1", "resource": "persistentvolumeclaims"},
            "object": {"metadata": {"name": "task-df-1", "labels": {
                "app.kubernetes.io/managed-by": "dark-factory",
                "dark-factory.io/instance": "dark-factory",
                "dark-factory.io/task-key": "DF-1",
            }}, "spec": {"accessModes": ["ReadWriteOncePod"], "storageClassName": "dark-factory-nfs"}},
        }}
        result = await self.client.post("/validate", json=review)
        self.assertEqual(result.status, 200)
        self.assertTrue((await result.json())["response"]["allowed"])

    async def test_reconciliation_preserves_existing_single_pod_claim(self):
        metadata = {"name": "task-df-1", "labels": {
            "app.kubernetes.io/managed-by": "dark-factory",
            "dark-factory.io/instance": "dark-factory",
            "dark-factory.io/task-key": "DF-1",
        }}
        review = {"request": {
            "uid": "claim-update", "namespace": "dark-factory-sandboxes", "operation": "UPDATE",
            "resource": {"group": "", "version": "v1", "resource": "persistentvolumeclaims"},
            "oldObject": {"metadata": metadata, "spec": {
                "accessModes": ["ReadWriteOncePod"], "storageClassName": "dark-factory-nfs",
            }},
            "object": {"metadata": metadata, "spec": {
                "accessModes": ["ReadWriteOnce"], "storageClassName": "dark-factory-nfs",
            }},
        }}
        result = await self.client.post("/mutate", json=review)
        self.assertEqual(result.status, 200)
        response = (await result.json())["response"]
        self.assertTrue(response["allowed"])
        self.assertEqual(json.loads(base64.b64decode(response["patch"])), [
            {"op": "replace", "path": "/spec/accessModes", "value": ["ReadWriteOncePod"]},
        ])

    async def test_task_name_must_match_task_key(self):
        review = {"request": {
            "uid": "claim-request", "namespace": "dark-factory-sandboxes", "operation": "CREATE",
            "resource": {"group": "", "version": "v1", "resource": "persistentvolumeclaims"},
            "object": {"metadata": {"name": "task-df-2", "labels": {
                "app.kubernetes.io/managed-by": "dark-factory",
                "dark-factory.io/instance": "dark-factory",
                "dark-factory.io/task-key": "DF-1",
            }}, "spec": {"accessModes": ["ReadWriteOnce"], "storageClassName": "dark-factory-nfs"}},
        }}
        result = await self.client.post("/mutate", json=review)
        self.assertEqual(result.status, 200)
        self.assertFalse((await result.json())["response"]["allowed"])

    async def test_existing_incompatible_or_relabelled_claim_is_never_adopted(self):
        metadata = {"name": "task-df-1", "labels": {
            "app.kubernetes.io/managed-by": "dark-factory",
            "dark-factory.io/instance": "dark-factory",
            "dark-factory.io/task-key": "DF-1",
        }}
        safe = {"metadata": metadata, "spec": {
            "accessModes": ["ReadWriteOncePod"], "storageClassName": "dark-factory-nfs",
        }}
        cases = []
        for field, value in (("accessModes", ["ReadWriteOnce"]), ("storageClassName", "homelab-smb"),
                             ("storageClassName", "homelab-iscsi")):
            old = copy.deepcopy(safe)
            old["spec"][field] = value
            cases.append((old, copy.deepcopy(safe)))
        removed = copy.deepcopy(safe)
        removed["metadata"]["labels"] = {}
        cases.append((copy.deepcopy(safe), removed))
        for old, new in cases:
            with self.subTest(old=old, new=new):
                review = {"request": {
                    "uid": "claim-update", "namespace": "dark-factory-sandboxes", "operation": "UPDATE",
                    "resource": {"group": "", "version": "v1", "resource": "persistentvolumeclaims"},
                    "oldObject": old, "object": new,
                }}
                result = await self.client.post("/mutate", json=review)
                self.assertEqual(result.status, 200)
                self.assertFalse((await result.json())["response"]["allowed"])

    async def test_wrong_namespace_instance_storage_or_access_fails_closed(self):
        safe = {"metadata": {"name": "task-df-1", "labels": {
            "app.kubernetes.io/managed-by": "dark-factory",
            "dark-factory.io/instance": "dark-factory",
            "dark-factory.io/task-key": "DF-1",
        }}, "spec": {"accessModes": ["ReadWriteOncePod"], "storageClassName": "dark-factory-nfs"}}
        review = {"request": {
            "uid": "claim-request", "namespace": "dark-factory-sandboxes", "operation": "CREATE",
            "resource": {"group": "", "version": "v1", "resource": "persistentvolumeclaims"},
            "object": safe,
        }}
        for kind in ("namespace", "instance", "storage", "access"):
            with self.subTest(kind=kind):
                bad = copy.deepcopy(review)
                request = bad["request"]
                if kind == "namespace":
                    request["namespace"] = "other"
                elif kind == "instance":
                    request["object"]["metadata"]["labels"]["dark-factory.io/instance"] = "other"
                elif kind == "storage":
                    request["object"]["spec"]["storageClassName"] = "homelab-smb"
                else:
                    request["object"]["spec"]["accessModes"] = ["ReadWriteOnce"]
                result = await self.client.post("/validate", json=bad)
                self.assertEqual(result.status, 200)
                self.assertFalse((await result.json())["response"]["allowed"])

    async def test_workspace_policy_without_configuration_denies_claims(self):
        client = TestClient(TestServer(admission_app("dark-factory-sandboxes", "desktop")))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        review = {"request": {
            "uid": "claim-request", "namespace": "dark-factory-sandboxes", "operation": "CREATE",
            "resource": {"group": "", "version": "v1", "resource": "persistentvolumeclaims"},
            "object": {"metadata": {"name": "task-df-1"}, "spec": {}},
        }}
        for endpoint in ("/mutate", "/validate"):
            with self.subTest(endpoint=endpoint):
                result = await client.post(endpoint, json=review)
                self.assertEqual(result.status, 200)
                self.assertFalse((await result.json())["response"]["allowed"])


class WorkspaceWebhookConfigurationTests(unittest.TestCase):
    def test_pvc_webhooks_are_fail_closed_and_namespace_scoped(self):
        root = Path(__file__).resolve().parents[3]
        path = root / "apps/dark-factory-wake/admission-webhooks.yaml"
        configurations = list(yaml.safe_load_all(path.read_text()))
        for kind, endpoint in (("MutatingWebhookConfiguration", "/mutate"),
                               ("ValidatingWebhookConfiguration", "/validate")):
            with self.subTest(kind=kind):
                config = next(item for item in configurations if item["kind"] == kind)
                pvc_hooks = [hook for hook in config["webhooks"] if any(
                    "persistentvolumeclaims" in rule["resources"]
                    and set(rule["operations"]) >= {"CREATE", "UPDATE"}
                    for rule in hook["rules"]
                )]
                self.assertEqual(len(pvc_hooks), 1)
                hook = pvc_hooks[0]
                self.assertEqual(hook["failurePolicy"], "Fail")
                self.assertEqual(hook["clientConfig"]["service"]["path"], endpoint)
                if kind == "ValidatingWebhookConfiguration":
                    self.assertTrue(any("DELETE" in rule["operations"] and "persistentvolumeclaims" in rule["resources"]
                                        for rule in hook["rules"]))
                self.assertEqual(hook["namespaceSelector"]["matchLabels"], {
                    "kubernetes.io/metadata.name": "dark-factory-sandboxes",
                })


class WorkspaceProductionWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_configured_storage_and_instance_reach_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(yaml.safe_dump({"nodeGroups": [{
                "name": "desktop", "listenPort": 8080, "wolMac": "test",
                "sandboxNamespace": "dark-factory-sandboxes", "sandboxInstance": "dark-factory",
                "sandboxWorkspaceStorageClass": "dark-factory-nfs", "kubernetesNode": "desktop",
                "sandboxWorkspaceDeleteActor": "system:serviceaccount:dark-factory:dark-factory-control-plane",
            }]}))
            cfg = load_config(str(path)).node_groups[0]
        monitor = SimpleNamespace(can_bind=None, can_release=None, can_delete_workspace=AsyncMock(return_value=True))
        client = TestClient(TestServer(build_admission_app(SimpleNamespace(cfg=cfg, _sandbox_monitor=monitor))))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        result = await client.post("/mutate", json={"request": {
            "uid": "production-wiring", "namespace": "dark-factory-sandboxes", "operation": "CREATE",
            "resource": {"group": "", "version": "v1", "resource": "persistentvolumeclaims"},
            "object": {"metadata": {"name": "task-df-1", "labels": {
                "app.kubernetes.io/managed-by": "dark-factory",
                "dark-factory.io/instance": "dark-factory", "dark-factory.io/task-key": "DF-1",
            }}, "spec": {"accessModes": ["ReadWriteOnce"], "storageClassName": "dark-factory-nfs"}},
        }})
        response = (await result.json())["response"]
        self.assertTrue(response["allowed"])
        self.assertEqual(json.loads(base64.b64decode(response["patch"]))[0]["value"], ["ReadWriteOncePod"])
        claim = {"metadata": {"name": "task-df-1", "uid": "claim-uid", "labels": {
            "app.kubernetes.io/managed-by": "dark-factory", "dark-factory.io/instance": "dark-factory",
            "dark-factory.io/task-key": "DF-1",
        }}, "spec": {"accessModes": ["ReadWriteOncePod"], "storageClassName": "dark-factory-nfs"}}
        result = await client.post("/validate", json={"request": {
            "uid": "production-delete", "namespace": "dark-factory-sandboxes", "operation": "DELETE",
            "resource": {"group": "", "version": "v1", "resource": "persistentvolumeclaims"},
            "userInfo": {"username": "system:serviceaccount:dark-factory:dark-factory-control-plane"},
            "oldObject": claim, "object": None,
        }})
        self.assertTrue((await result.json())["response"]["allowed"])
        monitor.can_delete_workspace.assert_awaited_once_with(claim)
