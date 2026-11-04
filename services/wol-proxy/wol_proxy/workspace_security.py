"""Security context required for a managed task workspace."""


def workspace_security(spec, *, allow_missing_policy=False):
    context = spec.get("securityContext") if isinstance(spec, dict) else None
    if not isinstance(context, dict):
        return False
    for field in ("runAsUser", "runAsGroup", "fsGroup"):
        if type(context.get(field)) is not int or context[field] != 1000:
            return False
    if context.get("runAsNonRoot") is not True:
        return False
    policy = context.get("fsGroupChangePolicy")
    if policy != "OnRootMismatch" and not (allow_missing_policy and policy is None):
        return False
    # Container-level user and group settings override the pod-level defaults.
    for field in ("containers", "initContainers", "ephemeralContainers"):
        containers = spec.get(field, [])
        if not isinstance(containers, list) or not all(isinstance(item, dict) for item in containers):
            return False
        for container in containers:
            security = container.get("securityContext")
            if security is None:
                continue
            if not isinstance(security, dict):
                return False
            for key in ("runAsUser", "runAsGroup"):
                if key in security and (type(security[key]) is not int or security[key] != 1000):
                    return False
            if "runAsNonRoot" in security and security["runAsNonRoot"] is not True:
                return False
    return True
