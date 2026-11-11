import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class CoderDeploymentTests(unittest.TestCase):
    def test_control_plane_uses_image_server_entrypoint(self):
        deployment = (ROOT / "apps/coder/deployment.yaml").read_text()
        containers = deployment.split("      containers:\n", 1)[1]
        coder = containers.split("        - name: coder\n", 1)[1].split("\n      volumes:", 1)[0]
        self.assertRegex(coder, r"(?m)^          image: ghcr\.io/coder/coder:")
        self.assertNotRegex(coder, r"(?m)^          (?:command|args):",
                            "the image entrypoint already runs coder server")


if __name__ == "__main__":
    unittest.main()
