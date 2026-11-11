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

    def test_oidc_only_login_keeps_github_external_auth(self):
        config = (ROOT / "apps/coder/configmap.yaml").read_text()
        self.assertRegex(config, r'(?m)^  CODER_DISABLE_PASSWORD_AUTH: "true"$')
        self.assertRegex(config, r'(?m)^  CODER_OAUTH2_GITHUB_DEFAULT_PROVIDER_ENABLE: "false"$')
        self.assertRegex(config, r"(?m)^  CODER_EXTERNAL_AUTH_0_ID: github$")
        self.assertRegex(config, r"(?m)^  CODER_EXTERNAL_AUTH_0_TYPE: github$")

        deployment = (ROOT / "apps/coder/deployment.yaml").read_text()
        self.assertRegex(deployment, r'(?m)^        coder\.home\.timosur\.com/oidc-only: "enabled"$')


if __name__ == "__main__":
    unittest.main()
