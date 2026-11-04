"""Opt-in API-server test; creates only isolated, non-provisioning PVCs.

Run with DF_KUBE_CONTEXT and DF_CONTROL_SSH set. The SSH target must be the
API-server host and permit a loopback-only reverse tunnel. No agents run.
"""

import asyncio
import base64
import copy
import json
import os
import secrets
import ssl
import tempfile
import unittest
from pathlib import Path

from aiohttp import web
import yaml

from wol_proxy.admission import admission_app


@unittest.skipUnless(os.environ.get("DF_KUBE_CONTEXT") and os.environ.get("DF_CONTROL_SSH"),
                     "set DF_KUBE_CONTEXT and DF_CONTROL_SSH for the isolated API-server test")
class WorkspaceServerSideApplyTests(unittest.IsolatedAsyncioTestCase):
    async def command(self, *args, data=None, check=True, cwd=None):
        proc = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=cwd,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(data), timeout=45)
        except TimeoutError:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise
        if check and proc.returncode:
            self.fail(f"{args[0]} failed: {stderr.decode(errors='replace')}")
        return proc.returncode, stdout.decode(), stderr.decode(errors="replace")

    async def kubectl(self, *args, obj=None, check=True):
        return await self.command(
            "kubectl", "--context", os.environ["DF_KUBE_CONTEXT"], "--request-timeout=8s", *args,
            data=json.dumps(obj).encode() if obj is not None else None, check=check,
        )

    async def test_real_api_reconciles_safe_claim_and_rejects_old_claim(self):
        suffix = secrets.token_hex(5)
        namespace = f"df-workspace-ssa-{suffix}"
        webhook = f"df-workspace-ssa-{suffix}"
        pv = f"df-workspace-ssa-{suffix}"
        storage_class = f"df-nonexistent-{suffix}"
        remote_port = 30000 + secrets.randbelow(20000)
        target = os.environ["DF_CONTROL_SSH"]
        created_namespace = created_pv = created_mutating = created_validating = tunnel = False
        runner = None
        delayed_delete = delayed_binding = None
        deletion_entered, release_deletion = asyncio.Event(), asyncio.Event()
        binding_entered, release_binding = asyncio.Event(), asyncio.Event()
        pause_delete = False
        pause_binding = False
        print(f"isolated test namespace: {namespace}", flush=True)
        with tempfile.TemporaryDirectory(prefix="df-ssa-") as directory:
            directory = Path(directory)
            cert, key, control = directory / "tls.crt", directory / "tls.key", "s"
            try:
                await self.command(
                    "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-keyout", str(key), "-out", str(cert), "-subj", "/CN=127.0.0.1",
                    "-addext", "subjectAltName=IP:127.0.0.1",
                )
                tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                tls.load_cert_chain(cert, key)
                binding_uids = []
                binding_observed = []
                expected_pod_uid = None
                delete_allowed = True
                delete_actor = f"system:serviceaccount:{namespace}:df-delete-probe"

                async def can_bind(name, uid):
                    binding_uids.append((name, uid))
                    permitted = name == "binding-probe" and uid == expected_pod_uid
                    if permitted and pause_binding:
                        binding_entered.set()
                        await asyncio.wait_for(release_binding.wait(), timeout=20)
                    return permitted

                async def can_delete(claim):
                    return delete_allowed

                app = admission_app(namespace, "homelab-amd", can_bind=can_bind, workspace_instance="dark-factory",
                                    workspace_storage_class=storage_class, workspace_delete_actor=delete_actor,
                                    can_delete=can_delete)

                @web.middleware
                async def observe_binding(request, handler):
                    nonlocal pause_delete
                    body = await request.json()
                    incoming = body.get("request", {})
                    resource = incoming.get("resource", {}).get("resource")
                    if resource == "bindings" or incoming.get("subResource") == "binding":
                        metadata = (incoming.get("object") or {}).get("metadata", {})
                        binding_observed.append((resource, incoming.get("subResource"), bool(metadata.get("uid"))))
                    if (resource == "persistentvolumeclaims" and incoming.get("operation") == "DELETE"
                            and incoming.get("name") == "delete-race-probe" and pause_delete):
                        pause_delete = False
                        deletion_entered.set()
                        await asyncio.wait_for(release_deletion.wait(), timeout=6)
                    return await handler(request)

                app.middlewares.append(observe_binding)
                runner = web.AppRunner(app)
                await runner.setup()
                site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=tls)
                await site.start()
                local_port = site._server.sockets[0].getsockname()[1]
                await self.command(
                    "ssh", "-f", "-N", "-M", "-S", str(control), "-o", "BatchMode=yes",
                    "-o", "ExitOnForwardFailure=yes", "-o", "ConnectTimeout=10",
                    "-R", f"127.0.0.1:{remote_port}:127.0.0.1:{local_port}", target, cwd=directory,
                )
                tunnel = True
                await self.kubectl("create", "-f", "-", obj={
                    "apiVersion": "v1", "kind": "Namespace", "metadata": {
                        "name": namespace, "labels": {"app.kubernetes.io/part-of": "df-workspace-ssa-test"},
                    },
                })
                created_namespace = True

                for resource in (
                    {"apiVersion": "v1", "kind": "ServiceAccount",
                     "metadata": {"name": "df-delete-probe", "namespace": namespace}},
                    {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role",
                     "metadata": {"name": "df-delete-probe", "namespace": namespace},
                     "rules": [{"apiGroups": [""], "resources": ["persistentvolumeclaims"],
                                "verbs": ["get", "delete"]}]},
                    {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding",
                     "metadata": {"name": "df-delete-probe", "namespace": namespace},
                     "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "df-delete-probe"},
                     "subjects": [{"kind": "ServiceAccount", "name": "df-delete-probe", "namespace": namespace}]},
                ):
                    await self.kubectl("create", "-f", "-", obj=resource)

                fixture = yaml.safe_load((Path(__file__).resolve().parents[1] / "fixtures/upstream-pvc-e73a64db.yaml").read_text())

                def claim(key):
                    value = copy.deepcopy(fixture)
                    value["metadata"].update(name="task-" + key.lower(), namespace=namespace)
                    value["metadata"]["labels"].update({"dark-factory.io/instance": "dark-factory",
                                                        "dark-factory.io/task-key": key})
                    value["spec"]["storageClassName"] = storage_class
                    return value

                # Create the incompatible claim before the isolated webhooks exist.
                await self.kubectl("create", "-f", "-", obj=claim("OLD-1"))
                # This fake static CSI PV has no registered driver and is never mounted.
                # It exercises bound-PVC immutability without provisioning backend data.
                await self.kubectl("create", "-f", "-", obj={
                    "apiVersion": "v1", "kind": "PersistentVolume", "metadata": {
                        "name": pv, "labels": {"app.kubernetes.io/part-of": "df-workspace-ssa-test"},
                    },
                    "spec": {
                        "capacity": {"storage": "20Gi"}, "accessModes": ["ReadWriteOncePod"],
                        "storageClassName": storage_class, "persistentVolumeReclaimPolicy": "Retain",
                        "claimRef": {"namespace": namespace, "name": "task-df-1"},
                        "csi": {"driver": "nonexistent.test.home.timosur.com", "volumeHandle": suffix},
                    },
                })
                created_pv = True
                ca = base64.b64encode(cert.read_bytes()).decode()
                for kind, path in (("MutatingWebhookConfiguration", "/mutate"),
                                   ("ValidatingWebhookConfiguration", "/validate")):
                    obj = {
                        "apiVersion": "admissionregistration.k8s.io/v1", "kind": kind,
                        "metadata": {"name": webhook, "labels": {"app.kubernetes.io/part-of": "df-workspace-ssa-test"}},
                        "webhooks": [{
                            "name": f"{path[1:]}.{suffix}.test.home.timosur.com",
                            "admissionReviewVersions": ["v1"], "sideEffects": "None", "failurePolicy": "Fail",
                            "timeoutSeconds": 25,
                            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": namespace}},
                            "clientConfig": {"url": f"https://127.0.0.1:{remote_port}{path}", "caBundle": ca},
                            "rules": [{"apiGroups": [""], "apiVersions": ["v1"],
                                       "operations": ["CREATE", "UPDATE", "DELETE"] if path == "/validate" else ["CREATE", "UPDATE"],
                                       "resources": ["persistentvolumeclaims"], "scope": "Namespaced"}] + (
                                           [{"apiGroups": [""], "apiVersions": ["v1"], "operations": ["CREATE"],
                                             "resources": ["pods/binding", "bindings"], "scope": "Namespaced"}]
                                           if path == "/validate" else []),
                        }],
                    }
                    await self.kubectl("create", "-f", "-", obj=obj)
                    if kind == "MutatingWebhookConfiguration":
                        created_mutating = True
                    else:
                        created_validating = True

                for attempt in range(2):
                    await self.kubectl("apply", "--server-side", "--force-conflicts",
                                       "--field-manager=dark-factory", "-f", "-", obj=claim("DF-1"))
                    if attempt == 0:
                        await self.kubectl("-n", namespace, "wait", "--for=jsonpath={.status.phase}=Bound",
                                           "pvc/task-df-1", "--timeout=30s")
                    _, output, _ = await self.kubectl("-n", namespace, "get", "pvc", "task-df-1",
                                                      "-o", "json", "--show-managed-fields")
                    persisted = json.loads(output)
                    self.assertEqual(persisted["spec"]["accessModes"], ["ReadWriteOncePod"])
                    self.assertTrue(any(entry.get("manager") == "dark-factory" and entry.get("operation") == "Apply"
                                        for entry in persisted["metadata"].get("managedFields", [])))

                code, _, _ = await self.kubectl("apply", "--server-side", "--force-conflicts",
                                                "--field-manager=dark-factory", "-f", "-",
                                                obj=claim("OLD-1"), check=False)
                self.assertNotEqual(code, 0, "an incompatible existing claim was adopted")
                _, output, _ = await self.kubectl("-n", namespace, "get", "pvc", "task-old-1", "-o", "json")
                self.assertEqual(json.loads(output)["spec"]["accessModes"], ["ReadWriteOnce"])

                code, _, _ = await self.kubectl("-n", namespace, "delete", "pvc", "task-df-1",
                                                "--dry-run=server", check=False)
                self.assertNotEqual(code, 0, "protected claim deletion was not blocked")
                await self.kubectl("-n", namespace, "get", "pvc", "task-df-1")

                await self.kubectl(f"--as={delete_actor}", "-n", namespace, "delete", "pvc", "task-df-1", "--dry-run=server")
                delete_allowed = False
                code, _, _ = await self.kubectl(f"--as={delete_actor}", "-n", namespace, "delete", "pvc", "task-df-1",
                                                "--dry-run=server", check=False)
                self.assertNotEqual(code, 0, "in-use workspace deletion was allowed")
                delete_allowed = True
                code, _, _ = await self.kubectl(f"--as={delete_actor}", "-n", namespace, "delete", "pvc", "task-old-1",
                                                "--dry-run=server", check=False)
                self.assertNotEqual(code, 0, "incompatible old claim deletion was allowed")

                # An unrecognised scheduler keeps this fake pod unscheduled. Initial binding
                # probes are dry-run; the later in-flight stale-UID probe is a real API request.
                await self.kubectl("create", "-f", "-", obj={
                    "apiVersion": "v1", "kind": "Pod", "metadata": {"name": "binding-probe", "namespace": namespace},
                    "spec": {"schedulerName": f"df-test-{suffix}", "activeDeadlineSeconds": 120,
                             "containers": [{"name": "never-start", "image": "invalid.invalid/never-start:test"}]},
                })
                _, output, _ = await self.kubectl("-n", namespace, "get", "pod", "binding-probe", "-o", "json")
                expected_pod_uid = json.loads(output)["metadata"]["uid"]
                binding = {"apiVersion": "v1", "kind": "Binding",
                           "metadata": {"name": "binding-probe", "namespace": namespace, "uid": expected_pod_uid},
                           "target": {"kind": "Node", "name": "homelab-amd"}}
                url = f"/api/v1/namespaces/{namespace}/pods/binding-probe/binding?dryRun=All"
                await self.kubectl("create", "--raw", url, "-f", "-", obj=binding)
                self.assertEqual(binding_uids[-1], ("binding-probe", expected_pod_uid))
                legacy = f"/api/v1/namespaces/{namespace}/bindings?dryRun=All"
                code, _, _ = await self.kubectl("create", "--raw", legacy, "-f", "-", obj=binding, check=False)
                self.assertNotEqual(code, 0, "legacy binding was not blocked")
                self.assertEqual(binding_observed[-1], ("bindings", None, False))
                binding["metadata"]["uid"] = "stale-pod-uid"
                code, _, _ = await self.kubectl("create", "--raw", url, "-f", "-", obj=binding, check=False)
                self.assertNotEqual(code, 0, "stale pod UID was accepted")
                _, output, _ = await self.kubectl("-n", namespace, "get", "pod", "binding-probe", "-o", "json")
                self.assertNotIn("nodeName", json.loads(output)["spec"], "dry-run binding modified the pod")

                # Admission approved the old UID before being delayed. Recreate the pod under
                # the same name while that binding is in flight: the API server must apply the
                # binding UID precondition after admission, not bind the replacement.
                binding["metadata"]["uid"] = expected_pod_uid
                pause_binding = True
                delayed_binding = asyncio.create_task(self.kubectl(
                    "create", "--raw", f"/api/v1/namespaces/{namespace}/pods/binding-probe/binding",
                    "-f", "-", obj=binding, check=False,
                ))
                await asyncio.wait_for(binding_entered.wait(), timeout=10)
                await self.kubectl("-n", namespace, "delete", "pod", "binding-probe", "--wait=true", "--timeout=10s")
                await self.kubectl("create", "-f", "-", obj={
                    "apiVersion": "v1", "kind": "Pod", "metadata": {"name": "binding-probe", "namespace": namespace},
                    "spec": {"schedulerName": f"df-test-{suffix}", "activeDeadlineSeconds": 120,
                             "containers": [{"name": "never-start", "image": "invalid.invalid/never-start:test"}]},
                })
                _, output, _ = await self.kubectl("-n", namespace, "get", "pod", "binding-probe", "-o", "json")
                replacement_pod_uid = json.loads(output)["metadata"]["uid"]
                self.assertNotEqual(replacement_pod_uid, expected_pod_uid)
                release_binding.set()
                code, _, error = await delayed_binding
                delayed_binding = None
                self.assertNotEqual(code, 0, "an admitted stale binding bound a replacement pod")
                self.assertIn("uid", error.lower(), f"binding did not fail on its UID precondition: {error}")
                _, output, _ = await self.kubectl("-n", namespace, "get", "pod", "binding-probe", "-o", "json")
                replacement_pod = json.loads(output)
                self.assertEqual(replacement_pod["metadata"]["uid"], replacement_pod_uid)
                self.assertNotIn("nodeName", replacement_pod["spec"], "stale binding modified the replacement pod")

                # Add pod admission after the binding probe so it remains ungated. This is the
                # upstream security-context shape, with a deliberately unusable image and scheduler.
                for kind, operations in (("mutatingwebhookconfiguration", ["CREATE"]),
                                         ("validatingwebhookconfiguration", ["CREATE", "UPDATE"])):
                    patch = [{"op": "add", "path": "/webhooks/0/rules/-", "value": {
                        "apiGroups": [""], "apiVersions": ["v1"], "operations": operations,
                        "resources": ["pods"], "scope": "Namespaced",
                    }}]
                    await self.kubectl("patch", kind, webhook, "--type=json", "--patch", json.dumps(patch))
                mode_probe = {
                    "apiVersion": "v1", "kind": "Pod",
                    "metadata": {"name": "mode-probe", "namespace": namespace, "labels": {
                        "app.kubernetes.io/managed-by": "dark-factory", "dark-factory.io/instance": "dark-factory",
                        "dark-factory.io/task-key": "DF-1",
                    }},
                    "spec": {"schedulerName": f"df-test-{suffix}", "activeDeadlineSeconds": 120,
                             "securityContext": {"runAsNonRoot": True, "runAsUser": 1000,
                                                 "runAsGroup": 1000, "fsGroup": 1000},
                             "containers": [{"name": "never-start", "image": "invalid.invalid/never-start:test"}],
                             "volumes": [{"name": "task-data", "persistentVolumeClaim": {"claimName": "task-df-1"}}]},
                }
                _, output, _ = await self.kubectl("create", "--dry-run=server", "-o", "json", "-f", "-", obj=mode_probe)
                dry_pod = json.loads(output)
                self.assertEqual(dry_pod["spec"]["securityContext"]["fsGroupChangePolicy"], "OnRootMismatch")
                self.assertIn("dark-factory.home.timosur.com/desktop-awake",
                              [gate["name"] for gate in dry_pod["spec"]["schedulingGates"]])
                bad_probe = copy.deepcopy(mode_probe)
                bad_probe["spec"]["securityContext"]["fsGroupChangePolicy"] = "Always"
                code, _, error = await self.kubectl("create", "--dry-run=server", "-f", "-", obj=bad_probe, check=False)
                self.assertNotEqual(code, 0, "explicit Always policy was allowed")
                self.assertIn("sandbox workspace security context is incompatible", error)
                await self.kubectl("create", "-f", "-", obj=mode_probe)
                code, _, _ = await self.kubectl("-n", namespace, "patch", "pod", "mode-probe", "--type=merge",
                                                "--patch", '{"spec":{"securityContext":{"fsGroupChangePolicy":"Always"}}}',
                                                check=False)
                self.assertNotEqual(code, 0, "pod update weakened the filesystem policy")
                _, output, _ = await self.kubectl("-n", namespace, "get", "pod", "mode-probe", "-o", "json")
                persisted_pod = json.loads(output)
                self.assertEqual(persisted_pod["spec"]["securityContext"]["fsGroupChangePolicy"], "OnRootMismatch")
                self.assertNotIn("nodeName", persisted_pod["spec"])

                # The fake class never provisions storage. Simulate a delayed name-only delete
                # racing with deletion and recreation of the same unrelated test claim.
                race_claim = {"apiVersion": "v1", "kind": "PersistentVolumeClaim",
                              "metadata": {"name": "delete-race-probe", "namespace": namespace},
                              "spec": {"accessModes": ["ReadWriteOnce"], "storageClassName": storage_class,
                                       "resources": {"requests": {"storage": "1Gi"}}}}
                await self.kubectl("create", "-f", "-", obj=race_claim)
                _, output, _ = await self.kubectl("-n", namespace, "get", "pvc", "delete-race-probe", "-o", "json")
                original_uid = json.loads(output)["metadata"]["uid"]
                pause_delete = True
                delayed_delete = asyncio.create_task(self.kubectl("-n", namespace, "delete", "pvc", "delete-race-probe",
                                                                  "--wait=false", check=False))
                await asyncio.wait_for(deletion_entered.wait(), timeout=5)
                await self.kubectl("-n", namespace, "delete", "pvc", "delete-race-probe", "--wait=true", "--timeout=6s")
                await self.kubectl("create", "-f", "-", obj=race_claim)
                _, output, _ = await self.kubectl("-n", namespace, "get", "pvc", "delete-race-probe", "-o", "json")
                replacement_uid = json.loads(output)["metadata"]["uid"]
                self.assertNotEqual(original_uid, replacement_uid)
                release_deletion.set()
                delete_code, _, delete_error = await delayed_delete
                delayed_delete = None
                self.assertTrue(delete_code == 0 or "not found" in delete_error.lower()
                                or "conflict" in delete_error.lower(),
                                f"delayed delete did not reach a conclusive API result: {delete_error}")
                code, output, error = await self.kubectl("-n", namespace, "get", "pvc", "delete-race-probe", "-o", "json", check=False)
                if code == 0:
                    replacement = json.loads(output)
                    self.assertEqual(replacement["metadata"]["uid"], replacement_uid)
                    outcome = "marked for deletion" if replacement["metadata"].get("deletionTimestamp") else "still present"
                else:
                    self.assertIn("not found", error.lower())
                    outcome = "deleted"
                print(f"accepted name-only delete race: replacement {outcome}", flush=True)

                await runner.cleanup()
                runner = None
                code, _, error = await self.kubectl("create", "-f", "-", obj=claim("DF-2"), check=False)
                self.assertNotEqual(code, 0, "unavailable admission did not fail closed")
                self.assertIn("failed calling webhook", error)
                code, _, _ = await self.kubectl("-n", namespace, "get", "pvc", "task-df-2", check=False)
                self.assertNotEqual(code, 0, "claim was persisted despite unavailable admission")
                binding["metadata"]["uid"] = replacement_pod_uid
                code, _, error = await self.kubectl(
                    "create", "--raw", f"/api/v1/namespaces/{namespace}/pods/binding-probe/binding?dryRun=All",
                    "-f", "-", obj=binding, check=False,
                )
                self.assertNotEqual(code, 0, "binding passed despite unavailable admission")
                self.assertIn("failed calling webhook", error)
                code, _, error = await self.kubectl("create", "--dry-run=server", "-f", "-", obj=mode_probe, check=False)
                self.assertNotEqual(code, 0, "pod passed despite unavailable admission")
                self.assertIn("failed calling webhook", error)
            finally:
                release_deletion.set()
                release_binding.set()
                if delayed_delete is not None:
                    await asyncio.gather(delayed_delete, return_exceptions=True)
                if delayed_binding is not None:
                    await asyncio.gather(delayed_binding, return_exceptions=True)
                cleanup_errors = []
                for created, kind in ((created_validating, "validatingwebhookconfiguration"),
                                      (created_mutating, "mutatingwebhookconfiguration")):
                    if created:
                        try:
                            code, _, error = await self.kubectl("delete", kind, webhook, "--wait=false", check=False)
                            if code:
                                cleanup_errors.append(f"{kind}/{webhook}: {error}")
                        except Exception as exc:
                            cleanup_errors.append(f"{kind}/{webhook}: {exc}")
                if created_namespace:
                    try:
                        code, _, error = await self.kubectl("delete", "namespace", namespace, "--wait=false", check=False)
                        if code:
                            cleanup_errors.append(f"namespace/{namespace}: {error}")
                    except Exception as exc:
                        cleanup_errors.append(f"namespace/{namespace}: {exc}")
                if created_pv:
                    try:
                        code, _, error = await self.kubectl("delete", "pv", pv, "--wait=false", check=False)
                        if code:
                            cleanup_errors.append(f"pv/{pv}: {error}")
                    except Exception as exc:
                        cleanup_errors.append(f"pv/{pv}: {exc}")
                if tunnel:
                    try:
                        code, _, error = await self.command("ssh", "-S", control, "-O", "exit", target,
                                                            check=False, cwd=directory)
                        if code:
                            cleanup_errors.append(f"SSH tunnel: {error}")
                    except Exception as exc:
                        cleanup_errors.append(f"SSH tunnel: {exc}")
                if runner is not None:
                    await runner.cleanup()
                if cleanup_errors:
                    self.fail("test cleanup needs attention: " + "; ".join(cleanup_errors))
