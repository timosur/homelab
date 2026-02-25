#!/usr/bin/env python3
"""Bootstrap Authentik on Kubernetes with bike-weather OAuth2 applications.

Run after Authentik is deployed and healthy in the bike-weather-auth namespace.
Creates OAuth2 providers and applications for both production and preview environments.
Stores the API token in Azure Key Vault.

Idempotent — safe to re-run.

Prerequisites:
    - kubectl configured with cluster access
    - az CLI logged in (for Key Vault updates)
    - Authentik initial setup completed (akadmin password set via web UI)

Usage:
    python3 scripts/setup_authentik_homelab.py
    python3 scripts/setup_authentik_homelab.py --base-url https://auth.bike-weather.com
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error

NAMESPACE = "bike-weather-auth"
VAULT_NAME = "homelab-timosur"

APPS = [
    {
        "name": "Bike Weather",
        "slug": "bike-weather",
        "client_id": "bike-weather",
        "redirect_uri": "https://bike-weather.com/auth/callback",
        "launch_url": "https://bike-weather.com",
        "keyvault_token_key": "bike-weather-authentik-api-token",
    },
    {
        "name": "Bike Weather Preview",
        "slug": "bike-weather-preview",
        "client_id": "bike-weather-preview",
        "redirect_uri": "https://preview.bike-weather.com/auth/callback",
        "launch_url": "https://preview.bike-weather.com",
        "keyvault_token_key": "bike-weather-preview-authentik-api-token",
    },
]

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
}


def api(base: str, method: str, path: str, data=None):
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(
        f"{base}{path}", data=body, headers=HEADERS, method=method
    )
    try:
        with urllib.request.urlopen(req) as resp:
            content = resp.read()
            if not content:
                return {}
            return json.loads(content)
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        print(f"  ERROR {e.code}: {err[:300]}")
        try:
            return json.loads(err)
        except json.JSONDecodeError:
            return {}


def ensure_api_token() -> str:
    """Create an API token via kubectl exec into the Authentik server pod."""
    # Find the server pod
    result = subprocess.run(
        [
            "kubectl",
            "get",
            "pods",
            "-n",
            NAMESPACE,
            "-l",
            "app=bike-weather-auth-server",
            "-o",
            "jsonpath={.items[0].metadata.name}",
        ],
        capture_output=True,
        text=True,
    )
    pod_name = result.stdout.strip()
    if not pod_name:
        print(f"ERROR: No Authentik server pod found in namespace {NAMESPACE}")
        print(f"  stdout: {result.stdout}")
        print(f"  stderr: {result.stderr}")
        sys.exit(1)

    print(f"   Using pod: {pod_name}")

    code = (
        "import os, django; "
        "os.environ['DJANGO_SETTINGS_MODULE']='authentik.root.settings'; "
        "django.setup(); "
        "from authentik.core.models import Token, TokenIntents, User; "
        "u=User.objects.get(username='akadmin'); "
        "t,_=Token.objects.get_or_create("
        "identifier='bike-weather-homelab-setup',"
        "defaults={'user':u,'intent':TokenIntents.INTENT_API,'expiring':False}); "
        "print(t.key)"
    )
    result = subprocess.run(
        [
            "kubectl",
            "exec",
            "-n",
            NAMESPACE,
            pod_name,
            "--",
            "python",
            "-c",
            code,
        ],
        capture_output=True,
        text=True,
    )
    token = result.stdout.strip().split("\n")[-1]
    if not token or len(token) < 10:
        print(f"Failed to get API token.")
        print(f"  stdout: {result.stdout}")
        print(f"  stderr: {result.stderr}")
        sys.exit(1)
    return token


def set_keyvault_secret(key: str, value: str):
    """Store a secret in Azure Key Vault."""
    result = subprocess.run(
        [
            "az",
            "keyvault",
            "secret",
            "set",
            "--vault-name",
            VAULT_NAME,
            "--name",
            key,
            "--value",
            value,
            "--output",
            "none",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  WARNING: Failed to set Key Vault secret {key}: {result.stderr}")
        return False
    return True


def provision_app(
    base: str,
    app_config: dict,
    auth_flow: str,
    invalidation_flow: str,
    cert_pk: str,
    mappings: list,
) -> int | None:
    """Create or find an OAuth2 provider + application. Returns provider pk."""
    slug = app_config["slug"]
    client_id = app_config["client_id"]

    # Create or find OAuth2 provider
    print(f"\n   Provisioning OAuth2 provider: {slug}...")
    existing = api(base, "GET", f"/api/v3/providers/oauth2/?search={slug}")
    if existing.get("results"):
        provider_pk = existing["results"][0]["pk"]
        print(f"   Provider already exists (pk={provider_pk})")
    else:
        provider = api(
            base,
            "POST",
            "/api/v3/providers/oauth2/",
            {
                "name": app_config["name"],
                "authorization_flow": auth_flow,
                "invalidation_flow": invalidation_flow,
                "client_type": "public",
                "client_id": client_id,
                "redirect_uris": [
                    {
                        "matching_mode": "strict",
                        "url": app_config["redirect_uri"],
                    },
                ],
                "signing_key": cert_pk,
                "access_code_validity": "minutes=10",
                "access_token_validity": "hours=1",
                "refresh_token_validity": "days=30",
                "sub_mode": "hashed_user_id",
                "include_claims_in_id_token": True,
                "property_mappings": mappings,
            },
        )
        provider_pk = provider.get("pk")
        if not provider_pk:
            print(f"   FAILED to create provider for {slug}!")
            return None
        print(f"   Provider created (pk={provider_pk})")

    # Create or find application
    print(f"   Provisioning application: {slug}...")
    existing_app = api(base, "GET", f"/api/v3/core/applications/?slug={slug}")
    if existing_app.get("results"):
        print(f"   Application already exists")
    else:
        app_result = api(
            base,
            "POST",
            "/api/v3/core/applications/",
            {
                "name": app_config["name"],
                "slug": slug,
                "provider": provider_pk,
                "open_in_new_tab": False,
                "meta_launch_url": app_config["launch_url"],
            },
        )
        if app_result.get("slug"):
            print(f"   Application created: {app_result['slug']}")
        else:
            print(f"   FAILED to create application for {slug}!")
            return None

    return provider_pk


def main():
    parser = argparse.ArgumentParser(
        description="Bootstrap Authentik for bike-weather homelab"
    )
    parser.add_argument(
        "--base-url",
        default="https://auth.bike-weather.com",
        help="Authentik base URL (default: https://auth.bike-weather.com)",
    )
    parser.add_argument(
        "--skip-keyvault",
        action="store_true",
        help="Skip writing API token to Azure Key Vault",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    print("=== Authentik Bootstrap for bike-weather (Homelab) ===\n")
    print(f"   Authentik URL: {base}")
    print(f"   K8s namespace: {NAMESPACE}")
    print(f"   Key Vault:     {VAULT_NAME}")

    # 1. Obtain API token via kubectl exec
    print("\n1. Obtaining API token via kubectl exec...")
    token = ensure_api_token()
    HEADERS["Authorization"] = f"Bearer {token}"
    print(f"   Token obtained (***{token[-6:]})")

    # 2. Get signing certificate
    print("\n2. Getting signing certificate...")
    certs = api(
        base,
        "GET",
        "/api/v3/crypto/certificatekeypairs/?name=authentik+Self-signed+Certificate",
    )
    if not certs.get("results"):
        print("   ERROR: No self-signed certificate found!")
        sys.exit(1)
    cert_pk = certs["results"][0]["pk"]
    print(f"   Certificate: {cert_pk}")

    # 3. Get flows
    print("\n3. Getting flows...")
    flows = api(base, "GET", "/api/v3/flows/instances/")
    auth_flow = invalidation_flow = None
    for f in flows.get("results", []):
        if f["slug"] == "default-provider-authorization-implicit-consent":
            auth_flow = f["pk"]
        if f["slug"] == "default-provider-invalidation-flow":
            invalidation_flow = f["pk"]
    if not auth_flow or not invalidation_flow:
        print("   ERROR: Required flows not found!")
        sys.exit(1)
    print(f"   Authorization flow: {auth_flow}")
    print(f"   Invalidation flow:  {invalidation_flow}")

    # 4. Get scope mappings
    print("\n4. Getting scope mappings...")
    scopes = api(base, "GET", "/api/v3/propertymappings/provider/scope/")
    scope_map = {s["scope_name"]: s["pk"] for s in scopes.get("results", [])}
    mappings = [scope_map[n] for n in ("openid", "profile", "email") if n in scope_map]
    print(f"   Mapped scopes: openid, profile, email")

    # 5. Provision each application
    print("\n5. Provisioning applications...")
    for app_config in APPS:
        provider_pk = provision_app(
            base, app_config, auth_flow, invalidation_flow, cert_pk, mappings
        )
        if provider_pk is None:
            print(f"\n   WARNING: Skipping {app_config['slug']} due to errors")

    # 6. Verify OIDC discovery endpoints
    print("\n6. Verifying OIDC discovery endpoints...")
    for app_config in APPS:
        slug = app_config["slug"]
        try:
            req = urllib.request.Request(
                f"{base}/application/o/{slug}/.well-known/openid-configuration"
            )
            with urllib.request.urlopen(req) as resp:
                config = json.loads(resp.read())
                print(f"   {slug}:")
                print(f"     Issuer: {config.get('issuer')}")
                print(f"     OK")
        except urllib.error.HTTPError as e:
            print(f"   {slug}: FAILED ({e.code})")

    # 7. Store API token in Azure Key Vault
    if not args.skip_keyvault:
        print("\n7. Storing API token in Azure Key Vault...")
        for app_config in APPS:
            key = app_config["keyvault_token_key"]
            if set_keyvault_secret(key, token):
                print(f"   SET  {key}")
            else:
                print(f"   FAIL {key}")
    else:
        print("\n7. Skipping Key Vault update (--skip-keyvault)")

    # Done
    print("\n=== Done! ===")
    print(f"\n   Authentik admin: {base}/if/admin/")
    print(f"   Production app:  https://bike-weather.com")
    print(f"   Preview app:     https://preview.bike-weather.com")
    print(f"   API Token:       ***{token[-6:]}")


if __name__ == "__main__":
    main()
