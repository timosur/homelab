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

Safety: the worker's `secretKeyRef` is `optional: true` (the worker uses `Recreate`, so a missing
Key Vault entry must not block it), and every blueprint entry has `conditions: [!Env [VAR, ""]]`,
so while the variable is unset or empty the entries are skipped and no provider with an empty
secret is created. Also create the secret *before* the app's own sync: if the entry is missing,
the app ExternalSecret errors and the app's new `oidc-client-secret` key is absent.

## Verify the apply

- Worker logs: `kubectl -n authentik logs deploy/authentik-worker | grep -i blueprint`
- Admin UI: Customization > Blueprints, instance "Open WebUI OIDC" should be `successful`.
- Admin UI: Applications should list `open-webui` with its provider.

## Open WebUI user behaviour

- Existing local accounts are **not** merged with SSO users by email (`OAUTH_MERGE_ACCOUNTS_BY_EMAIL`
  is intentionally unset; the owner may decide later). An SSO user whose email matches a local account
  fails to sign in.
- New SSO users are created as `pending` until an admin approves them. `DEFAULT_USER_ROLE` is left
  unchanged because local signup on the public URL inherits it.
- Local login stays enabled (break-glass admin).
- Open WebUI stores OAuth settings and `WEBUI_URL` as PersistentConfig in its database. After first
  start, values saved in the admin UI win over the env vars, and later env changes are ignored for
  those settings. `ENABLE_PERSISTENT_CONFIG=false` would make env vars win, but it applies to **all**
  settings (web search, etc.), so it is not set here, and I found no OAuth-only variant. To change
  OIDC settings, edit them in the admin UI (or reset the stored value).

## Add the next app

1. **Create the secret in Key Vault** (human step, never in Git):
   ```
   az keyvault secret set --vault-name homelab-timosur \
     --name <app>-oidc-client-secret --value "$(openssl rand -hex 32)"
   ```
2. Copy `open-webui.yaml` to `<app>.yaml`; change the provider name, `client_id`, `redirect_uris`
   (strict), application slug/name/launch URL and the `!Env` variable name.
3. Add the file to the `authentik-oidc-blueprints` generator in `../kustomization.yaml`.
4. Add the key to `../oidc-external-secret.yaml` and as an env var (`secretKeyRef` on
   `authentik-oidc-secrets`) in `../worker-deployment.yaml`. Only the worker loads blueprints.
5. In the app namespace, add an ExternalSecret entry reading the **same** Key Vault name and
   configure the app with issuer `https://auth.timosur.com/application/o/<slug>/`.
6. The worker pod must restart to pick up a new env var; blueprint file changes are re-applied automatically.

Blueprints are mounted at `/blueprints/oidc/`; the worker discovers `/blueprints` recursively.
