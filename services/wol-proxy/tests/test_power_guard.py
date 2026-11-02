import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from wol_proxy.power_guard import GuardError, PowerGuard

OWNER = "a" * 32
OTHER = "b" * 32
ANCHOR = "12345678-1234-1234-1234-123456789abc"


class PowerGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runner = Mock(return_value=subprocess.CompletedProcess([], 0))
        self.guard = PowerGuard(Path(self.temp.name) / "guard", runner=self.runner)
        self.guard.initialize()

    def test_new_epoch_fences_stale_suspend(self):
        self.guard.fence(2, OWNER, ANCHOR)
        with self.assertRaises(GuardError):
            self.guard.suspend(1, OWNER, ANCHOR)
        self.runner.assert_not_called()

    def test_unresolved_suspend_survives_restart_and_blocks_new_epoch(self):
        self.guard.fence(1, OWNER, ANCHOR)
        self.guard.suspend(1, OWNER, ANCHOR)
        restarted = PowerGuard(self.guard.directory, runner=self.runner)
        self.assertEqual(restarted.status()["phase"], "suspending")
        with self.assertRaises(GuardError):
            restarted.fence(2, OTHER, ANCHOR)
        restarted.complete()
        self.assertEqual(restarted.fence(2, OTHER, ANCHOR), {"epoch": 2, "owner": OTHER, "anchor": ANCHOR, "phase": "ready"})
        with self.assertRaises(GuardError):
            restarted.suspend(1, OWNER, ANCHOR)

    def test_failed_or_timed_out_suspend_stays_unresolved(self):
        self.guard.fence(1, OWNER, ANCHOR)
        self.runner.side_effect = subprocess.TimeoutExpired("systemctl", 30)
        with self.assertRaises(GuardError):
            self.guard.suspend(1, OWNER, ANCHOR)
        self.assertEqual(self.guard.status()["phase"], "suspending")
        with self.assertRaises(GuardError):
            self.guard.fence(2, OTHER, ANCHOR)

    def test_equal_epoch_cannot_be_reused_by_another_owner(self):
        self.guard.fence(1, OWNER, ANCHOR)
        with self.assertRaises(GuardError):
            self.guard.fence(1, OTHER, ANCHOR)

    def test_recreated_configmap_cannot_reuse_host_guard(self):
        self.guard.fence(1, OWNER, ANCHOR)
        with self.assertRaises(GuardError):
            self.guard.fence(2, OTHER, "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    def test_existing_directory_with_missing_state_is_not_reset(self):
        (self.guard.directory / "state.json").unlink()
        with self.assertRaises(GuardError):
            self.guard.initialize()

    def test_corrupt_state_fails_closed(self):
        (self.guard.directory / "state.json").write_text("{}")
        with self.assertRaises(GuardError):
            self.guard.fence(1, OWNER, ANCHOR)
