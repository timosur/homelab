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

    def test_workspace_gateway_ingress_is_scoped(self):
        path = ROOT / "networking/cilium-network-policies/home"
        policy = (path / "coder-workspace.yaml").read_text()
        self.assertIn("gateway.envoyproxy.io/owning-gateway-name: envoy-gateway-home", policy)
        self.assertIn("k8s:io.kubernetes.pod.namespace: coder", policy)
        self.assertIn("app.kubernetes.io/name: coder-workspace", policy)
        self.assertIn("app.kubernetes.io/component: development-workspace", policy)
        self.assertRegex(policy, r'(?m)^            - port: "10443"$')
        self.assertEqual(policy.count("fromEndpoints:"), 1)
        self.assertNotIn("fromEntities:", policy)
        self.assertNotIn("fromCIDR:", policy)
        self.assertIn("- coder-workspace.yaml", (path / "kustomization.yaml").read_text())


if __name__ == "__main__":
    unittest.main()
