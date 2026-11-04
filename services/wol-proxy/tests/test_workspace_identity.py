import copy
import json
import unittest
from unittest.mock import AsyncMock

from aiohttp import ClientResponseError

from wol_proxy.workspace import WorkspaceVerifier, workspace_identity


def fixtures():
    labels = {"app.kubernetes.io/managed-by": "dark-factory",
              "dark-factory.io/instance": "factory", "dark-factory.io/task-key": "DF-1"}
    pod = {"metadata": {"namespace": "sandboxes", "name": "run-df-1-1", "uid": "pod-uid",
                        "labels": copy.deepcopy(labels)},
           "spec": {"securityContext": {"runAsNonRoot": True, "runAsUser": 1000, "runAsGroup": 1000,
                                        "fsGroup": 1000, "fsGroupChangePolicy": "OnRootMismatch"},
                    "volumes": [{"name": "task-data", "persistentVolumeClaim": {"claimName": "task-df-1"}}]}}
    claim = {"metadata": {"namespace": "sandboxes", "name": "task-df-1", "uid": "claim-uid",
                          "labels": copy.deepcopy(labels)},
             "spec": {"storageClassName": "dark-factory-nfs", "accessModes": ["ReadWriteOncePod"]}}
    return pod, claim


class WorkspaceIdentityTests(unittest.TestCase):
    def identity(self, pod, claim):
        return workspace_identity(pod, claim, "sandboxes", "factory", "dark-factory-nfs")

    def test_pinned_pod_and_claim_identity(self):
        pod, claim = fixtures()
        pod["spec"]["volumes"].extend([
            {"name": "factory-runtime", "image": {"reference": "example.invalid/runtime"}},
            {"name": "tls", "csi": {"driver": "csi.cert-manager.io", "readOnly": True}},
            {"name": "tmpfs", "emptyDir": {"medium": "Memory"}},
        ])
        self.assertEqual(self.identity(pod, claim), ("task-df-1", "claim-uid", "run-df-1-1", "pod-uid"))

    def test_invalid_or_ambiguous_identities_are_rejected(self):
        changes = (
            lambda p, c: p["metadata"].update(uid=""),
            lambda p, c: c["metadata"].update(uid=""),
            lambda p, c: p["metadata"].update(namespace="other"),
            lambda p, c: c["metadata"].update(namespace="other"),
            lambda p, c: p["metadata"].update(deletionTimestamp="now"),
            lambda p, c: c["metadata"].update(deletionTimestamp="now"),
            lambda p, c: p["metadata"]["labels"].update({"dark-factory.io/instance": "other"}),
            lambda p, c: p["metadata"]["labels"].update({"dark-factory.io/task-key": "DF-2"}),
            lambda p, c: c["metadata"]["labels"].update({"dark-factory.io/task-key": "DF-2"}),
            lambda p, c: c["metadata"].update(name="task-df-2"),
            lambda p, c: c["spec"].update(storageClassName="homelab-iscsi"),
            lambda p, c: c["spec"].update(accessModes=["ReadWriteOnce"]),
            lambda p, c: p["spec"].update(volumes=[]),
            lambda p, c: p["spec"]["volumes"].append({"name": "other", "persistentVolumeClaim": {"claimName": "task-df-1"}}),
            lambda p, c: p["spec"]["volumes"][0]["persistentVolumeClaim"].update(claimName="task-df-2"),
            lambda p, c: p["spec"]["volumes"].append({"name": "bypass", "nfs": {"server": "nas", "path": "/volume1/k3s_nfs"}}),
            lambda p, c: p["spec"]["volumes"].append({"name": "bypass", "hostPath": {"path": "/"}}),
            lambda p, c: p["spec"]["volumes"].append({"name": "bypass", "csi": {"driver": "nfs.csi.k8s.io"}}),
            lambda p, c: p["spec"]["volumes"].append({"name": "bypass", "ephemeral": {}}),
            lambda p, c: p["spec"]["volumes"].append({"name": "bypass", "image": {}, "nfs": {"server": "nas"}}),
            lambda p, c: p["spec"]["securityContext"].pop("fsGroupChangePolicy"),
            lambda p, c: p["spec"]["securityContext"].update(fsGroupChangePolicy="Always"),
            lambda p, c: p["spec"]["securityContext"].update(fsGroup=42),
            lambda p, c: p["spec"]["securityContext"].update(runAsUser=0),
            lambda p, c: p["spec"]["securityContext"].update(runAsGroup=0),
            lambda p, c: p["spec"]["securityContext"].update(runAsNonRoot=False),
            lambda p, c: p["spec"].pop("securityContext"),
            lambda p, c: p["spec"].update(containers=[{"name": "agent", "securityContext": {"runAsUser": 0}}]),
        )
        for index, change in enumerate(changes):
            with self.subTest(case=index):
                pod, claim = fixtures()
                change(pod, claim)
                with self.assertRaises(ValueError):
                    self.identity(pod, claim)


class WorkspaceVerifierTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pod, self.claim = fixtures()
        self.state = {"metadata": {"uid": "anchor", "resourceVersion": "12"},
                      "data": {"epoch": "3", "owner": "coordinator", "phase": "releasing", "http_active": "0",
                               "workspace_records": json.dumps({"task-df-1": {
                                   "claim_uid": "claim-uid", "pod_name": "run-df-1-1", "pod_uid": "pod-uid",
                                   "possible_nodes": ["desktop", "gpu"],
                               }})}}
        self.client = type("Api", (), {})()

        async def get(path):
            if path == "/api/v1/namespaces/sandboxes/persistentvolumeclaims/task-df-1":
                return copy.deepcopy(self.claim)
            if path == "/api/v1/namespaces/wol-proxy/configmaps/dark-factory-interlock":
                return copy.deepcopy(self.state)
            raise AssertionError(f"unexpected path: {path}")

        self.client._get = AsyncMock(side_effect=get)
        self.client._put = AsyncMock()
        self.verifier = WorkspaceVerifier(self.client, "sandboxes", "factory", "dark-factory-nfs", ("desktop", "gpu"))

    async def test_matching_reservation_allows_read_only_check(self):
        self.assertTrue(await self.verifier.check(self.pod))
        self.client._put.assert_not_awaited()

    async def test_replaced_claim_and_absent_reservation_do_not_match(self):
        self.claim["metadata"]["uid"] = "replacement"
        self.assertFalse(await self.verifier.check(self.pod))
        self.claim["metadata"]["uid"] = "claim-uid"
        self.state["data"]["workspace_records"] = "{}"
        self.assertFalse(await self.verifier.check(self.pod))
        self.client._put.assert_not_awaited()

    async def test_invalid_pod_never_authorizes_a_workspace(self):
        self.pod["metadata"]["labels"]["dark-factory.io/task-key"] = "../../invalid"
        with self.assertRaises(ValueError):
            await self.verifier.check(self.pod)
        self.client._get.assert_not_awaited()

    async def test_previous_pod_uid_must_be_absent_or_replaced(self):
        previous = {"pod_name": "old-run", "pod_uid": "old-uid"}
        self.client._get = AsyncMock(return_value={"metadata": {
            "name": "old-run", "namespace": "sandboxes", "uid": "old-uid",
        }})
        self.assertFalse(await self.verifier.previous_gone(previous))
        self.client._get.return_value["metadata"]["uid"] = "new-uid"
        self.assertTrue(await self.verifier.previous_gone(previous))
        self.client._get.side_effect = ClientResponseError(None, (), status=404)
        self.assertTrue(await self.verifier.previous_gone(previous))

    async def test_previous_pod_unknown_or_invalid_state_does_not_authorize_handoff(self):
        previous = {"pod_name": "old-run", "pod_uid": "old-uid"}
        for status in (403, 500):
            with self.subTest(status=status):
                self.client._get = AsyncMock(side_effect=ClientResponseError(None, (), status=status))
                with self.assertRaises(ClientResponseError):
                    await self.verifier.previous_gone(previous)
        for result in ({}, {"metadata": {"name": "old-run", "namespace": "sandboxes"}},
                       {"metadata": {"name": "other", "namespace": "sandboxes", "uid": "new"}}):
            with self.subTest(result=result):
                self.client._get = AsyncMock(return_value=result)
                with self.assertRaises(ValueError):
                    await self.verifier.previous_gone(previous)
