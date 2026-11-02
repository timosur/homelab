"""Fail-closed admission for pods in the Dark Factory sandbox namespace."""

import base64
import json
from collections.abc import Awaitable, Callable

from aiohttp import web

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
        if operation == "CREATE":
            if pod["spec"].get("nodeName"):
                return response(uid, allowed=False, message="pre-bound sandbox pods are not permitted")
            if any(item.get("name") == GATE for item in current):
                return response(uid, allowed=True)
            if not mutate:
                return response(uid, allowed=False, message="desktop scheduling gate is required")
            if "schedulingGates" in pod["spec"]:
                patch = [{"op": "add", "path": "/spec/schedulingGates/-", "value": {"name": GATE}}]
            else:
                patch = [{"op": "add", "path": "/spec/schedulingGates", "value": [{"name": GATE}]}]
            return response(uid, allowed=True, patch=patch)

        if operation == "UPDATE" and not mutate:
            old = request.get("oldObject")
            if not isinstance(old, dict):
                return response(uid, allowed=False, message="old pod is required")
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


async def review_binding(body: dict, namespace: str, node: str, can_bind: Callable[[str], Awaitable[bool]]) -> dict:
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
    metadata = binding.get("metadata", {})
    if not isinstance(metadata, dict):
        return response(uid, allowed=False, message="invalid binding metadata")
    target = binding.get("target", {})
    if not isinstance(target, dict) or target.get("kind") != "Node" or target.get("name") != node:
        return response(uid, allowed=False, message="binding must target the designated desktop node")
    name = request.get("name") or metadata.get("name")
    if not isinstance(name, str) or not name or (metadata.get("name") and metadata["name"] != name):
        return response(uid, allowed=False, message="binding pod name is missing or inconsistent")
    if not await can_bind(name):
        return response(uid, allowed=False, message="desktop scheduling gate is still present")
    return response(uid, allowed=True)


def admission_app(
    namespace: str, node: str, can_bind: Callable[[str], Awaitable[bool]] | None = None,
    can_release: Callable[[dict], Awaitable[bool]] | None = None,
) -> web.Application:
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
            if request.path == "/validate" and is_binding:
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
