"""Opt-in render check; downloads only the pinned upstream CSI manifests."""

import os
import subprocess
import unittest
from pathlib import Path

import yaml


@unittest.skipUnless(os.environ.get("DF_TEST_NFS_RENDER"), "set DF_TEST_NFS_RENDER for the networked render test")
class NfsRenderTests(unittest.TestCase):
    def test_rendered_driver_has_restricted_placement_and_no_secret_or_snapshot_access(self):
        root = Path(__file__).resolve().parents[4]
        result = subprocess.run(["kubectl", "kustomize", str(root / "apps/nfs-csi-driver")],
                                check=True, capture_output=True, text=True, timeout=90)
        objects = list(yaml.safe_load_all(result.stdout))

        def find(kind, name):
            return next(obj for obj in objects if obj["kind"] == kind and obj["metadata"]["name"] == name)

        controller = find("Deployment", "csi-nfs-controller")["spec"]["template"]["spec"]
        self.assertEqual(controller["nodeSelector"]["kubernetes.io/hostname"], "homelab-amd")
        self.assertNotIn("csi-snapshotter", [container["name"] for container in controller["containers"]])
        node = find("DaemonSet", "csi-nfs-node")["spec"]["template"]["spec"]
        terms = node["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"]
        self.assertEqual(terms[0]["matchExpressions"][0]["values"], ["homelab-amd-desktop", "homelab-gpu"])
        role = find("ClusterRole", "nfs-external-provisioner-role")
        self.assertFalse(any("secrets" in rule["resources"] or
                             "snapshot.storage.k8s.io" in rule["apiGroups"] for rule in role["rules"]))
        self.assertEqual(find("CSIDriver", "nfs.csi.k8s.io")["spec"]["fsGroupPolicy"], "File")


if __name__ == "__main__":
    unittest.main()
