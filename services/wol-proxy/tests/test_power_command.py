import os
import subprocess
import unittest
from pathlib import Path

import wol_proxy


class ForcedCommandTests(unittest.TestCase):
    def run_command(self, command):
        script = Path(wol_proxy.__file__).parent / "power_command.sh"
        env = dict(os.environ, SSH_ORIGINAL_COMMAND=command)
        return subprocess.run(["/bin/sh", str(script)], env=env, capture_output=True, check=False)

    def test_refuses_unrelated_sudo_and_shell_commands(self):
        for command in (
            "sudo -n /usr/local/sbin/dark-factory-power-guard complete",
            "sh -c id",
            "sudo -n /usr/local/sbin/dark-factory-power-guard init",
            "sudo -n /usr/local/sbin/dark-factory-power-guard fence 1 a b; id",
        ):
            self.assertNotEqual(self.run_command(command).returncode, 0)
