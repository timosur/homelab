import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml

from wol_proxy.config import BackendConfig, Config, NodeGroupConfig, SandboxPoolConfig, SandboxWorkerConfig, load_config
from wol_proxy.pool import PoolMonitor, PoolPowerClient
from wol_proxy.server import build_proxies


def worker(node, host):
    return SandboxWorkerConfig(
        node=node, suspend_host=host, wol_mac="00:11:22:33:44:55" if node == "desktop" else "00:11:22:33:44:66", wol_host="relay",
        wol_broadcast="192.168.2.255", power_key_path=f"/run/{node}/key",
        known_hosts_path=f"/run/{node}/known_hosts",
    )


class PoolWiringTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"KUBERNETES_SERVICE_HOST": "kubernetes"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.backend = BackendConfig(name="ollama", listen_port=11434, target_host="gpu-host",
                                     target_port=11434, wol_mac="00:11:22:33:44:66")
        self.group = NodeGroupConfig(
            name="workers", listen_port=8080, wol_mac="00:11:22:33:44:55", ssh_user="wake", suspend_host="desktop-host",
            sandbox_namespace="sandboxes", sandbox_instance="factory", kubernetes_node="desktop",
            sandbox_admission_enabled=True,
            sandbox_pool=SandboxPoolConfig([worker("desktop", "desktop-host"), worker("gpu", "gpu-host")], ["ollama"]),
        )

    async def test_staged_pool_disables_unfenced_backend_sleep(self):
        groups, backends = build_proxies(Config([self.backend], [self.group]))
        self.assertIsInstance(groups[0]._sandbox_monitor, PoolMonitor)
        self.assertIsNone(backends[0]._coordinator)
        self.assertTrue(backends[0]._sleep_managed)
        backends[0].suspend = AsyncMock(wraps=backends[0].suspend)
        await backends[0].idle_watcher()
        backends[0].suspend.assert_not_awaited()
        with self.assertRaises(RuntimeError):
            await backends[0].suspend()

    async def test_enabled_pool_shares_coordinator_with_ollama(self):
        self.group.sandbox_power_interlock_enabled = True
        groups, backends = build_proxies(Config([self.backend], [self.group]))
        coordinator = groups[0]._coordinator
        self.assertIs(backends[0]._coordinator, coordinator)
        self.assertIsInstance(coordinator.power, PoolPowerClient)
        self.assertEqual([client.host for client in coordinator.power.clients], ["desktop-host", "gpu-host"])

    async def test_pool_wake_targets_both_hosts(self):
        groups, _ = build_proxies(Config([self.backend], [self.group]))
        with patch("wol_proxy.proxy.send_wol_packet", new_callable=AsyncMock) as send:
            await groups[0]._send_desktop_wake()
        self.assertEqual({call.args[1] for call in send.await_args_list}, {"desktop", "gpu"})

    def test_workspace_verifier_uses_the_complete_pool(self):
        self.group.sandbox_workspace_storage_class = "dark-factory-nfs"
        groups, _ = build_proxies(Config([self.backend], [self.group]))
        primary = groups[0]._sandbox_monitor.monitors[0]
        self.assertIsNotNone(primary.workspace_check)
        self.assertIsNotNone(primary.workspace_reserve)
        verifier = primary.workspace_check.__self__
        self.assertEqual(verifier.reservations.nodes, ("desktop", "gpu"))

    def test_missing_workspace_configuration_leaves_release_blocked(self):
        groups, _ = build_proxies(Config([self.backend], [self.group]))
        primary = groups[0]._sandbox_monitor.monitors[0]
        self.assertIsNone(primary.workspace_check)
        self.assertIsNone(primary.workspace_reserve)

    def test_refuses_missing_or_mismatched_managed_backends(self):
        for names in ([], ["unknown"], ["ollama", "ollama"]):
            with self.subTest(names=names):
                self.group.sandbox_pool.backends = names
                with self.assertRaises(ValueError):
                    build_proxies(Config([self.backend], [self.group]))

    def test_refuses_duplicate_workers_and_incomplete_identity(self):
        for workers in (
            [worker("desktop", "desktop-host"), worker("desktop", "gpu-host")],
            [worker("desktop", "desktop-host"), worker("gpu", "desktop-host")],
            [worker("desktop", "desktop-host"), worker("gpu", "")],
        ):
            with self.subTest(workers=workers):
                self.group.sandbox_pool.workers = workers
                with self.assertRaises(ValueError):
                    build_proxies(Config([self.backend], [self.group]))

    def test_refuses_uncoordinated_overlapping_node_group(self):
        other = NodeGroupConfig(name="legacy", listen_port=9000, wol_mac=self.backend.wol_mac,
                                suspend_host="gpu-host")
        with self.assertRaises(ValueError):
            build_proxies(Config([self.backend], [self.group, other]))

    def test_enabled_gitops_pool_uses_the_power_coordinator(self):
        root = Path(__file__).resolve().parents[3]
        manifest = yaml.safe_load((root / "apps/wol-proxy/configmap.yaml").read_text())
        lines = manifest["data"]["config.yaml"].splitlines()
        staged = []
        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith("# ") and (
                stripped.startswith(("# sandboxNamespace:", "# sandboxInstance:", "# kubernetesNode:",
                                     "# sandboxAdmissionEnabled:", "# sandboxPool:"))
                or stripped.startswith("#   ")
            ):
                line = line.replace("# ", "", 1)
            staged.append(line)
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.yaml"
            config.write_text("\n".join(staged))
            groups, backends = build_proxies(load_config(str(config)))
        self.assertEqual([worker.node for worker in groups[0].cfg.sandbox_pool.workers],
                         ["homelab-amd-desktop", "homelab-gpu"])
        self.assertIsNotNone(groups[0]._coordinator)
        self.assertTrue(backends[0]._sleep_managed)
