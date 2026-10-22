import json
import os
import ssl
import sys
from pathlib import Path
from urllib.request import Request, urlopen


CONFIG = json.loads(Path("/config/routes.json").read_text())


def items(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return next((item for item in value.values() if isinstance(item, list)), [])
    return []


def request(url, method="GET", data=None, headers=None, context=None, wrapped=True):
    body = json.dumps(data).encode() if data is not None else None
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    with urlopen(Request(url, data=body, method=method, headers=request_headers), timeout=20, context=context) as response:
        result = json.load(response)
        return result.get("data") if wrapped else result


def service_ip(service):
    token = Path("/var/run/secrets/kubernetes.io/serviceaccount/token").read_text()
    ca = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    url = f"https://kubernetes.default.svc/api/v1/namespaces/{service['namespace']}/services/{service['name']}"
    result = request(
        url,
        headers={"Authorization": f"Bearer {token}"},
        context=ssl.create_default_context(cafile=ca),
        wrapped=False,
    )
    ports = result["spec"].get("ports", [])
    if not any(port["port"] == service["port"] for port in ports):
        raise RuntimeError(f"{service['namespace']}/{service['name']} has no port {service['port']}")
    return result["spec"]["clusterIP"]


def api(method, path, data=None):
    return request(
        f"{CONFIG['api']}{path}",
        method,
        data,
        {"Authorization": f"Bearer {os.environ['PANGOLIN_API_KEY']}"},
    )


def reconcile(route):
    domains = items(api("GET", f"/org/{CONFIG['organization']}/domains"))
    domain = next((item for item in domains if item["baseDomain"] == route["domain"]), None)
    if not domain:
        raise RuntimeError(f"Pangolin domain not found: {route['domain']}")

    hostname = f"{route['subdomain']}.{route['domain']}"
    resources = items(api("GET", f"/org/{CONFIG['organization']}/public-resources"))
    resource = next((item for item in resources if item.get("fullDomain") == hostname), None)
    if resource and resource.get("name") != route["name"]:
        raise RuntimeError(f"refusing to adopt {hostname}: name does not match")
    if not resource:
        resource = api("PUT", f"/org/{CONFIG['organization']}/public-resource", {
            "name": route["name"],
            "http": True,
            "subdomain": route["subdomain"],
            "domainId": domain["domainId"],
            "protocol": "tcp",
        })

    if route.get("public"):
        rules = items(api("GET", f"/public-resource/{resource['resourceId']}/rules"))
        if not any(rule.get("action") == "ACCEPT" and rule.get("match") == "CIDR" and rule.get("value") == "0.0.0.0/0" for rule in rules):
            api("PUT", f"/public-resource/{resource['resourceId']}/rule", {
                "action": "ACCEPT",
                "match": "CIDR",
                "value": "0.0.0.0/0",
                "priority": 0,
                "enabled": True,
            })

    ip = service_ip(route["service"])
    port = route["service"]["port"]
    targets = items(api("GET", f"/public-resource/{resource['resourceId']}/targets"))
    wanted = lambda target: (
        target.get("siteId") == CONFIG["siteId"]
        and target.get("ip") == ip
        and target.get("port") == port
        and target.get("method") == "http"
    )
    if not any(wanted(target) for target in targets):
        for target in targets:
            api("DELETE", f"/target/{target['targetId']}")
        api("PUT", f"/public-resource/{resource['resourceId']}/target", {
            "siteId": CONFIG["siteId"],
            "ip": ip,
            "port": port,
            "method": "http",
        })
    print(f"{hostname} -> {ip}:{port}")


if "--self-check" in sys.argv:
    assert items({"routes": [1]}) == [1]
    assert f"{CONFIG['routes'][0]['subdomain']}.{CONFIG['routes'][0]['domain']}" == "portfolio.pangolin.timosur.com"
else:
    for route in CONFIG["routes"]:
        reconcile(route)
