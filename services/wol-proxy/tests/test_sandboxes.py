import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml

from wol_proxy.config import NodeGroupConfig, optional_boolean
from wol_proxy.admission import GATE, RELEASE_TOKEN
from wol_proxy.proxy import NodeGroupProxy
from wol_proxy.sandboxes import SandboxMonitor
from wol_proxy.workspace import WorkspaceBusy


class SandboxMonitorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with patch.dict(os.environ, {"KUBERNETES_SERVICE_HOST": "kubernetes"}):
            self.monitor = SandboxMonitor("sandboxes", "factory", "desktop")

    async def test_empty_workloads_are_inactive(self):
        self.monitor._get = AsyncMock(return_value={"items": []})
        self.assertFalse(await self.monitor.active())
        self.monitor._get.assert_awaited_once_with(
            "/api/v1/namespaces/sandboxes/pods",
            params={"labelSelector": "dark-factory.io/instance=factory"},
        )

    async def test_pending_or_deleting_plain_pod_is_active(self):
        self.monitor._get = AsyncMock(return_value={"items": [{"metadata": {"deletionTimestamp": "now"}}]})
        self.assertTrue(await self.monitor.active())

    async def test_sleep_checks_all_namespace_pods_without_label_filter(self):
        self.monitor._get = AsyncMock(return_value={"items": [{}]})
        self.assertTrue(await self.monitor.any_workload())
        self.monitor._get.assert_awaited_once_with("/api/v1/namespaces/sandboxes/pods")
        self.monitor._get = AsyncMock(return_value={"items": []})
        self.assertFalse(await self.monitor.any_workload())

    async def test_incomplete_response_is_not_treated_as_inactive(self):
        for response in ({}, {"items": [], "metadata": {"continue": "next-page"}}):
            with self.subTest(response=response):
                self.monitor._get = AsyncMock(return_value=response)
                with self.assertRaises(ValueError):
                    await self.monitor.active()
                with self.assertRaises(ValueError):
                    await self.monitor.any_workload()

    async def test_api_outage_never_reports_no_workload(self):
        self.monitor._get = AsyncMock(side_effect=OSError("API unavailable"))
        with self.assertRaises(OSError):
            await self.monitor.active()
        with self.assertRaises(OSError):
            await self.monitor.any_workload()

    async def test_workspace_delete_requires_complete_pod_list_without_claim_users(self):
        claim = {"metadata": {"name": "task-df-1", "uid": "claim-uid"}}
        cases = [
            ([], True),
            ([{"spec": {"volumes": [{"name": "x", "persistentVolumeClaim": {"claimName": "task-other"}}]}}], True),
            ([{"metadata": {"deletionTimestamp": "now"}, "spec": {"volumes": [
                {"name": "x", "persistentVolumeClaim": {"claimName": "task-df-1"}},
            ]}}], False),
            ([{"spec": {"volumes": [{"name": "ephemeral", "ephemeral": {}}]}}], False),
        ]
        for items, allowed in cases:
            with self.subTest(items=items):
                self.monitor._get = AsyncMock(return_value={"items": items})
                self.assertEqual(await self.monitor.can_delete_workspace(claim), allowed)
                self.monitor._get.assert_awaited_once_with("/api/v1/namespaces/sandboxes/pods")
        for response in ({}, {"items": [], "metadata": {"continue": "next-page"}},
                         {"items": [{"spec": {"volumes": [{"name": "x", "persistentVolumeClaim": {}}]}}]},
                         {"items": [{"spec": {"volumes": "bad"}}]}):
            with self.subTest(response=response):
                self.monitor._get = AsyncMock(return_value=response)
                with self.assertRaises(ValueError):
                    await self.monitor.can_delete_workspace(claim)
        self.monitor._get = AsyncMock(side_effect=OSError("API unavailable"))
        with self.assertRaises(OSError):
            await self.monitor.can_delete_workspace(claim)

    async def test_node_readiness(self):
        self.monitor._get = AsyncMock(return_value={
            "status": {"conditions": [{"type": "Ready", "status": "True"}]}
        })
        self.assertTrue(await self.monitor.node_ready())
        self.monitor._get = AsyncMock(return_value={"status": {}})
        with self.assertRaises(ValueError):
            await self.monitor.node_ready()
        self.monitor._get = AsyncMock(return_value={"status": {"conditions": [None]}})
        with self.assertRaises(ValueError):
            await self.monitor.node_ready()

    async def test_releases_only_matching_gate_with_uid_and_version_checks(self):
        self.monitor._get = AsyncMock(return_value={"items": [{
            "metadata": {"name": "run-1", "uid": "pod-uid", "resourceVersion": "23"},
            "spec": {"schedulingGates": [{"name": "other.example/gate"}, {"name": GATE}]},
        }]})
        self.monitor._patch = AsyncMock()
        self.monitor.workspace_reserve = AsyncMock()
        await self.monitor.release_gates(2, "owner", "anchor")
        path, patch_body = self.monitor._patch.await_args.args
        self.assertEqual(path, "/api/v1/namespaces/sandboxes/pods/run-1")
        self.assertEqual(patch_body, [
            {"op": "test", "path": "/metadata/uid", "value": "pod-uid"},
            {"op": "test", "path": "/metadata/resourceVersion", "value": "23"},
            {"op": "test", "path": "/spec/schedulingGates/1/name", "value": GATE},
            {"op": "add", "path": "/metadata/annotations", "value": {RELEASE_TOKEN: "2:owner:anchor"}},
            {"op": "remove", "path": "/spec/schedulingGates/1"},
        ])

    async def test_busy_workspace_does_not_block_other_tasks(self):
        self.monitor._get = AsyncMock(return_value={"items": [
            {"metadata": {"name": "run-1", "uid": "pod-1", "resourceVersion": "1"},
             "spec": {"schedulingGates": [{"name": GATE}]}},
            {"metadata": {"name": "run-2", "uid": "pod-2", "resourceVersion": "2"},
             "spec": {"schedulingGates": [{"name": GATE}]}},
        ]})
        self.monitor.workspace_reserve = AsyncMock(side_effect=[WorkspaceBusy("old pod exists"), None])
        self.monitor._patch = AsyncMock()
        await self.monitor.release_gates(2, "owner", "anchor")
        self.monitor._patch.assert_awaited_once()
        self.assertEqual(self.monitor._patch.await_args.args[0], "/api/v1/namespaces/sandboxes/pods/run-2")

    async def test_gated_pod_cannot_be_bound(self):
        self.monitor._get = AsyncMock(return_value={
            "metadata": {"name": "run-1", "uid": "pod-uid"}, "spec": {"schedulingGates": [{"name": GATE}]}
        })
        self.assertFalse(await self.monitor.can_bind("run-1", "pod-uid"))
        self.monitor._get = AsyncMock(return_value={"metadata": {"name": "run-1", "uid": "pod-uid"}, "spec": {}})
        self.assertFalse(await self.monitor.can_bind("run-1", "pod-uid"))
        self.assertFalse(await self.monitor.can_bind("run-1", "old-uid"))
        self.monitor.workspace_check = AsyncMock(return_value=True)
        self.assertTrue(await self.monitor.can_bind("run-1", "pod-uid"))

    async def test_admission_checks_require_fail_closed_rules_and_ca(self):
        def hook(path, rules):
            return {"webhooks": [{
                "failurePolicy": "Fail",
                "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "sandboxes"}},
                "clientConfig": {"caBundle": "Y2E=", "service": {
                    "name": "dark-factory-admission", "namespace": "wol-proxy", "path": path, "port": 443,
                }},
                "rules": rules,
            }]}
        def rule(resources, operations):
            return {"apiGroups": [""], "apiVersions": ["v1"], "resources": resources,
                    "operations": operations, "scope": "Namespaced"}
        mutating_pvc = rule(["persistentvolumeclaims"], ["CREATE", "UPDATE"])
        validating_pvc = rule(["persistentvolumeclaims"], ["CREATE", "UPDATE", "DELETE"])
        mutating = hook("/mutate", [rule(["pods"], ["CREATE"]), mutating_pvc])
        validating = hook("/validate", [rule(["pods"], ["CREATE", "UPDATE"]),
                                        rule(["pods/binding", "bindings"], ["CREATE"]), validating_pvc])
        self.monitor._get = AsyncMock(side_effect=[mutating, validating])
        await self.monitor.verify_admission()
        for config, pvc_rule in ((mutating, mutating_pvc), (validating, validating_pvc)):
            with self.subTest(missing_pvc=config):
                config["webhooks"][0]["rules"].remove(pvc_rule)
                self.monitor._get = AsyncMock(side_effect=[mutating, validating])
                with self.assertRaises(ValueError):
                    await self.monitor.verify_admission()
                config["webhooks"][0]["rules"].append(pvc_rule)
        validating["webhooks"][0]["failurePolicy"] = "Ignore"
        self.monitor._get = AsyncMock(side_effect=[mutating, validating])
        with self.assertRaises(ValueError):
            await self.monitor.verify_admission()

    async def test_does_not_release_gate_on_deleting_pod(self):
        self.monitor._get = AsyncMock(return_value={"items": [{
            "metadata": {"deletionTimestamp": "now"}, "spec": {"schedulingGates": [{"name": GATE}]},
        }]})
        self.monitor._patch = AsyncMock()
        await self.monitor.release_gates(2, "owner", "anchor")
        self.monitor._patch.assert_not_awaited()

    async def test_release_proof_must_match_live_reservation(self):
        self.monitor._get = AsyncMock(return_value={
            "metadata": {"uid": "anchor", "resourceVersion": "7"},
            "data": {"epoch": "2", "owner": "owner", "phase": "releasing", "http_active": "0"},
        })
        pod = {"metadata": {"annotations": {RELEASE_TOKEN: "2:owner:anchor"}}}
        self.assertFalse(await self.monitor.can_release(pod))
        self.monitor.workspace_check = AsyncMock(return_value=True)
        self.assertTrue(await self.monitor.can_release(pod))
        pod["metadata"]["annotations"][RELEASE_TOKEN] = "1:owner:anchor"
        self.assertFalse(await self.monitor.can_release(pod))


class NodeGroupSandboxTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        config = NodeGroupConfig(
            name="desktop", listen_port=8080, wol_mac="00:11:22:33:44:55",
            sandbox_namespace="sandboxes", sandbox_instance="factory",
            kubernetes_node="desktop",
        )
        with patch.dict(os.environ, {"KUBERNETES_SERVICE_HOST": "kubernetes"}):
            self.proxy = NodeGroupProxy(config)

    async def test_pending_sandbox_wakes_unready_node(self):
        self.proxy._sandbox_monitor.active = AsyncMock(return_value=True)
        self.proxy._sandbox_monitor.node_ready = AsyncMock(return_value=False)
        self.proxy.wake_and_wait = AsyncMock()
        with patch("wol_proxy.proxy.asyncio.sleep", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await self.proxy.sandbox_watcher()
        self.proxy.wake_and_wait.assert_awaited_once()

    async def test_unknown_workload_state_does_not_wake(self):
        self.proxy._sandbox_monitor.active = AsyncMock(side_effect=OSError("API unavailable"))
        self.proxy.wake_and_wait = AsyncMock()
        with patch("wol_proxy.proxy.asyncio.sleep", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await self.proxy.sandbox_watcher()
        self.proxy.wake_and_wait.assert_not_awaited()

    async def test_uncoordinated_wake_never_releases_gates(self):
        self.proxy.cfg.sandbox_admission_enabled = True
        self.proxy._sandbox_monitor.active = AsyncMock(return_value=True)
        self.proxy._sandbox_monitor.node_ready = AsyncMock(return_value=True)
        self.proxy._sandbox_monitor.release_gates = AsyncMock()
        with patch("wol_proxy.proxy.asyncio.sleep", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await self.proxy.sandbox_watcher()
        self.proxy._sandbox_monitor.release_gates.assert_not_awaited()

    async def test_unready_node_does_not_release_gates(self):
        self.proxy.cfg.sandbox_admission_enabled = True
        self.proxy._sandbox_monitor.active = AsyncMock(return_value=True)
        self.proxy._sandbox_monitor.node_ready = AsyncMock(return_value=False)
        self.proxy._sandbox_monitor.release_gates = AsyncMock()
        self.proxy.wake_and_wait = AsyncMock()
        with patch("wol_proxy.proxy.asyncio.sleep", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await self.proxy.sandbox_watcher()
        self.proxy._sandbox_monitor.release_gates.assert_not_awaited()

    async def test_sandbox_integration_never_suspends_without_interlock(self):
        self.proxy.suspend = AsyncMock()
        with patch("wol_proxy.proxy.asyncio.sleep", side_effect=[None, asyncio.CancelledError]):
            with self.assertRaises(asyncio.CancelledError):
                await self.proxy.idle_watcher()
        self.proxy.suspend.assert_not_awaited()

    async def test_unconfigured_node_group_keeps_existing_idle_sleep(self):
        proxy = NodeGroupProxy(NodeGroupConfig(
            name="desktop", listen_port=8080, wol_mac="00:11:22:33:44:55",
            idle_timeout_minutes=0,
        ))
        proxy.check_node_health = AsyncMock(return_value=True)
        proxy.suspend = AsyncMock()
        with patch("wol_proxy.proxy.asyncio.sleep", side_effect=[None, asyncio.CancelledError]):
            with self.assertRaises(asyncio.CancelledError):
                await proxy.idle_watcher()
        proxy.suspend.assert_awaited_once()

    def test_partial_sandbox_configuration_is_rejected(self):
        config = NodeGroupConfig(
            name="desktop", listen_port=8080, wol_mac="00:11:22:33:44:55",
            sandbox_namespace="sandboxes",
        )
        with self.assertRaises(ValueError):
            NodeGroupProxy(config)

    def test_interlock_flags_reject_string_booleans(self):
        with self.assertRaises(ValueError):
            optional_boolean({"sandboxPowerInterlockEnabled": "false"}, "sandboxPowerInterlockEnabled")


class PlainPodRbacTests(unittest.TestCase):
    def test_monitor_has_pod_and_claim_access_without_sandbox_crd_access(self):
        root = Path(__file__).resolve().parents[3]
        resources = list(yaml.safe_load_all((root / "apps/dark-factory-wake/rbac.yaml").read_text()))
        role = next(item for item in resources if item.get("kind") == "Role"
                    and item["metadata"]["name"] == "dark-factory-wake-reader")
        self.assertFalse(any("agents.x-k8s.io" in rule["apiGroups"] for rule in role["rules"]))
        for resource, required in (("pods", {"get", "list", "patch"}),
                                   ("persistentvolumeclaims", {"get"})):
            self.assertTrue(any("" in rule["apiGroups"] and resource in rule["resources"]
                                and required.issubset(set(rule["verbs"])) for rule in role["rules"]))


if __name__ == "__main__":
    unittest.main()
