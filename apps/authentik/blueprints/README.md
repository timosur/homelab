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

## Shared ExternalSecret: create Key Vault secrets before the PR syncs

`authentik-oidc-secrets` is one ExternalSecret with one entry per app. If any Key Vault entry (for example
`paperless-oidc-client-secret`) is missing, the **whole** ExternalSecret fails to sync. That also blocks refreshing
the other keys (Open WebUI) and, on a fresh cluster, the Open WebUI key is never created. The worker's `optional: true`
does not help here, since the Secret itself is not produced or updated. So the Key Vault secret must exist before
the PR that adds the entry is synced. This pattern is intentionally unchanged.

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

## Paperless-ngx user behaviour

- Blueprint `paperless.yaml`, Key Vault secret `paperless-oidc-client-secret`, slug `paperless`, restricted to
  group `board-member`. Paperless builds its config in ExternalSecret `paperless-oidc` (namespace `paperless`):
  `PAPERLESS_APPS` and `PAPERLESS_SOCIALACCOUNT_PROVIDERS` (JSON containing the secret) with allauth provider id `authentik`.
- Callback route: `https://docs.timosur.com/accounts/oidc/authentik/login/callback/` (`/accounts/oidc/<provider_id>/login/callback/`).
  The blueprint's strict `redirect_uris` and the `provider_id` must change together. `PAPERLESS_URL` must stay `https://docs.timosur.com`.
- Local login stays enabled (break-glass) and local self-signup stays off (`PAPERLESS_ACCOUNT_ALLOW_SIGNUPS=false`).
  The first SSO login creates the account (`PAPERLESS_SOCIALACCOUNT_ALLOW_SIGNUPS`/`PAPERLESS_SOCIAL_AUTO_SIGNUP` true).
  New accounts get no superuser/staff rights and no default groups (`PAPERLESS_SOCIAL_ACCOUNT_DEFAULT_GROUPS` unset;
  group sync off); grant permissions in Paperless afterwards.
- Existing local accounts are not merged or connected by email. An SSO user whose email matches a local account
  does not get linked.
- Rollout: the Key Vault secret must exist **before** ArgoCD syncs (see "Shared ExternalSecret" above). The
  `paperless-oidc` `secretRef` in the Paperless deployment is `optional: true`, so if the secret is still missing
  Paperless starts without SSO (local login only) instead of failing. Then follow the rollout order above
  (restart worker, apply `/blueprints/oidc/paperless.yaml`), restart Paperless and check `.well-known` for slug `paperless`.
- Proxy settings required for OIDC (lessons from the live rollout, both in `apps/paperless/configmap.yaml`):
  - `PAPERLESS_ALLAUTH_TRUSTED_PROXY_COUNT: "1"` is required. Without it allauth defaults the count to the number of
    `PAPERLESS_TRUSTED_PROXIES` entries (3 CIDRs), but `X-Forwarded-For` has only one hop, so every OIDC login raises
    `ImproperlyConfigured`.
  - `PAPERLESS_USE_X_FORWARD_HOST: "true"` is needed because wol-proxy rewrites the Host header. It keeps the
    redirect URIs on `docs.timosur.com`.
- The client secret is inserted into the provider JSON with `toJson`, so `"` or `\` in the value cannot break it.

## Mealie user behaviour

Checked against the Mealie v3.30.0 source (`mealie/routes/auth/auth.py`, `mealie/core/security/providers/openid_provider.py`,
`mealie/core/settings/settings.py`) and its OIDC docs. Env var names are verified against `settings.py`; the
`.well-known` and sign-in behaviour were not tested against a live cluster.

- Blueprint `mealie.yaml`, Key Vault secret `mealie-oidc-client-secret`, slug/client_id `mealie`, restricted to group
  `board-member`. Mealie reads the secret from ExternalSecret `mealie-oidc` (key `OIDC_CLIENT_SECRET`, `secretKeyRef` is
  `optional: true`, so a missing secret means Mealie starts without SSO). Non-secret settings are in ConfigMap `mealie-config`.
- Callback: Mealie sends `redirect_uri=<BASE_URL>/login`, i.e. `https://mealie.timosur.com/login` (strict). `/login?direct=1`
  is also registered (return after RP-initiated logout; listed in the Mealie docs). `BASE_URL` must stay `https://mealie.timosur.com`.
- First SSO login creates the Mealie account (`OIDC_SIGNUP_ENABLED=true`) as a regular **non-admin** user (`admin` is only set
  from `OIDC_ADMIN_GROUP`, which is deliberately unset, as is `OIDC_USER_GROUP`; access is gated in authentik by the `board-member` binding).
- `ALLOW_SIGNUP=false` does **not** block OIDC signup: in v3.30.0 it is only checked by the public registration endpoint
  (`routes/users/registration.py`). The OIDC path only checks `OIDC_SIGNUP_ENABLED`.
- Local password login stays on and `OIDC_AUTO_REDIRECT=false` (break-glass).
- **Deviation: existing accounts ARE matched by username/email and this cannot be turned off.** On OIDC login Mealie looks
  up a user by the `OIDC_USER_CLAIM` value (default `email`) first as `username`, then as `email` (case-insensitive) and, if found,
  logs the SSO user into that existing account, including local and admin accounts, keeping its role. It never creates a duplicate.
  There is no setting to disable linking. Keep the merge-by-email warning in mind: a `board-member` whose authentik email
  (or username) equals a local Mealie account signs in as that account, including its role.
- **Email verification (how it really works).** Mealie rejects logins unless the `email_verified` claim is `true`
  (`OIDC_REQUIRES_EMAIL_VERIFICATION`, default true, kept). authentik 2026.8.6's default `email` mapping returns
  `email_verified: False`, so the blueprint uses its own scope mapping `Mealie: email (admin-managed, verified)` that always
  asserts `true`. This is safe **only because authentik users cannot edit their own email** (confirmed by the board member).
  If self-service email editing is ever enabled in authentik, this mapping becomes an account-takeover path, because Mealie links
  SSO logins to existing accounts by username or email. Do not enable it without removing or reworking this mapping.
- **Required safeguard (manual, before rollout):** the local Mealie admin must use a username and email that no authentik user
  has (for example `admin@mealie.local`), so no SSO login can link to the admin account.
- Board-member accounts need a non-empty `name` in authentik (Mealie requires the `name` claim). SSO usernames
  (`preferred_username`) must not collide with local Mealie usernames. Mealie matches existing users only on the
  `OIDC_USER_CLAIM` value (email), so a colliding `preferred_username` does **not** link the accounts: creating the new user
  fails on the unique username, and the login returns 401 with only a log entry.
- Rollout (no checksum annotation in this repo, and `envFrom` does not roll the pod on ConfigMap/Secret changes):
  1. Create the Key Vault secret `mealie-oidc-client-secret` **before** the sync (`openssl rand -hex 32`). If it is missing,
     the whole shared `authentik-oidc-secrets` ExternalSecret fails.
  2. Change the local Mealie admin's username and email (see safeguard above).
  3. Sync.
  4. Restart the authentik worker: `kubectl -n authentik rollout restart deploy/authentik-worker`.
  5. Apply the blueprint: `kubectl -n authentik exec deploy/authentik-worker -- ak apply_blueprint /blueprints/oidc/mealie.yaml`.
  6. Check `curl -s -o /dev/null -w '%{http_code}\n' https://auth.timosur.com/application/o/mealie/.well-known/openid-configuration` returns `200`.
  7. `kubectl -n mealie rollout restart deploy/mealie`. The sync may have restarted the pod before the `mealie-oidc` Secret
     existed, so this restart is still needed.
  8. Verify that a `board-member` SSO login works and lands as a non-admin, and that local login still works.

## Grafana user behaviour

Checked against the kube-prometheus-stack 92.16.0 grafana subchart templates and Grafana's generic OAuth docs; the sign-in
flow itself was not tested against a live cluster.

- Blueprint `grafana.yaml`, Key Vault secret `grafana-oidc-client-secret`, slug/client_id `grafana`, restricted to group
  `board-member`. Grafana reads the secret from ExternalSecret `grafana-oidc` (namespace `monitoring`, key `client-secret`) through
  the chart's `envValueFrom` as `GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET` (`optional: true`, so Grafana still starts if the Secret is missing;
  the "Sign in with authentik" button is then still shown but SSO sign-in fails, while the local/admin login keeps working). The secret is not in values, `grafana.ini` or a ConfigMap.
- Callback: `https://grafana.home.timosur.com/login/generic_oauth` (strict; `root_url` + `/login/generic_oauth`). `root_url`
  and the blueprint `redirect_uris` must change together. Grafana is reachable only from the home network, while authentik is public. The browser follows the redirect back to Grafana, so the login must happen on the home network.
- Local/admin login stays on (`grafana-admin-secret`): no `disable_login_form`, `auto_login` or basic-auth change.
- First SSO login creates the user (`allow_sign_up: true`) as **Viewer** (`[users] auto_assign_org_role`). No `role_attribute_path`, no
  group scope, no Grafana-admin mapping (`allow_assign_grafana_admin: false`).
- Role sync: `skip_org_role_sync: true`. Without a `role_attribute_path` Grafana would reset the org role to the default on each
  login; with this setting, roles an admin grants in the Grafana UI persist.
- No merge by email: `[auth] oauth_allow_insecure_email_lookup: false` is set explicitly. Grafana then identifies SSO users by the
  OAuth `sub` (auth_id) only; a `board-member` whose email matches a local Grafana user is not linked and sign-in fails with a
  "user already exists"-style error. (Default is false; stated in config so it is not changed by accident.)
- Claims: Grafana's generic OAuth does not check `email_verified`, so authentik's default `email` mapping is used and no custom
  mapping is needed. The default openid/email/profile mappings provide the `email` and `name` claims Grafana needs. Grafana sets the user's login to the email address, not to `preferred_username`.
- Restart: the chart puts a `checksum/config` annotation on the pod, so changing `grafana.ini` or values rolls Grafana automatically.
  A change to the `grafana-oidc` Secret (first creation, rotation) does **not** roll it (env from `secretKeyRef` is read at start),
  so run `kubectl -n monitoring rollout restart deploy/monitoring-grafana`.
- Rollout:
  1. Create Key Vault secret `grafana-oidc-client-secret` **before** the sync (`openssl rand -hex 32`); otherwise the shared
     `authentik-oidc-secrets` ExternalSecret fails as a whole.
  2. Sync.
  3. `kubectl -n authentik rollout restart deploy/authentik-worker`.
  4. `kubectl -n authentik exec deploy/authentik-worker -- ak apply_blueprint /blueprints/oidc/grafana.yaml`.
  5. `curl -s -o /dev/null -w '%{http_code}\n' https://auth.timosur.com/application/o/grafana/.well-known/openid-configuration` returns `200`.
  6. `kubectl -n monitoring rollout restart deploy/monitoring-grafana` (the sync may have started the pod before the Secret existed).
  7. Verify an SSO login as a `board-member` lands as Viewer and the admin login still works.

## ArgoCD user behaviour

Checked against the Argo CD v3.6.2 source and docs (`util/settings/settings.go`, `server/server.go`, user-management docs);
the sign-in flow was not tested against a live cluster.

- Blueprint `argocd.yaml`, Key Vault secret `argocd-oidc-client-secret`, slug/client_id `argocd`, restricted to group
  `board-member`. ArgoCD reads the secret from ExternalSecret `argocd-oidc` (namespace `argocd`, key `clientSecret`, in
  `apps/argocd-oidc/oidc-external-secret.yaml`). It is a separate Secret, **not** `argocd-secret`, and carries the label
  `app.kubernetes.io/part-of: argocd` (set via `target.template.metadata.labels`), which ArgoCD requires before it resolves
  `$argocd-oidc:clientSecret` in `oidc.config`. The secret is never in a ConfigMap or Git.
- Config (patches in `apps/argocd/kustomization.yml`): `argocd-cm` gets `url: https://argo.home.timosur.com` and `oidc.config`
  (name `authentik`, issuer `https://auth.timosur.com/application/o/argocd/`, `requestedScopes: [openid, profile, email]`);
  `argocd-rbac-cm` gets `policy.default: role:admin`. The upstream keys of both ConfigMaps are preserved (checked in the rendered output).
- Callback: `https://argo.home.timosur.com/auth/callback` (strict; `<url>/auth/callback`). `url` in `argocd-cm` and the blueprint
  `redirect_uris` must change together. ArgoCD is reachable only from the home network, while authentik is public, so the login
  must happen on the home network.
- CLI: `argocd login --sso` uses `http://localhost:8085/auth/callback` (default `--sso-port`). It is **not** registered in the blueprint,
  so CLI SSO does not work. To enable it, add a second strict redirect URI for that URL (the CLI reuses the same client). Use the local `admin` for the CLI until then.
- **Role decision (deliberate exception to the lowest-role rule of other apps): every SSO user is full admin.** `policy.default: role:admin`
  grants every authenticated user without an explicit policy full access. Access is gated only by the authentik `board-member`
  binding; there is no groups scope mapping and no per-group RBAC. Anyone who can pass that binding administers the cluster's GitOps.
- The local `admin` account is kept (`admin.enabled` is not set to false), as the break-glass login.
  `policy.default` applies to **every authenticated account**, local ones included (today that is only `admin`), so any local
  account added in future would also be admin. `users.anonymous.enabled: "false"` is pinned in `argocd-cm` so the default role
  can never apply to anonymous users.
- The `argocd-oidc` ExternalSecret lives in its own Application (`apps/_argocd/argocd-oidc-app.yaml`, path `apps/argocd-oidc`), not in
  `apps/argocd`: Ansible applies the `apps/argocd` overlay before External Secrets Operator and its CRD exist, so an
  ExternalSecret there would break a fresh bootstrap. The new app owns only that ExternalSecret (`prune: false`) and needs ESO
  and the `azure-keyvault-store` ClusterSecretStore.
- Claims: ArgoCD does not check `email_verified` and uses no groups, so authentik's default `openid`/`email`/`profile` mappings suffice
  and no custom mapping is added.
- Missing secret: if `argocd-oidc` does not exist, argocd-server still starts. The unresolved `$argocd-oidc:clientSecret` string is used
  as the literal secret (a warning is logged), so SSO login fails at the token exchange, while the local `admin` login is unaffected.
- Reload: argocd-server watches `argocd-cm` and Secrets. When the OIDC config (including the resolved secret) or `url` changes it logs
  "oidc config modified. restarting" and restarts its server loop in-process, so no pod restart should be needed. A manual restart is
  the safe fallback if the login button or SSO still fails after a sync.
- Rollout:
  1. Create Key Vault secret `argocd-oidc-client-secret` **before** the sync (`openssl rand -hex 32`); otherwise the shared
     `authentik-oidc-secrets` ExternalSecret fails as a whole.
  2. Sync `root` (it creates the new `argocd-oidc` Application), then `argocd-oidc` (must sync, it creates the `argocd-oidc` Secret), `argocd` and `authentik`.
  3. `kubectl -n authentik rollout restart deploy/authentik-worker`. Needed because the sync can start the worker before ESO has
     added the new `ARGOCD_OIDC_CLIENT_SECRET` key to `authentik-oidc-secrets`; env vars from a Secret are only read at pod start.
  4. `kubectl -n authentik exec deploy/authentik-worker -- ak apply_blueprint /blueprints/oidc/argocd.yaml`.
  5. `curl -s -o /dev/null -w '%{http_code}\n' https://auth.timosur.com/application/o/argocd/.well-known/openid-configuration` returns `200`.
  6. If needed: `kubectl -n argocd rollout restart deploy/argocd-server`.
  7. Verify that an SSO login as a `board-member` has full access and that the local `admin` login still works.

## Penpot user behaviour

Checked against the Penpot 2.19.0 backend source (`app/auth/oidc.clj`, `app/rpc/commands/auth.clj`, `app/config.clj`) and the
penpot-helm chart 1.11.9 templates (`helm template` was run against the chart source of tag `penpot-1.11.9`, since
`helm.penpot.app` was not reachable from the sandbox). The sign-in flow was not tested against a live cluster.

- Blueprint `penpot.yaml`, Key Vault secret `penpot-oidc-client-secret`, slug/client_id `penpot`, restricted to group `board-member`.
  Penpot reads the secret from ExternalSecret `penpot-oidc` (namespace `penpot`, key `clientSecret`, in `apps/penpot/oidc-external-secret.yaml`;
  separate from `penpot-secrets` so a missing entry cannot affect the API secret key).
- Config is set with `backend.extraEnvs` (`PENPOT_OIDC_BASE_URI`, `_CLIENT_ID`, `_SCOPES`, `_CLIENT_SECRET`), not `config.providers.oidc`: the chart's
  template renders the client secret `secretKeyRef` without `optional`, so a missing secret would leave the backend in `CreateContainerConfigError`.
  With `optional: true` Penpot still starts, and password login keeps working. The `enable-login-with-oidc` flag is set in `config.flags`
  (it reaches backend and frontend). Endpoints come from discovery at `PENPOT_OIDC_BASE_URI`.
- Callback: `https://penpot.home.timosur.com/api/auth/oidc/callback` (strict; `PENPOT_PUBLIC_URI` + `/api/auth/oidc/callback`). `config.publicUri` and the
  blueprint `redirect_uris` must change together. Penpot is reachable only from the home network, while authentik is public, so the login must happen on the home network.
- Password login stays on (`enable-login-with-password`). It is break-glass **and** a hard requirement of the SSO first-login flow:
  in 2.19.0 `register-profile` runs `validate-registration-flags!`, which refuses with `registration-disabled` unless `login-with-password`
  is set. Do not remove this flag, even once everyone uses SSO.
- **`enable-registration` is required for first SSO login.** An unknown email is sent to the register step, and `validate-registration-flags!`
  there needs both `enable-registration` and `enable-login-with-password`. (`enable-oidc-registration` only gets the callback past the first check
  and does not replace `enable-registration`.) So local signup stays open on the LAN (accepted). The first SSO login lands on a "create account"
  confirmation screen with the name prefilled; the new account is a normal user (Penpot has no global roles). `disable-email-verification` makes it active at once.
- **No merge by email.** An existing profile with the same email is only logged in by SSO if it was created through OIDC, or if the `email_verified` claim is `true`
  (`callback-handler`: `auth-provider-not-allowed` otherwise). authentik 2026.8.6's default `email` mapping returns `email_verified: false`, so the
  blueprint uses the default `openid`/`email`/`profile` mappings and **no custom `email_verified: true` mapping**. A `board-member` whose email matches a local password
  account is refused (`auth-provider-not-allowed` error on the login page). Do not add a mapping that asserts `email_verified: true`: that would enable account linking by email.
  Penpot does not require `email_verified` for new accounts.
- Claims: Penpot needs `email` and `name` (falls back to `nickname`, then to the email); both come from the default mappings. No groups or roles are used (`PENPOT_OIDC_ROLES` unset).
- **SSRF guard:** Penpot validates the OIDC discovery, token, userinfo and JWKS requests against its SSRF blocklist (private, loopback, link-local,
  CGNAT ranges) after resolving the host. `PENPOT_SSRF_ALLOWED_HOSTS=auth.timosur.com` (set in `backend.extraEnvs`; exact, case-insensitive host match) bypasses it
  for authentik. Reason: whether `auth.timosur.com` resolves to a LAN or cluster address inside the cluster could not be ruled out from Git.
  Pi-hole's local DNS records live on its PVCs (`pi-hole-dnsmasq`), not in the repo, and no CoreDNS override or `hostAliases` for it is in Git
  (the repo has none for `auth.timosur.com`). If discovery fails with `ssrf-blocked-target`, this setting is the first thing to check.
- Restart: Penpot discovers the provider **once, when the backend starts** (`.well-known` fetch in `providers/generic`); if it fails it only logs a warning and the
  SSO button does not work until the next start. The client secret is an env var from a Secret. Neither change rolls the pod, so restart the backend
  after the authentik provider exists: `kubectl -n penpot rollout restart deploy/penpot-backend`.
- Rollout:
  1. Create Key Vault secret `penpot-oidc-client-secret` **before** the sync (`openssl rand -hex 32`); otherwise the shared
     `authentik-oidc-secrets` ExternalSecret fails as a whole (and `penpot-oidc` does not sync).
  2. Sync (`authentik` and `penpot`).
  3. `kubectl -n authentik rollout restart deploy/authentik-worker`.
  4. `kubectl -n authentik exec deploy/authentik-worker -- ak apply_blueprint /blueprints/oidc/penpot.yaml`.
  5. `curl -s -o /dev/null -w '%{http_code}\n' https://auth.timosur.com/application/o/penpot/.well-known/openid-configuration` returns `200`.
  6. `kubectl -n penpot rollout restart deploy/penpot-backend`.
  7. `kubectl -n penpot logs deploy/penpot-backend | grep -i oidc` must show the "provider initialized" line. If it is missing (a
     "unable to initialize auth provider" warning instead), check first that `PENPOT_SSRF_ALLOWED_HOSTS=auth.timosur.com` is on the backend pod, then that the
     `.well-known` URL is reachable from the pod and that `penpot-oidc` exists (secret set).
  8. Verify that an SSO login as a `board-member` creates a normal account and that password login still works.

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
