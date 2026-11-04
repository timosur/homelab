"""Read-only Kubernetes checks for a node group's sandbox workloads."""

import os
import ssl
from pathlib import Path
from urllib.parse import quote

from aiohttp import ClientSession, ClientTimeout

from .admission import GATE, RELEASE_TOKEN
from .interlock import Interlock
from .workspace import WorkspaceBusy


def pod_list(response: dict) -> list:
    items, metadata = response.get("items"), response.get("metadata", {})
    if (not isinstance(items, list) or not isinstance(metadata, dict)
            or metadata.get("continue")):
        raise ValueError("incomplete Kubernetes pod list")
    return items


class SandboxMonitor:
    def __init__(self, namespace: str, instance: str, node: str) -> None:
        if not namespace or not instance or not node:
            raise ValueError("sandbox namespace, instance, and node are all required")
        self.namespace = namespace
        self.instance = instance
        self.node = node
        # No gate or binding may pass until a durable workspace verifier is installed.
        self.workspace_check = None
        self.workspace_reserve = None
        self.api = f"https://{os.environ['KUBERNETES_SERVICE_HOST']}:{os.environ.get('KUBERNETES_SERVICE_PORT_HTTPS', '443')}"
        self.credentials = Path("/var/run/secrets/kubernetes.io/serviceaccount")

    async def _get(self, path: str, *, params: dict[str, str] | None = None) -> dict:
        # Re-read the projected token for each request so Kubernetes can rotate it.
        token = (self.credentials / "token").read_text().strip()
        tls = ssl.create_default_context(cafile=str(self.credentials / "ca.crt"))
        async with ClientSession(timeout=ClientTimeout(total=5)) as session:
            async with session.get(
                self.api + path,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                ssl=tls,
            ) as response:
                response.raise_for_status()
                body = await response.json()
        if not isinstance(body, dict):
            raise ValueError("unexpected Kubernetes response")
        return body

    async def active(self) -> bool:
        namespace = quote(self.namespace, safe="")
        selector = {"labelSelector": f"dark-factory.io/instance={self.instance}"}
        pods = await self._get(f"/api/v1/namespaces/{namespace}/pods", params=selector)
        # A deleting or terminal pod remains active until Kubernetes removes it.
        return bool(pod_list(pods))

    async def any_workload(self) -> bool:
        """Use an unfiltered pod list before suspending the desktop."""
        namespace = quote(self.namespace, safe="")
        pods = await self._get(f"/api/v1/namespaces/{namespace}/pods")
        return bool(pod_list(pods))

    async def can_delete_workspace(self, claim: dict) -> bool:
        metadata = claim.get("metadata") if isinstance(claim, dict) else None
        name = metadata.get("name") if isinstance(metadata, dict) else None
        uid = metadata.get("uid") if isinstance(metadata, dict) else None
        if not all(isinstance(value, str) and value for value in (name, uid)):
            raise ValueError("workspace claim identity is incomplete")
        namespace = quote(self.namespace, safe="")
        pods = await self._get(f"/api/v1/namespaces/{namespace}/pods")
        for pod in pod_list(pods):
            spec = pod.get("spec") if isinstance(pod, dict) else None
            if not isinstance(spec, dict):
                raise ValueError("invalid Kubernetes pod")
            volumes = spec.get("volumes", [])
            if not isinstance(volumes, list) or not all(isinstance(volume, dict) for volume in volumes):
                raise ValueError("invalid Kubernetes pod volumes")
            for volume in volumes:
                # Generic ephemeral claims can be generated from pod and volume names.
                if "ephemeral" in volume:
                    return False
                if "persistentVolumeClaim" in volume:
                    source = volume["persistentVolumeClaim"]
                    if not isinstance(source, dict) or not isinstance(source.get("claimName"), str) or not source["claimName"]:
                        raise ValueError("invalid Kubernetes pod claim reference")
                    if source["claimName"] == name:
                        return False
        return True

    async def node_lease_revision(self) -> str:
        lease = await self._get(f"/apis/coordination.k8s.io/v1/namespaces/kube-node-lease/leases/{quote(self.node, safe='')}")
        renewal = lease.get("spec", {}).get("renewTime")
        if not isinstance(renewal, str) or not renewal:
            raise ValueError("node lease renewal is unavailable")
        return renewal

    async def verify_admission(self) -> None:
        """Refuse power actions if either fail-closed admission configuration is missing."""
        base = "/apis/admissionregistration.k8s.io/v1"
        for resource, path, operations in (
            ("mutatingwebhookconfigurations", "/mutate", {
                "pods": {"CREATE"}, "persistentvolumeclaims": {"CREATE", "UPDATE"},
            }),
            ("validatingwebhookconfigurations", "/validate", {
                "pods": {"CREATE", "UPDATE"}, "pods/binding": {"CREATE"}, "bindings": {"CREATE"},
                "persistentvolumeclaims": {"CREATE", "UPDATE", "DELETE"},
            }),
        ):
            configuration = await self._get(f"{base}/{resource}/dark-factory-sandbox-gate")
            webhooks = configuration.get("webhooks")
            if not isinstance(webhooks, list) or len(webhooks) != 1 or not isinstance(webhooks[0], dict):
                raise ValueError("unexpected sandbox admission configuration")
            hook = webhooks[0]
            selector = hook.get("namespaceSelector", {})
            client = hook.get("clientConfig", {})
            service = client.get("service", {}) if isinstance(client, dict) else {}
            if (
                hook.get("failurePolicy", "Fail") != "Fail"
                or hook.get("matchConditions")
                or hook.get("objectSelector") not in (None, {}, {"matchLabels": {}})
                or not isinstance(selector, dict)
                or selector.get("matchLabels") != {"kubernetes.io/metadata.name": self.namespace}
                or selector.get("matchExpressions")
                or not isinstance(service, dict)
                or service.get("name") != "dark-factory-admission"
                or service.get("namespace") != "wol-proxy"
                or service.get("path") != path
                or service.get("port", 443) != 443
                or not client.get("caBundle")
            ):
                raise ValueError("sandbox admission is not fail-closed")
            rules = hook.get("rules", [])
            if not isinstance(rules, list):
                raise ValueError("invalid sandbox admission rules")
            for name, required in operations.items():
                if not any(
                    isinstance(rule, dict) and "" in rule.get("apiGroups", [])
                    and "v1" in rule.get("apiVersions", [])
                    and name in rule.get("resources", [])
                    and required.issubset(set(rule.get("operations", [])))
                    and rule.get("scope", "*") in ("Namespaced", "*")
                    for rule in rules
                ):
                    raise ValueError("sandbox admission rule is missing")

    async def node_ready(self) -> bool:
        node = await self._get(f"/api/v1/nodes/{quote(self.node, safe='')}")
        conditions = node.get("status", {}).get("conditions")
        if not isinstance(conditions, list) or not all(isinstance(c, dict) for c in conditions):
            raise ValueError("node readiness is unavailable")
        return any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions)

    async def can_bind(self, name: str, uid: str) -> bool:
        namespace = quote(self.namespace, safe="")
        pod = await self._get(f"/api/v1/namespaces/{namespace}/pods/{quote(name, safe='')}")
        metadata = pod.get("metadata", {})
        spec = pod.get("spec", {})
        if not isinstance(metadata, dict) or metadata.get("name") != name or not isinstance(spec, dict):
            raise ValueError("invalid bound pod")
        if metadata.get("uid") != uid:
            return False
        scheduling_gates = spec.get("schedulingGates", [])
        if not isinstance(scheduling_gates, list) or not all(isinstance(g, dict) for g in scheduling_gates):
            raise ValueError("invalid pod scheduling gates")
        if any(gate.get("name") == GATE for gate in scheduling_gates) or self.workspace_check is None:
            return False
        return await self.workspace_check(pod) is True

    async def can_release(self, pod: dict) -> bool:
        metadata = pod.get("metadata", {})
        annotations = metadata.get("annotations", {}) if isinstance(metadata, dict) else {}
        token = annotations.get(RELEASE_TOKEN) if isinstance(annotations, dict) else None
        if not isinstance(token, str):
            return False
        state = await Interlock(self).read()
        expected = f"{state['epoch']}:{state['owner']}:{state['anchor']}"
        if state["phase"] != "releasing" or token != expected or self.workspace_check is None:
            return False
        return await self.workspace_check(pod) is True

    async def has_gated_pods(self) -> bool:
        namespace = quote(self.namespace, safe="")
        result = await self._get(
            f"/api/v1/namespaces/{namespace}/pods",
            params={"labelSelector": f"dark-factory.io/instance={self.instance}"},
        )
        for pod in pod_list(result):
            if not isinstance(pod, dict) or not isinstance(pod.get("spec", {}), dict):
                raise ValueError("invalid Kubernetes pod")
            pod_gates = pod.get("spec", {}).get("schedulingGates", [])
            if not isinstance(pod_gates, list) or not all(isinstance(g, dict) for g in pod_gates):
                raise ValueError("invalid pod scheduling gates")
            if any(g.get("name") == GATE for g in pod_gates):
                return True
        return False

    async def release_gates(self, epoch: int, owner: str, anchor: str) -> None:
        """Remove only our gate from the current revision of each factory pod."""
        namespace = quote(self.namespace, safe="")
        result = await self._get(
            f"/api/v1/namespaces/{namespace}/pods",
            params={"labelSelector": f"dark-factory.io/instance={self.instance}"},
        )
        for pod in pod_list(result):
            if not isinstance(pod, dict):
                raise ValueError("invalid Kubernetes pod")
            metadata = pod.get("metadata", {})
            if metadata.get("deletionTimestamp"):
                continue
            scheduling_gates = pod.get("spec", {}).get("schedulingGates", [])
            if not isinstance(scheduling_gates, list) or not all(isinstance(g, dict) for g in scheduling_gates):
                raise ValueError("invalid pod scheduling gates")
            for index, gate in enumerate(scheduling_gates):
                if gate.get("name") != GATE:
                    continue
                name, uid, version = metadata.get("name"), metadata.get("uid"), metadata.get("resourceVersion")
                if not all(isinstance(value, str) and value for value in (name, uid, version)):
                    raise ValueError("pod identity is incomplete")
                if self.workspace_reserve is None:
                    raise ValueError("workspace reservation is unavailable")
                try:
                    await self.workspace_reserve(pod, epoch, owner, anchor)
                except WorkspaceBusy:
                    # Leave this gate in place without starving unrelated tasks.
                    break
                patch = [
                    {"op": "test", "path": "/metadata/uid", "value": uid},
                    {"op": "test", "path": "/metadata/resourceVersion", "value": version},
                    {"op": "test", "path": f"/spec/schedulingGates/{index}/name", "value": GATE},
                ]
                token = f"{epoch}:{owner}:{anchor}"
                if "annotations" in metadata:
                    if not isinstance(metadata["annotations"], dict):
                        raise ValueError("invalid pod annotations")
                    patch.append({"op": "add", "path": "/metadata/annotations/dark-factory.home.timosur.com~1release-token", "value": token})
                else:
                    patch.append({"op": "add", "path": "/metadata/annotations", "value": {RELEASE_TOKEN: token}})
                patch.append({"op": "remove", "path": f"/spec/schedulingGates/{index}"})
                await self._patch(f"/api/v1/namespaces/{namespace}/pods/{quote(name, safe='')}", patch)
                break

    async def _patch(self, path: str, patch: list[dict]) -> None:
        token = (self.credentials / "token").read_text().strip()
        tls = ssl.create_default_context(cafile=str(self.credentials / "ca.crt"))
        async with ClientSession(timeout=ClientTimeout(total=5)) as session:
            async with session.patch(
                self.api + path,
                json=patch,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json-patch+json"},
                ssl=tls,
            ) as response:
                response.raise_for_status()

    async def _put(self, path: str, body: dict) -> dict:
        token = (self.credentials / "token").read_text().strip()
        tls = ssl.create_default_context(cafile=str(self.credentials / "ca.crt"))
        async with ClientSession(timeout=ClientTimeout(total=5)) as session:
            async with session.put(
                self.api + path, json=body,
                headers={"Authorization": f"Bearer {token}"}, ssl=tls,
            ) as response:
                response.raise_for_status()
                result = await response.json()
        if not isinstance(result, dict):
            raise ValueError("unexpected Kubernetes response")
        return result
