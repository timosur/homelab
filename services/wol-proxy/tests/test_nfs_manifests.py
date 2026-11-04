import unittest
from pathlib import Path

import yaml


class NfsManifestTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[3]
        self.directory = self.root / "apps/nfs-csi-driver"

    def load(self, name):
        return yaml.safe_load((self.directory / name).read_text())

    def test_dedicated_nondefault_storage_class(self):
        storage = self.load("storage-class.yaml")
        self.assertEqual(storage["metadata"]["name"], "dark-factory-nfs")
        self.assertNotEqual(storage["metadata"].get("annotations", {}).get("storageclass.kubernetes.io/is-default-class"), "true")
        self.assertEqual(storage["provisioner"], "nfs.csi.k8s.io")
        self.assertEqual(storage["parameters"]["server"], "192.168.1.26")
        self.assertEqual(storage["parameters"]["share"], "/volume1/k3s_nfs")
        self.assertEqual(storage["reclaimPolicy"], "Delete")
        self.assertIn("nfsvers=4.1", storage["mountOptions"])

    def test_controller_and_node_placement_is_restricted(self):
        controller = self.load("controller-patch.yaml")
        self.assertEqual(controller["spec"]["template"]["spec"]["nodeSelector"]["kubernetes.io/hostname"], "homelab-amd")
        node = self.load("node-patch.yaml")
        terms = node["spec"]["template"]["spec"]["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"]
        self.assertEqual(terms, [{"matchExpressions": [{"key": "kubernetes.io/hostname", "operator": "In",
                                                       "values": ["homelab-amd-desktop", "homelab-gpu"]}]}])

    def test_application_requires_manual_sync(self):
        app = yaml.safe_load((self.root / "apps/_argocd/nfs-csi-driver-app.yaml").read_text())
        self.assertNotIn("automated", app["spec"].get("syncPolicy", {}))
        self.assertEqual(app["spec"]["source"]["path"], "apps/nfs-csi-driver")
