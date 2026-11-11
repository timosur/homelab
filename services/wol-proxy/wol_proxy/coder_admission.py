"""Fail-closed Coder workspace scheduling admission, separate from factory rules."""

import uuid

from aiohttp import web

from .admission import CONTROLLER, allowed_nodes, gates, response

GATE = "coder.home.timosur.com/pool-awake"
RELEASE_TOKEN = "coder.home.timosur.com/release-token"
PROVISIONER = "system:serviceaccount:coder:coder"


def workspace_id(pod):
    metadata = pod.get("metadata", {})
    labels = metadata.get("labels", {}) if isinstance(metadata, dict) else None
    if not isinstance(labels, dict):
        raise ValueError("invalid Coder workspace labels")
    value = labels.get("coder.workspace.id")
    try:
        if (not isinstance(value, str) or str(uuid.UUID(value)) != value
                or metadata.get("name") != f"coder-{value}"
                or labels.get("app.kubernetes.io/name") != "coder-workspace"
                or labels.get("app.kubernetes.io/component") != "development-workspace"):
            raise ValueError("invalid Coder workspace identity")
    except (ValueError, AttributeError) as exc:
        raise ValueError("invalid Coder workspace identity") from exc
    return value


def protected(pod):
    metadata = pod.get("metadata", {})
    if not isinstance(metadata, dict) or not isinstance(metadata.get("labels", {}), dict):
        raise ValueError("invalid pod metadata")
    labels = metadata.get("labels", {})
    return ("coder.workspace.id" in labels
            or labels.get("app.kubernetes.io/name") == "coder-workspace"
            or labels.get("app.kubernetes.io/component") == "development-workspace")


def review(body, namespace, *, mutate):
    req = body.get("request")
    if not isinstance(req, dict) or not isinstance(req.get("uid"), str):
        raise ValueError("invalid admission request")
    uid, resource = req["uid"], req.get("resource", {})
    if (req.get("namespace") != namespace or not isinstance(resource, dict)
            or resource.get("group", "") != "" or resource.get("version") != "v1"
            or resource.get("resource") != "pods" or req.get("subResource", "")):
        return response(uid, allowed=False, message="unexpected admission target")
    pod, old = req.get("object"), req.get("oldObject")
    if not isinstance(pod, dict) or not isinstance(pod.get("spec"), dict):
        return response(uid, allowed=False, message="invalid pod")
    operation = req.get("operation")
    user = req.get("userInfo", {}).get("username")
    try:
        is_protected = protected(pod) or (isinstance(old, dict) and protected(old))
        if operation == "CREATE" and user == PROVISIONER:
            is_protected = True
        if not is_protected:
            return response(uid, allowed=True)
        current = gates(pod)
        identity = workspace_id(pod)
        if operation == "CREATE":
            if user != PROVISIONER or pod["spec"].get("nodeName"):
                return response(uid, allowed=False, message="workspace creation is not authorized")
            if any(item.get("name") == GATE for item in current):
                return response(uid, allowed=True)
            if not mutate:
                return response(uid, allowed=False, message="Coder scheduling gate is required")
            path = "/spec/schedulingGates/-" if "schedulingGates" in pod["spec"] else "/spec/schedulingGates"
            value = {"name": GATE} if path.endswith("/-") else [{"name": GATE}]
            return response(uid, allowed=True, patch=[{"op": "add", "path": path, "value": value}])
        if operation == "UPDATE" and not mutate:
            if not isinstance(old, dict) or not isinstance(old.get("spec"), dict) or workspace_id(old) != identity:
                return response(uid, allowed=False, message="workspace identity cannot change")
            if pod["spec"].get("nodeName", "") != old["spec"].get("nodeName", ""):
                return response(uid, allowed=False, message="direct node assignment is forbidden")
            previous = gates(old)
            if any(item.get("name") == GATE for item in previous) and not any(item.get("name") == GATE for item in current):
                if user != CONTROLLER or current != [item for item in previous if item.get("name") != GATE]:
                    return response(uid, allowed=False, message="only the controller can remove the workspace gate")
            return response(uid, allowed=True)
    except (TypeError, ValueError, AttributeError):
        return response(uid, allowed=False, message="invalid workspace pod")
    return response(uid, allowed=False, message="unsupported admission operation")


def register(app, namespace, nodes, can_release, binding_pod):
    nodes = allowed_nodes(nodes)

    async def handle(request):
        try:
            body = await request.json()
            req = body["request"]
            uid = req["uid"]
            resource = req.get("resource", {})
            is_binding = req.get("subResource") == "binding" or resource.get("resource") == "bindings"
            if request.path == "/coder/validate" and is_binding:
                if (req.get("namespace") != namespace or req.get("operation") != "CREATE"
                        or resource.get("group", "") != "" or resource.get("version") != "v1"
                        or resource.get("resource") != "pods" or req.get("subResource") != "binding"):
                    return web.json_response(response(uid, allowed=False, message="unsupported binding"))
                binding = req.get("object", {})
                metadata = binding.get("metadata", {})
                name, pod_uid = req.get("name"), metadata.get("uid")
                labels = metadata.get("labels", {})
                if (not isinstance(name, str) or not name or not isinstance(pod_uid, str) or not pod_uid
                        or metadata.get("name", name) != name or not isinstance(labels, dict)
                        or any(key in labels for key in ("coder.workspace.id", "app.kubernetes.io/name", "app.kubernetes.io/component"))):
                    return web.json_response(response(uid, allowed=False, message="invalid binding identity"))
                actual = await binding_pod(name, pod_uid)
                if actual is None:
                    return web.json_response(response(uid, allowed=False, message="binding pod identity is unavailable"))
                if protected(actual):
                    target = binding.get("target", {})
                    if (not isinstance(target, dict) or target.get("kind") != "Node" or target.get("name") not in nodes
                            or any(item.get("name") == GATE for item in gates(actual))):
                        return web.json_response(response(uid, allowed=False, message="workspace binding is not allowed"))
                    workspace_id(actual)
                return web.json_response(response(uid, allowed=True))
            result = review(body, namespace, mutate=request.path == "/coder/mutate")
            if (request.path == "/coder/validate" and req.get("operation") == "UPDATE"
                    and result["response"]["allowed"]):
                old, new = req.get("oldObject"), req.get("object")
                if (isinstance(old, dict) and isinstance(new, dict) and protected(old)
                        and any(item.get("name") == GATE for item in gates(old))
                        and not any(item.get("name") == GATE for item in gates(new))
                        and await can_release(new) is not True):
                    result = response(uid, allowed=False, message="workspace gate release lacks a durable reservation")
            return web.json_response(result)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise web.HTTPBadRequest(text="invalid admission review")

    app.router.add_post("/coder/mutate", handle)
    app.router.add_post("/coder/validate", handle)
