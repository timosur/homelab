import importlib.util
import contextlib
import io
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "setup_authentik_homelab.py"
spec = importlib.util.spec_from_file_location("setup_authentik_homelab", SCRIPT)
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class OidcProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.args = SimpleNamespace(
            base_url="https://auth.example.invalid", api_token_env="TEST_API_TOKEN", namespace="authentik",
            server_selector="app=authentik-server", allowed_group="board-member", app_name="Coder",
            client_id="coder", client_secret_env="TEST_CLIENT_SECRET", redirect_uri="https://example.invalid/callback",
            slug="coder", launch_url="https://example.invalid/", keyvault_client_id_key="client-id",
            keyvault_client_secret_key="client-secret", secret_namespace="coder", secret_name="coder-oidc",
            secret_key="client-secret",
        )
        self.events = []
        self.existing_provider = True
        self.fail_provider = False

    def api(self, base, method, path, data=None):
        self.events.append((method, path))
        if self.fail_provider and method in ("POST", "PATCH") and path.startswith("/api/v3/providers/oauth2/"):
            raise RuntimeError("provider unavailable")
        if path == "/api/v3/flows/instances/":
            return {"results": [{"slug": "default-provider-authorization-implicit-consent", "pk": "auth"},
                                {"slug": "default-provider-invalidation-flow", "pk": "invalidate"}]}
        if path.startswith("/api/v3/crypto/certificatekeypairs/"):
            return {"results": [{"pk": "certificate"}]}
        if path == "/api/v3/propertymappings/provider/scope/":
            return {"results": [{"scope_name": name, "pk": name} for name in ("openid", "profile", "email")]}
        if path == "/api/v3/providers/oauth2/" and method == "GET":
            return {"results": [{"client_id": "coder", "pk": 1}] if self.existing_provider else []}
        if path.startswith("/api/v3/core/applications/"):
            return {"results": [{"pk": "app"}]}
        if path == "/api/v3/providers/oauth2/" and method == "POST":
            return {"pk": 1}
        return {}

    def run_provisioning(self, set_secret):
        with patch.dict(os.environ, {"TEST_API_TOKEN": "dummy-token", "TEST_CLIENT_SECRET": "dummy-secret"}), \
             patch.object(bootstrap, "api_or_raise", side_effect=self.api), \
             patch.object(bootstrap, "ensure_group"), \
             patch.object(bootstrap, "ensure_scope_mapping", return_value="groups"), \
             patch.object(bootstrap, "set_keyvault_secret", side_effect=set_secret):
            bootstrap.provision_oidc_app(self.args)

    def test_vault_failure_does_not_mutate_existing_or_new_provider(self):
        for existing in (True, False):
            for failing_key in ("client-id", "client-secret"):
                with self.subTest(existing=existing, failing_key=failing_key):
                    self.events.clear()
                    self.existing_provider = existing

                    def set_secret(key, value):
                        self.events.append(("vault", key))
                        return key != failing_key

                    with self.assertRaises(RuntimeError):
                        self.run_provisioning(set_secret)
                    self.assertFalse(any(method in ("POST", "PATCH") and path.startswith("/api/v3/providers/oauth2/")
                                         for method, path in self.events))

    def test_both_vault_writes_precede_provider_create_or_rotation(self):
        for existing in (True, False):
            with self.subTest(existing=existing):
                self.events.clear()
                self.existing_provider = existing

                def set_secret(key, value):
                    self.events.append(("vault", key))
                    return True

                self.run_provisioning(set_secret)
                provider_index = next(i for i, (method, path) in enumerate(self.events)
                                      if method in ("POST", "PATCH") and path.startswith("/api/v3/providers/oauth2/"))
                self.assertLess(self.events.index(("vault", "client-id")), provider_index)
                self.assertLess(self.events.index(("vault", "client-secret")), provider_index)

    def test_generated_secret_is_written_before_provider_creation(self):
        self.existing_provider = False
        self.args.client_secret_env = None

        def set_secret(key, value):
            self.events.append(("vault", key))
            self.assertTrue(value)
            return True

        self.run_provisioning(set_secret)
        self.assertLess(self.events.index(("vault", "client-secret")),
                        self.events.index(("POST", "/api/v3/providers/oauth2/")))

    def test_provider_failure_warns_that_vault_needs_reconciliation(self):
        self.fail_provider = True
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(RuntimeError):
            self.run_provisioning(lambda key, value: True)
        self.assertIn("reconcile both systems", stderr.getvalue())
        self.assertNotIn("dummy-secret", stderr.getvalue())

    def test_existing_provider_without_rotation_does_not_write_credentials(self):
        self.args.client_secret_env = None
        self.run_provisioning(lambda key, value: self.fail("unexpected Key Vault write"))
        self.assertFalse(any(method in ("POST", "PATCH") and path.startswith("/api/v3/providers/oauth2/")
                             for method, path in self.events))

    def test_kubernetes_secret_mode_still_persists_after_provider_creation(self):
        self.args.keyvault_client_id_key = None
        self.args.keyvault_client_secret_key = None
        self.existing_provider = False
        with patch.object(bootstrap, "create_kubernetes_client_secret") as create:
            self.run_provisioning(lambda key, value: self.fail("unexpected Key Vault write"))
        create.assert_called_once_with("coder", "coder-oidc", "client-secret", "dummy-secret")

    def test_vault_cli_failure_does_not_log_command_diagnostics(self):
        stderr = io.StringIO()
        with patch.object(bootstrap.subprocess, "run", return_value=SimpleNamespace(
                returncode=1, stderr="sensitive diagnostic")), contextlib.redirect_stdout(stderr):
            self.assertFalse(bootstrap.set_keyvault_secret("client-secret", "dummy-secret"))
        self.assertNotIn("sensitive diagnostic", stderr.getvalue())
        self.assertNotIn("dummy-secret", stderr.getvalue())

    def test_vault_error_reports_known_tenant_problem_without_leaking_diagnostics(self):
        output = io.StringIO()
        with patch.object(bootstrap.subprocess, "run", return_value=SimpleNamespace(
                returncode=1, stderr="AKV10032: Invalid issuer. dummy-secret private diagnostic")), \
             contextlib.redirect_stdout(output):
            self.assertFalse(bootstrap.set_keyvault_secret("client-secret", "dummy-secret"))
        self.assertIn("AKV10032", output.getvalue())
        self.assertIn("tenant", output.getvalue().lower())
        self.assertNotIn("dummy-secret", output.getvalue())
        self.assertNotIn("private diagnostic", output.getvalue())

    def test_vault_error_reports_rbac_without_echoing_encoded_secret(self):
        output = io.StringIO()
        with patch.object(bootstrap.subprocess, "run", return_value=SimpleNamespace(
                returncode=1, stderr="ForbiddenByRbac: value=dummy%2Fsecret")), \
             contextlib.redirect_stdout(output):
            self.assertFalse(bootstrap.set_keyvault_secret("client-secret", "dummy/secret"))
        self.assertIn("ForbiddenByRbac", output.getvalue())
        self.assertIn("RBAC", output.getvalue())
        self.assertNotIn("dummy%2Fsecret", output.getvalue())
        self.assertNotIn("dummy/secret", output.getvalue())


if __name__ == "__main__":
    unittest.main()
