import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class WorkspaceTemplateTests(unittest.TestCase):
    def test_home_volume_uses_workspace_user_and_writable_group(self):
        template = (ROOT / "apps/coder/templates/board-member/main.tf").read_text()
        pod = template.split('resource "kubernetes_pod_v1" "workspace" {', 1)[1]
        context = re.search(r"\bsecurity_context\s*\{([^{}]*)\}", pod)
        self.assertIsNotNone(context, "workspace pod needs a security context for the home PVC")
        fields = dict(re.findall(r"^\s*(run_as_user|run_as_group|fs_group)\s*=\s*(\d+)", context.group(1), re.MULTILINE))
        self.assertEqual(fields, {"run_as_user": "1000", "run_as_group": "1000", "fs_group": "1000"})
        self.assertRegex(context.group(1), r'(?m)^\s*fs_group_change_policy\s*=\s*"OnRootMismatch"')

    def test_workspace_has_restricted_capabilities_and_seccomp(self):
        template = (ROOT / "apps/coder/templates/board-member/main.tf").read_text()
        pod = template.split('resource "kubernetes_pod_v1" "workspace" {', 1)[1]
        for field in ("host_network", "host_pid", "host_ipc", "automount_service_account_token"):
            self.assertRegex(pod, rf"(?m)^\s*{field}\s*=\s*false\b")
        self.assertRegex(pod, r"(?m)^\s*privileged\s*=\s*false\b")
        self.assertRegex(pod, r"(?m)^\s*allow_privilege_escalation\s*=\s*true\b")
        self.assertRegex(pod, r'hostnames\s*=\s*\["coder\.home\.timosur\.com"\]')
        self.assertIn("data.kubernetes_service_v1.coder_agent_gateway[0].spec[0].cluster_ip", pod)
        self.assertRegex(
            template,
            r'data "kubernetes_service_v1" "coder_agent_gateway"\s*\{\s*'
            r'count\s*=\s*data\.coder_workspace\.me\.start_count',
        )
        self.assertRegex(pod, r'drop\s*=\s*\["ALL"\]')
        capabilities = re.search(r"\badd\s*=\s*\[([^]]+)\]", pod)
        self.assertIsNotNone(capabilities)
        self.assertEqual(
            set(re.findall(r'"([A-Z_]+)"', capabilities.group(1))),
            {"SETUID", "SETGID", "CHOWN", "DAC_OVERRIDE", "FOWNER"},
        )
        self.assertRegex(
            pod,
            r'seccomp_profile\s*\{\s*type\s*=\s*"Localhost"\s*'
            r'localhost_profile\s*=\s*"coder-workspace\.json"\s*\}',
        )


if __name__ == "__main__":
    unittest.main()
