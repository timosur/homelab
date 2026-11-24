# OIDC app blueprints

New OAuth2/OIDC providers are declared as authentik blueprints in this directory
(not via `scripts/setup_authentik_homelab.py --provision-oidc`, which only covers Coder and Dark Factory).
Reference: `open-webui.yaml`. Blueprint schema: https://docs.goauthentik.io/customize/blueprints/v1/structure/
(`!Env`, `!File`, `!Find`, `!KeyOf` tags).

## Prerequisites (authentik objects the blueprint looks up with `!Find`)

The apply fails if any of these is missing: flows `default-provider-authorization-implicit-consent`
and `default-provider-invalidation-flow`, the signing certificate `authentik Self-signed Certificate`,
the default OpenID scope mappings (`openid`, `email`, `profile`) and the group `board-member`.
Blueprints must not depend on objects created by `scripts/setup_authentik_homelab.py`
(e.g. the "Dark Factory groups" mapping); open-webui does not use groups.

## Rollout order (first deployment of an app)

1. Create the Key Vault secret (step 1 below).
2. Let ArgoCD sync (ExternalSecrets create `authentik-oidc-secrets` and the app's secret).
3. Restart the worker (`kubectl -n authentik rollout restart deploy/authentik-worker`) so it sees the env var.
4. **Apply the blueprint manually.** The restart alone may not re-apply it, because the file hash has not
   changed since the run in which all entries were skipped. Use the Apply button on
   Customization > Blueprints, or run `ak apply_blueprint /blueprints/oidc/<app>.yaml` in the worker
   (`kubectl -n authentik exec deploy/authentik-worker -- ak apply_blueprint /blueprints/oidc/open-webui.yaml`).

Rotating the secret (new Key Vault value, ExternalSecret refresh, worker restart) also needs this manual
apply, because the blueprint file hash does not change. Restart the app pod too.

Safety: the worker's `secretKeyRef` is `optional: true` (the worker uses `Recreate`, so a missing
Key Vault entry must not block it), and every blueprint entry has `conditions: [!Env [VAR, ""]]`,
so while the variable is unset or empty the entries are skipped and no provider with an empty
secret is created. Also create the secret *before* the app's own sync: if the entry is missing,
the app ExternalSecret errors and the app's new `oidc-client-secret` key is absent.

## Verify the apply

- Worker logs: `kubectl -n authentik logs deploy/authentik-worker | grep -i blueprint`
- Blueprint status `successful` is **not** proof: it is also shown when every entry was skipped
  (secret unset or empty).
- Success check: Admin UI > Applications lists `open-webui` with its provider, and
  `curl -s -o /dev/null -w '%{http_code}\n' https://auth.timosur.com/application/o/open-webui/.well-known/openid-configuration`
  returns `200`.

## Open WebUI user behaviour

- Existing local accounts are **not** merged with SSO users by email (`OAUTH_MERGE_ACCOUNTS_BY_EMAIL`
  is intentionally unset; the owner may decide later). An SSO user whose email matches a local account
  fails to sign in.
- New SSO users are created as `pending` until an admin approves them. `DEFAULT_USER_ROLE` is left
  unchanged because local signup on the public URL inherits it.
- Local login stays enabled (break-glass admin).
- PersistentConfig: on current Open WebUI the `oauth.*` settings are read from the env vars unless
  `ENABLE_OAUTH_PERSISTENT_CONFIG` is `true` (default `false`), so the database does not override the
  OIDC env vars here. Do not set `ENABLE_OAUTH_PERSISTENT_CONFIG=true`. Only `WEBUI_URL` stays in the
  database once an admin has saved it in the UI; after that the stored value wins over the env var.
- `/oauth/oidc/callback` is a legacy route on Open WebUI `main` (the current one is
  `/oauth/oidc/login/callback`); the old route still works, so it is kept. If the route is ever changed,
  the blueprint's `redirect_uris` and `OPENID_REDIRECT_URI` must change together.

## Add the next app

1. **Create the secret in Key Vault** (human step, never in Git):
   ```
   az keyvault secret set --vault-name homelab-timosur \
     --name <app>-oidc-client-secret --value "$(openssl rand -hex 32)"
   ```
2. Copy `open-webui.yaml` to `<app>.yaml`; change the provider name, `client_id`, `grant_types`,
   `redirect_uris` (strict), application slug/name/launch URL and the `!Env` variable name. Keep
   `grant_types: [authorization_code, refresh_token]` (see "Known failure mode" below). Rename that variable in
   **every** entry's `conditions` too, not only in `client_secret`; otherwise the entries are gated on
   the wrong secret.
3. Add the file to the `authentik-oidc-blueprints` generator in `../kustomization.yaml`.
4. Add the key to `../oidc-external-secret.yaml` and as an env var (`secretKeyRef` on
   `authentik-oidc-secrets`) in `../worker-deployment.yaml`. Only the worker loads blueprints.
5. In the app namespace, add an ExternalSecret entry reading the **same** Key Vault name and
   configure the app with issuer `https://auth.timosur.com/application/o/<slug>/`.
6. The worker pod must restart to pick up a new env var. If the secret was set after the blueprint
   was first applied with all entries skipped, apply the blueprint manually (see rollout step 4).

Blueprints are mounted at `/blueprints/oidc/`; the worker discovers `/blueprints` recursively.

## Known failure mode: empty `grant_types`

Symptom: the app redirects to authentik and comes straight back with
`/oauth/oidc/callback?error=invalid_request&error_description=The request is otherwise malformed`,
while `.well-known/openid-configuration` for the provider returns `200` and advertises every grant type.

Cause: `grant_types` was left out of the blueprint, so the provider stores an empty list. A blueprint keeps the
Django model default (`ArrayField(default=list)`), not the web UI form default that a provider created in the
Admin UI gets. Authentik's authorize view resolves `response_type=code` to `authorization_code`, checks it
against `provider.grant_types` and raises `invalid_request` to the redirect URI when it is absent. Discovery is
global and per-instance, so it never shows the per-provider list. Fix: set `grant_types` explicitly.
