import ipaddress
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class WorkspaceSandboxTests(unittest.TestCase):
    def test_seccomp_keeps_namespace_creation_and_privileged_calls_restricted(self):
        profile = json.loads(
            (ROOT / "ansible/roles/coder-workspace-seccomp/files/coder-workspace.json").read_text()
        )
        self.assertEqual(profile["defaultAction"], "SCMP_ACT_ERRNO")
        allowed = {
            name
            for rule in profile["syscalls"]
            if rule["action"] == "SCMP_ACT_ALLOW" and not rule.get("args")
            for name in rule["names"]
        }
        self.assertIn("setns", allowed)
        self.assertTrue({"bpf", "mount", "unshare", "open_by_handle_at"}.isdisjoint(allowed))
        self.assertTrue(any(
            rule["names"] == ["clone"] and rule.get("args")
            for rule in profile["syscalls"]
        ))
        self.assertTrue(any(
            rule["names"] == ["clone3"] and rule["action"] == "SCMP_ACT_ERRNO"
            for rule in profile["syscalls"]
        ))

    def test_tetragon_policy_excludes_runtime_host_pid_namespace(self):
        policy = (ROOT / "apps/tetragon/coder-workspace-setns.yaml").read_text()
        self.assertIn("kind: TracingPolicyNamespaced", policy)
        self.assertIn("namespace: coder", policy)
        self.assertIn("app.kubernetes.io/name: coder-workspace", policy)
        self.assertIn("app.kubernetes.io/component: development-workspace", policy)
        self.assertIn("call: sys_setns", policy)
        self.assertIn("syscall: true", policy)
        self.assertRegex(policy, r'namespace: Pid\s+operator: NotIn\s+values: \["host_ns"\]')
        self.assertIn("action: Sigkill", policy)

    def test_workspace_egress_does_not_deny_public_world_identity(self):
        policy = (ROOT / "networking/cilium-network-policies/home/coder-workspace-egress.yaml").read_text()
        self.assertIn("namespace: coder", policy)
        self.assertIn("app.kubernetes.io/name: coder-workspace", policy)
        self.assertIn("app.kubernetes.io/component: development-workspace", policy)
        self.assertRegex(policy, r'key: k8s:io\.kubernetes\.pod\.namespace\s+operator: Exists')
        self.assertIn('endPort: 52', policy)
        self.assertIn('port: "54"', policy)

        blocks = re.findall(r'cidr: (\S+)\s+except:\n((?:\s+- \S+\n)+)', policy)
        self.assertEqual(len(blocks), 2)
        for cidr, block in blocks:
            subnet = ipaddress.ip_network(cidr)
            exclusions = [ipaddress.ip_network(value) for value in re.findall(r'- (\S+)', block)]
            probes = (["10.1.2.3", "172.16.0.1", "192.168.2.1", "169.254.1.1"]
                      if subnet.version == 4 else ["fc00::1", "fe80::1", "::1"])
            self.assertTrue(all(any(ipaddress.ip_address(ip) in net for net in exclusions) for ip in probes))
            public = ipaddress.ip_address("1.1.1.1" if subnet.version == 4 else "2606:4700:4700::1111")
            self.assertIn(public, subnet)
            self.assertFalse(any(public in net for net in exclusions))

    def test_agent_gateway_is_private_and_routes_only_coder(self):
        text = (ROOT / "networking/gateways/home/coder-agent-gateway.yaml").read_text()
        proxy, _, gateway, role, binding = text.split("\n---\n")
        self.assertRegex(proxy, r'name: coder-agent-gateway\s+type: ClusterIP')
        self.assertEqual(gateway.count("protocol: HTTPS"), 1)
        self.assertIn("hostname: coder.home.timosur.com", gateway)
        self.assertIn("kubernetes.io/metadata.name: coder", gateway)
        self.assertIn('resourceNames: ["coder-agent-gateway"]', role)
        self.assertIn('verbs: ["get"]', role)
        self.assertIn("namespace: coder", binding)

        route = (ROOT / "networking/httproutes/home/coder-agent.yaml").read_text()
        self.assertIn("name: envoy-gateway-coder-agent", route)
        self.assertIn("coder.home.timosur.com", route)
        self.assertIn("name: coder", route)
        ingress = (ROOT / "networking/cilium-network-policies/home/coder-workspace-agent-gateway.yaml").read_text()
        self.assertIn("gateway.envoyproxy.io/owning-gateway-name: envoy-gateway-coder-agent", ingress)
        self.assertIn("app.kubernetes.io/name: coder-workspace", ingress)
        self.assertIn("app.kubernetes.io/component: development-workspace", ingress)


if __name__ == "__main__":
    unittest.main()
