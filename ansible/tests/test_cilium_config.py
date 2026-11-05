import shlex
import unittest
from pathlib import Path

import yaml


ROLE = Path(__file__).resolve().parents[1] / "roles" / "cilium"


class CiliumConfigurationTests(unittest.TestCase):
    def test_kata_networking_values(self):
        values = yaml.safe_load((ROLE / "files" / "values.yaml").read_text())
        self.assertEqual(values["devices"], "eth+,en+")
        self.assertIs(values["socketLB"]["hostNamespaceOnly"], True)
        self.assertIs(values["rollOutCiliumPods"], True)

    def test_install_and_upgrade_use_copied_values_without_conflicting_flags(self):
        tasks = yaml.safe_load((ROLE / "tasks" / "main.yml").read_text())
        install_index, install = next(
            (index, task)
            for index, task in enumerate(tasks)
            if task["name"] == "Install or upgrade Cilium with cluster mesh support"
        )
        command = install["ansible.builtin.command"]["cmd"]
        self.assertIn("cilium {{ cilium_operation }}", command)
        args = shlex.split(command.replace("{{ cilium_operation }}", "upgrade"))
        self.assertIn("--values", args)
        destination = args[args.index("--values") + 1]
        copied = next(
            task["ansible.builtin.copy"]
            for task in tasks[:install_index]
            if task.get("ansible.builtin.copy", {}).get("dest") == destination
        )
        self.assertEqual(copied["src"], "values.yaml")
        self.assertTrue((ROLE / "files" / copied["src"]).is_file())
        self.assertTrue(any(
            task.get("ansible.builtin.file", {}).get("path") == str(Path(destination).parent)
            and task["ansible.builtin.file"].get("state") == "directory"
            for task in tasks[:install_index]
        ))
        for flag in ("devices=", "socketLB.hostNamespaceOnly=", "rollOutCiliumPods="):
            self.assertNotIn(flag, command)


if __name__ == "__main__":
    unittest.main()
