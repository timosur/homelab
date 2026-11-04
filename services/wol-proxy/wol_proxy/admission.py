"""Fail-closed admission for pods in the Dark Factory sandbox namespace."""

import base64
import json
from collections.abc import Awaitable, Callable

from aiohttp import web

from .workspace_security import workspace_security

GATE = "dark-factory.home.timosur.com/desktop-awake"
RELEASE_TOKEN = "dark-factory.home.timosur.com/release-token"
CONTROLLER = "system:serviceaccount:wol-proxy:wol-proxy"


def gates(pod: dict) -> list[dict]:
    value = pod.get("spec", {}).get("schedulingGates", [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("invalid scheduling gates")
    return value


def response(uid: str, *, allowed: bool, message: str = "", patch: list | None = None) -> dict:
    result = {"uid": uid, "allowed": allowed}
    if message:
        result["status"] = {"message": message}
    if patch is not None:
        result["patchType"] = "JSONPatch"
        result["patch"] = base64.b64encode(json.dumps(patch).encode()).decode()
    return {"apiVersion": "admission.k8s.io/v1", "kind": "AdmissionReview", "response": result}


def protected_pod(pod: dict) -> bool:
    metadata, spec = pod.get("metadata", {}), pod.get("spec", {})
    if not isinstance(metadata, dict) or not isinstance(spec, dict):
        raise ValueError("invalid sandbox pod")
    labels = metadata.get("labels", {})
    if not isinstance(labels, dict) or not all(isinstance(key, str) for key in labels):
        raise ValueError("invalid sandbox labels")
    if (labels.get("app.kubernetes.io/managed-by") == "dark-factory"
            or any(key.startswith("dark-factory.io/") for key in labels)):
        return True
    volumes = spec.get("volumes", [])
    if not isinstance(volumes, list) or not all(isinstance(volume, dict) for volume in volumes):
        raise ValueError("invalid sandbox volumes")
    for volume in volumes:
        claim = volume.get("persistentVolumeClaim")
        if isinstance(claim, dict) and isinstance(claim.get("claimName"), str) and claim["claimName"].startswith("task-"):
            return True
    return False


def review(body: dict, namespace: str, *, mutate: bool) -> dict:
    request = body.get("request")
    if not isinstance(request, dict) or not isinstance(request.get("uid"), str):
        raise ValueError("invalid admission request")
    uid = request["uid"]
    resource = request.get("resource", {})
    if (
        request.get("namespace") != namespace
        or resource.get("group", "") != ""
        or resource.get("version") != "v1"
        or resource.get("resource") != "pods"
        or request.get("subResource", "")
    ):
        return response(uid, allowed=False, message="unexpected admission target")

    operation = request.get("operation")
    pod = request.get("object")
    if not isinstance(pod, dict) or not isinstance(pod.get("spec"), dict):
        return response(uid, allowed=False, message="invalid pod")
    try:
        current = gates(pod)
        old = request.get("oldObject")
        protected = protected_pod(pod) or (operation == "UPDATE" and isinstance(old, dict) and protected_pod(old))
        patch = []
        if protected:
            allow_missing = operation == "CREATE" and mutate
            if not workspace_security(pod["spec"], allow_missing_policy=allow_missing):
                return response(uid, allowed=False, message="sandbox workspace security context is incompatible")
            if allow_missing and pod["spec"]["securityContext"].get("fsGroupChangePolicy") is None:
                patch.append({"op": "add", "path": "/spec/securityContext/fsGroupChangePolicy", "value": "OnRootMismatch"})
        if operation == "CREATE":
            if pod["spec"].get("nodeName"):
                return response(uid, allowed=False, message="pre-bound sandbox pods are not permitted")
            if any(item.get("name") == GATE for item in current):
                return response(uid, allowed=True, patch=patch or None)
            if not mutate:
                return response(uid, allowed=False, message="desktop scheduling gate is required")
            if "schedulingGates" in pod["spec"]:
                patch.append({"op": "add", "path": "/spec/schedulingGates/-", "value": {"name": GATE}})
            else:
                patch.append({"op": "add", "path": "/spec/schedulingGates", "value": [{"name": GATE}]})
            return response(uid, allowed=True, patch=patch)

        if operation == "UPDATE" and not mutate:
            if not isinstance(old, dict) or not isinstance(old.get("spec"), dict):
                return response(uid, allowed=False, message="old pod is required")
            if pod["spec"].get("nodeName", "") != old["spec"].get("nodeName", ""):
                return response(uid, allowed=False, message="direct node assignment is not permitted")
            had_gate = any(item.get("name") == GATE for item in gates(old))
            has_gate = any(item.get("name") == GATE for item in current)
            if had_gate and not has_gate:
                username = request.get("userInfo", {}).get("username")
                if username != CONTROLLER:
                    return response(uid, allowed=False, message="only the wake controller can remove the desktop gate")
                old_other = [item for item in gates(old) if item.get("name") != GATE]
                if current != old_other:
                    return response(uid, allowed=False, message="the wake controller may only remove the desktop gate")
            return response(uid, allowed=True)
    except (TypeError, ValueError):
        return response(uid, allowed=False, message="invalid scheduling gates")
    return response(uid, allowed=False, message="unsupported admission operation")


def allowed_nodes(nodes: str | tuple[str, ...]) -> frozenset[str]:
    if isinstance(nodes, str):
        nodes = (nodes,)
    if not isinstance(nodes, tuple) or not nodes or not all(isinstance(node, str) and node for node in nodes):
        raise ValueError("sandbox worker nodes must be explicitly specified")
    if len(set(nodes)) != len(nodes):
        raise ValueError("sandbox worker nodes must be distinct")
    return frozenset(nodes)


def protected_workspace(metadata: dict) -> bool:
    name, labels = metadata.get("name", ""), metadata.get("labels", {})
    return (
        name.startswith("task-") or labels.get("app.kubernetes.io/managed-by") == "dark-factory"
        or "dark-factory.io/instance" in labels or "dark-factory.io/task-key" in labels
    )


def review_workspace(body: dict, namespace: str, instance: str, storage_class: str, *, mutate: bool,
                     delete_actor: str = "") -> dict:
    request = body.get("request")
    if not isinstance(request, dict) or not isinstance(request.get("uid"), str):
        raise ValueError("invalid admission request")
    uid = request["uid"]
    resource = request.get("resource", {})
    operation = request.get("operation")
    claim = request.get("oldObject") if operation == "DELETE" else request.get("object")
    if (
        request.get("namespace") != namespace or operation not in ("CREATE", "UPDATE", "DELETE")
        or (mutate and operation == "DELETE")
        or not isinstance(resource, dict) or resource.get("group", "") != ""
        or resource.get("version") != "v1" or resource.get("resource") != "persistentvolumeclaims"
        or request.get("subResource", "") or not isinstance(claim, dict)
    ):
        return response(uid, allowed=False, message="unexpected workspace admission target")
    metadata, spec = claim.get("metadata"), claim.get("spec")
    if not isinstance(metadata, dict) or not isinstance(spec, dict):
        return response(uid, allowed=False, message="invalid workspace claim")
    labels = metadata.get("labels", {})
    name = metadata.get("name")
    if not isinstance(labels, dict) or not isinstance(name, str) or not name:
        return response(uid, allowed=False, message="invalid workspace identity")
    protected = protected_workspace(metadata)
    if operation == "DELETE":
        if not protected:
            return response(uid, allowed=True)
        task_key = labels.get("dark-factory.io/task-key")
        user = request.get("userInfo")
        if (not isinstance(delete_actor, str) or not delete_actor or not isinstance(user, dict)
                or user.get("username") != delete_actor
                or not isinstance(metadata.get("uid"), str) or not metadata["uid"]
                or labels.get("app.kubernetes.io/managed-by") != "dark-factory"
                or labels.get("dark-factory.io/instance") != instance
                or not isinstance(task_key, str) or not task_key or not task_key.isascii()
                or name != f"task-{task_key.lower()}"
                or spec.get("storageClassName") != storage_class
                or spec.get("accessModes") != ["ReadWriteOncePod"]):
            return response(uid, allowed=False, message="workspace deletion is not authorized or claim is incompatible")
        return response(uid, allowed=True)
    if request["operation"] == "UPDATE":
        old = request.get("oldObject")
        if not isinstance(old, dict) or not isinstance(old.get("metadata"), dict) or not isinstance(old.get("spec"), dict):
            return response(uid, allowed=False, message="old workspace claim is required")
        old_metadata, old_spec = old["metadata"], old["spec"]
        old_labels, old_name = old_metadata.get("labels", {}), old_metadata.get("name")
        if not isinstance(old_labels, dict) or not isinstance(old_name, str) or not old_name:
            return response(uid, allowed=False, message="invalid old workspace identity")
        old_protected = protected_workspace(old_metadata)
        if old_protected or protected:
            if (
                not old_protected or not protected or old_name != name
                or old_labels.get("app.kubernetes.io/managed-by") != "dark-factory"
                or old_labels.get("dark-factory.io/instance") != instance
                or not old_labels.get("dark-factory.io/task-key")
                or old_labels.get("dark-factory.io/task-key") != labels.get("dark-factory.io/task-key")
                or old_spec.get("storageClassName") != storage_class
                or old_spec.get("accessModes") != ["ReadWriteOncePod"]
            ):
                return response(uid, allowed=False, message="existing workspace is incompatible or identity changed")
    if not protected:
        return response(uid, allowed=True)
    task_key = labels.get("dark-factory.io/task-key")
    if (
        labels.get("app.kubernetes.io/managed-by") != "dark-factory"
        or labels.get("dark-factory.io/instance") != instance
        or not isinstance(task_key, str) or not task_key or not task_key.isascii()
        or name != f"task-{task_key.lower()}"
        or spec.get("storageClassName") != storage_class
    ):
        return response(uid, allowed=False, message="invalid workspace identity or storage class")
    if mutate and spec.get("accessModes") == ["ReadWriteOnce"]:
        return response(uid, allowed=True, patch=[
            {"op": "replace", "path": "/spec/accessModes", "value": ["ReadWriteOncePod"]},
        ])
    if spec.get("accessModes") == ["ReadWriteOncePod"]:
        return response(uid, allowed=True)
    return response(uid, allowed=False, message="single-pod workspace access is required")


async def review_binding(body: dict, namespace: str, node: str | tuple[str, ...], can_bind: Callable[[str, str], Awaitable[bool]]) -> dict:
    request = body.get("request")
    if not isinstance(request, dict) or not isinstance(request.get("uid"), str):
        raise ValueError("invalid admission request")
    uid = request["uid"]
    resource = request.get("resource", {})
    binding = request.get("object")
    is_binding = (
        resource.get("resource") == "pods" and request.get("subResource") == "binding"
    ) or (
        resource.get("resource") == "bindings" and not request.get("subResource")
    )
    if (
        request.get("namespace") != namespace
        or request.get("operation") != "CREATE"
        or resource.get("group", "") != ""
        or resource.get("version") != "v1"
        or not is_binding
        or not isinstance(binding, dict)
    ):
        return response(uid, allowed=False, message="unexpected binding request")
    # Kubernetes strips UID preconditions from legacy /bindings admission requests.
    if resource.get("resource") == "bindings":
        return response(uid, allowed=False, message="legacy binding is not permitted")
    metadata = binding.get("metadata", {})
    if not isinstance(metadata, dict):
        return response(uid, allowed=False, message="invalid binding metadata")
    target = binding.get("target", {})
    if not isinstance(target, dict) or target.get("kind") != "Node" or target.get("name") not in allowed_nodes(node):
        return response(uid, allowed=False, message="binding must target a designated sandbox worker")
    name = request.get("name") or metadata.get("name")
    if not isinstance(name, str) or not name or (metadata.get("name") and metadata["name"] != name):
        return response(uid, allowed=False, message="binding pod name is missing or inconsistent")
    pod_uid = metadata.get("uid")
    if not isinstance(pod_uid, str) or not pod_uid:
        return response(uid, allowed=False, message="binding pod UID is required")
    labels = metadata.get("labels", {})
    if (not isinstance(labels, dict)
            or any(not isinstance(key, str) or key == "app.kubernetes.io/managed-by"
                   or key.startswith("dark-factory.io/") for key in labels)):
        return response(uid, allowed=False, message="binding cannot change workspace identity labels")
    if not await can_bind(name, pod_uid):
        return response(uid, allowed=False, message="desktop scheduling gate is still present")
    return response(uid, allowed=True)


def admission_app(
    namespace: str, node: str | tuple[str, ...], can_bind: Callable[[str, str], Awaitable[bool]] | None = None,
    can_release: Callable[[dict], Awaitable[bool]] | None = None,
    *, workspace_instance: str = "", workspace_storage_class: str = "", workspace_delete_actor: str = "",
    can_delete: Callable[[dict], Awaitable[bool]] | None = None,
) -> web.Application:
    allowed_nodes(node)  # Reject incomplete or duplicate worker configuration at startup.
    app = web.Application(client_max_size=1024 * 1024)

    async def handle(request: web.Request) -> web.Response:
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("invalid admission review")
            admission_request = body.get("request", {})
            if not isinstance(admission_request, dict):
                raise ValueError("invalid admission request")
            resource = admission_request.get("resource", {})
            if not isinstance(resource, dict):
                raise ValueError("invalid admission resource")
            is_binding = admission_request.get("subResource") == "binding" or resource.get("resource") == "bindings"
            if resource.get("resource") == "persistentvolumeclaims":
                if not workspace_instance or not workspace_storage_class:
                    result = response(admission_request["uid"], allowed=False, message="workspace admission is unavailable")
                else:
                    result = review_workspace(body, namespace, workspace_instance, workspace_storage_class,
                                              mutate=request.path == "/mutate", delete_actor=workspace_delete_actor)
                    if (admission_request.get("operation") == "DELETE" and result["response"]["allowed"]
                            and protected_workspace(admission_request["oldObject"]["metadata"])):
                        if can_delete is None or await can_delete(admission_request["oldObject"]) is not True:
                            result = response(admission_request["uid"], allowed=False,
                                              message="workspace is in use or deletion checks are unavailable")
            elif request.path == "/validate" and is_binding:
                if can_bind is None:
                    raise ValueError("binding validation is unavailable")
                result = await review_binding(body, namespace, node, can_bind)
            else:
                result = review(body, namespace, mutate=request.path == "/mutate")
                if request.path == "/validate" and admission_request.get("operation") == "UPDATE" and result["response"]["allowed"]:
                    old, new = admission_request.get("oldObject"), admission_request.get("object")
                    if isinstance(old, dict) and isinstance(new, dict):
                        removed = any(g.get("name") == GATE for g in gates(old)) and not any(g.get("name") == GATE for g in gates(new))
                        if removed and (can_release is None or not await can_release(new)):
                            result = response(admission_request["uid"], allowed=False, message="gate release lacks the current durable reservation")
            return web.json_response(result)
        except (ValueError, TypeError, json.JSONDecodeError):
            # A malformed review cannot be safely authorized. HTTP failure is fail-closed.
            raise web.HTTPBadRequest(text="invalid admission review")

    app.router.add_post("/mutate", handle)
    app.router.add_post("/validate", handle)
    return app
