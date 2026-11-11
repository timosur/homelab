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


if __name__ == "__main__":
    unittest.main()
