# OIDC app blueprints

New OAuth2/OIDC providers are declared as authentik blueprints in this directory
(not via `scripts/setup_authentik_homelab.py --provision-oidc`, which only covers Coder and Dark Factory).
Reference: `open-webui.yaml`. Blueprint schema: https://docs.goauthentik.io/customize/blueprints/v1/structure/
(`!Env`, `!File`, `!Find`, `!KeyOf` tags).

## Add the next app

1. **Create the secret in Key Vault** (human step, never in Git):
   ```
   az keyvault secret set --vault-name homelab-timosur \
     --name <app>-oidc-client-secret --value "$(openssl rand -base64 48 | tr -d '\n')"
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
