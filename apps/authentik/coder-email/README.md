# Coder email verification

The blueprint is staged with automatic instantiation disabled. After review and deployment,
enable it separately, then test the full confirmation flow. It creates a separate, authenticated
Authentik flow and a Coder-only email scope without changing the default authentication flow.
Coder continues to reject unverified email addresses. When enabled, the blueprint binds the
`board-member` group to the Coder application.

The Authentik worker waits for the existing Key Vault `smtp-password` reference before starting.
After deploying, check that the ExternalSecret is ready and the worker is healthy, then restart
the Authentik server to load the updated mail configuration and credential. Only then enable the
blueprint and check that `Coder email verification` is successful. Do not place SMTP credentials
in this repository.

To verify an address, sign in to Authentik, then open
`https://auth.timosur.com/if/flow/coder-email-verification/` in the same browser. Follow the
confirmation link sent to the account's current email address. Afterwards, retry Coder's OIDC login.
If the email address differs from the last confirmed address, repeat verification before signing
in to Coder. If it changes back to that last confirmed address, the earlier confirmation remains
valid; this accepts the risk that the mailbox might no longer be under the user's control.

The confirmation token retains the destination address in its original flow plan. The record
policy checks that address against the user's current database record, so an address changed
while a confirmation link is outstanding is not marked verified.
