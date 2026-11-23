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
- Open WebUI keeps some settings as PersistentConfig: once an admin has saved such a setting in the
  admin UI (for example `WEBUI_URL`), the stored value overrides the env var and later env changes are
  ignored for it. The OIDC client settings (`OAUTH_CLIENT_ID`, `OAUTH_CLIENT_SECRET`,
  `OPENID_PROVIDER_URL`, ...) are not editable in the admin UI, so they are not affected that way;
  verify after changing them. `ENABLE_PERSISTENT_CONFIG=false` would make env vars always win, but it
  applies to **all** settings (web search, etc.), so it is not set here, and I found no OAuth-only variant.

## Add the next app

1. **Create the secret in Key Vault** (human step, never in Git):
   ```
   az keyvault secret set --vault-name homelab-timosur \
     --name <app>-oidc-client-secret --value "$(openssl rand -hex 32)"
   ```
2. Copy `open-webui.yaml` to `<app>.yaml`; change the provider name, `client_id`, `redirect_uris`
   (strict), application slug/name/launch URL and the `!Env` variable name. Rename that variable in
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
