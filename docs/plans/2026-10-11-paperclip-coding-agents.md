# Paperclip Coding-Agent Platform Implementation Plan

**Goal:** Run Paperclip coding and review agents for the `homelab` GitHub repository. Agents create/update pull requests and respond to review comments; humans remain the only merge authority.

**Architecture:** Paperclip runs in the homelab cluster with central CNPG and a home-only UI. Its native Kubernetes sandbox provider creates ephemeral Agent Sandbox workloads. Workloads run as Kata microVMs only on `homelab-amd` and `homelab-amd-desktop`, call models through the internal `agentgateway`, and use Cilium allow-list egress. GitHub reaches a separate, public, signed webhook route; the Paperclip UI is never public.

**Decisions:**

- Use Paperclip's native Kubernetes sandbox provider; do **not** install `paperclip-operator`.
- Use `sandbox-cr`, not the Job backend, because OpenCode requires multi-command execution.
- Use Kata and Cilium. Do not schedule agents on `homelab-gpu`, `homelab-arm-large`, or `homelab-arm-small`.
- Start with at most two concurrent agents, one per eligible node.
- Use two allow-listed GitHub Apps installed only on `homelab`: a Paperclip trigger/review App with the native connector's least privileges, and a coding App with repository-content write access. Humans merge all pull requests.
- Accept coding requests only from GitOps-allow-listed GitHub humans using an explicit `@paperclip-bot` mention. A small public trigger guard enforces this before forwarding to Paperclip's native GitHub webhook.
- A coding-App-authored PR automatically receives a review on `opened`, `synchronize`, `reopened`, or `ready_for_review`; human comments never automatically start coding work.
- Keep Paperclip logs/artifacts for 30 days on a best-effort basis. Sandboxes, workspaces, and run secrets are ephemeral.
- Use GitOps bootstrap for Paperclip plugins, environments, and policies; the GitHub App connection is a documented one-time manual prerequisite until Paperclip supports a stable headless self-hosted connection API.
- Use Paperclip local authentication, with bootstrap/admin secret material from ExternalSecrets.
- Run the pilot only after a smoke test proves AgentGateway returns OpenCode-compatible cost data: enforce a 30-minute and $5 per-task cap, with no automatic retry.
- GitOps pre-creates one fixed tenant namespace and its base RBAC/quota/policies. Paperclip may create/delete only its per-run Cilium policies in that namespace for human-approved extra egress.

---

### Task 1: Validate version and API compatibility

**Purpose:** The Paperclip Kubernetes provider and Agent Sandbox APIs evolve independently. Pin mutually compatible versions before adding cluster resources.

- [ ] Pin a Paperclip server image and `@paperclipai/plugin-kubernetes` version.
- [ ] **Block implementation until Paperclip PR [#13734](https://github.com/paperclipai/paperclip/pull/13734) is merged and released.** It discovers the served Agent Sandbox API version and supports current `agents.x-k8s.io/v1beta1`; do not pin a deprecated `v1alpha1` controller merely to work around the provider.
- [ ] Pin the Paperclip release containing #13734 and a non-`latest` Agent Sandbox v1.x release. Record the exact image digests and verify the generated Sandbox uses `agents.x-k8s.io/v1beta1`.
- [ ] Verify the provider supports `runtimeClassName`, `egressMode: cilium`, in-cluster authentication, and `opencode_local` at the selected versions.
- [ ] Verify the generated `Sandbox.spec.podTemplate` can be admission-mutated before its pod is created.
- [ ] Prove the native GitHub chat endpoint accepts the installed App webhook through the trigger guard, creates a task from an explicit mention, and automatically schedules the configured review agent for the selected PR events.
- [ ] Prove AgentGateway's OpenCode response reports per-part cost that Paperclip can use to enforce the $5 cap; implementation is blocked until this passes.
- [ ] Record all pinned versions in the Paperclip manifests and this plan's implementation PR.

**Acceptance:** A disposable Paperclip environment can create and delete one sandbox CR without an API-version or RBAC error.

---

### Task 2: Install Kata on the two sandbox nodes

**Files:**
- Create an Ansible playbook/role for the `homelab-amd` and `homelab-amd-desktop` inventory hosts.
- Create a GitOps `RuntimeClass` manifest, for example `kata-paperclip`.

- [ ] Install one pinned Kata release on `homelab-amd` and `homelab-amd-desktop` only; verify checksums.
- [ ] Extend K3s' containerd template with the Kata runtime handler. Preserve K3s' generated base template rather than editing rendered `config.toml`.
- [ ] Restart K3s serially, one node at a time, and confirm the runtime handler is available.
- [ ] Create `RuntimeClass kata-paperclip` with the Kata handler and scheduling restrictions for `workload-type=paperclip-sandbox`.
- [ ] Label the two nodes `workload-type=paperclip-sandbox`. Do not add a sandbox-only taint to `homelab-amd`: it remains the control plane and hosts existing workloads. Include the existing `availability=daytime:NoSchedule` toleration in the RuntimeClass scheduling rules so sandboxes can use `homelab-amd-desktop`.
- [ ] Run an unprivileged test pod using `runtimeClassName: kata-paperclip` on each node.

**Acceptance:** Both nodes run the test pod with Kata; no normal workload moves to the dedicated nodes unintentionally.

---

### Task 3: Install Agent Sandbox through ArgoCD

**Files:**
- Create `apps/agent-sandbox/` and `apps/_argocd/agent-sandbox-app.yaml`.

- [ ] Add the pinned Agent Sandbox controller and CRDs as a dedicated ArgoCD application.
- [ ] Install only required extensions; do not deploy warm pools or persistent workspace facilities for the pilot.
- [ ] Confirm controller health and that `Sandbox` resources cascade-delete their pods and secrets.

**Acceptance:** A hand-created Kata-backed sandbox starts, accepts exec, and is fully removed on CR deletion.

---

### Task 4: Add node placement and wake control without forking Paperclip

**Files:**
- Create `apps/paperclip-sandbox-controller/` and its ArgoCD application.
- Update `apps/wol-proxy/` with an internal wake endpoint and its policy.

**Design:** A small cluster-local controller implements the missing Kubernetes behavior rather than changing Paperclip's provider:

1. Its mutating admission webhook recognizes Paperclip sandbox CRs and injects a dedicated sandbox label, required node affinity for the two labeled nodes, and required hostname pod anti-affinity for that label.
2. Its controller watches the generated pods. If a second sandbox is pending because `homelab-amd-desktop` is asleep, it calls `wol-proxy` and waits for the desktop node to become Kubernetes `Ready`.
3. The normal scheduler then schedules the existing pending pod. The controller never schedules pods directly.

- [ ] Add the webhook TLS/certificate plumbing and a narrowly scoped `MutatingWebhookConfiguration`.
- [ ] Add the wake controller with RBAC limited to read/watch sandbox CRs, pods, and nodes, plus delete only for labeled pending Paperclip Sandbox CRs.
- [ ] Add a dedicated authenticated internal `wol-proxy` endpoint for waking `homelab-amd-desktop`; it must not be exposed through an HTTPRoute.
- [ ] Allow only the wake controller to reach that endpoint with Cilium policy. Sandbox pods get neither the endpoint nor node-management credentials.
- [ ] Make wake requests idempotent and wait at most 180 seconds for Kubernetes `Ready`.
- [ ] Set Paperclip lease acquisition above that wake window. Before relying on deletion as failure signalling, prove the pinned provider converts a deleted pending Sandbox into a terminal acquisition failure. If it does not, expose a provider-supported terminal failure instead; do not assume deletion wakes the lease waiter. Do not auto-retry or delete Sandboxes pending for image-pull, resource, affinity, or other non-sleep causes.

**Acceptance:** With desktop asleep, a second task wakes it and runs on it. With both nodes ready, two sandboxes occupy different hosts. A third sandbox remains Pending because both eligible hosts already carry the required anti-affinity label.

---

### Task 5: Deploy Paperclip server statefully

**Files:**
- Create `apps/paperclip/namespace.yaml`, `appdb.yaml`, `external-secret.yaml`, `configmap.yaml`, `deployment.yaml`, `service.yaml`, `pvc.yaml`, retention `CronJob`, `network-policy.yaml`, and `kustomization.yaml`.
- Create `apps/_argocd/paperclip-app.yaml`.
- Create a home HTTPRoute under `networking/httproutes/home/`.

- [ ] Create the `paperclip` namespace with restricted pod-security labels.
- [ ] Create an `AppDBClaim` on central CNPG; use its generated connection secret rather than a per-app PostgreSQL cluster.
- [ ] Create minimal persistent storage only for Paperclip server state, plugin storage, and retained artifacts/logs.
- [ ] Deploy one Paperclip replica with resource requests/limits, probes, non-root security context, and a dedicated service account.
- [ ] Expose the UI only at a home HTTPS route. Disable public signup and bootstrap the initial local administrator through the supported Paperclip flow using ExternalSecrets-backed bootstrap/admin material.
- [ ] Retain the local Paperclip log/artifact volume for 30 days with a daily best-effort pruning Job; confirm central CNPG backups cover its database. Do not claim Paperclip has a built-in retention setting unless the selected release proves it.

**Acceptance:** The home UI is reachable and authenticated, while its service has no Internet HTTPRoute.

---

### Task 6: Configure provider RBAC, isolation, and agentgateway access

**Files:**
- Extend `apps/paperclip/` with provider RBAC and Cilium policies.

- [ ] Pre-create one provider-derived tenant namespace (for example, `paperclip-homelab`) and the stock provider's expected service account, role, role binding, quota, limit range, and base policies through GitOps. Paperclip gets no permission to create namespaces, quotas, limit ranges, or baseline policies.
- [ ] Grant Paperclip's server service account only fixed-tenant permissions to create/read/delete Sandbox CRs, pods/logs, per-run secrets, and its labeled per-run `CiliumNetworkPolicy` resources. Audit every cluster-scoped permission; none should be required for tenant provisioning.
- [ ] Use that one tenant namespace for the pilot so its two-sandbox `ResourceQuota` is effective. Do not represent the namespace-scoped quota as a cluster-wide concurrency control if more tenants are added later.
- [ ] Configure the Kubernetes provider with `inCluster: true`, `namespacePrefix: paperclip-`, `companySlug: homelab`, `backend: sandbox-cr`, `runtimeClassName: kata-paperclip`, `egressMode: cilium`, and `adapterType: opencode_local` so its derived namespace is the GitOps-created tenant.
- [ ] Configure namespace quota for two sandboxes and per-sandbox CPU, memory, ephemeral-storage, and wall-clock limits.
- [ ] Default-deny sandbox ingress/egress. Baseline policy permits only DNS, Paperclip callback traffic, `github.com`, `api.github.com`, and internal `inference.ai.home.timosur.com`; do not allow package registries in the pilot runtime path.
- [ ] Create a dedicated `paperclip-agentgateway-api-key` in Azure Key Vault. Mirror it with ExternalSecrets into the Paperclip namespace as `AGENTGATEWAY_API_KEY` and into `agentgateway-system` as a labeled API-key Secret. Change the strict AgentGateway policy from a single `secretRef` to a `secretSelector` that admits the existing clients and the Paperclip key independently, so Paperclip access can be revoked without rotating other clients.
- [ ] Mount `AGENTGATEWAY_API_KEY` into the Paperclip server from its ExternalSecret and list it as the sole `opencode_local` adapter `envKey`. The Kubernetes provider then copies only that configured value into its Sandbox-owned per-run Secret, which is deleted with the Sandbox. OpenCode's OpenAI-compatible provider sends it as `Authorization: Bearer …` to AgentGateway; AgentGateway validates it before applying its separate upstream-provider credential. Do not inject upstream model-provider credentials into sandbox pods.
- [ ] Configure the provider's authoritative `adapters` registry for `opencode_local`: retain its runtime image, set `envKeys` to only `AGENTGATEWAY_API_KEY`, set `allowFqdns` to only `inference.ai.home.timosur.com`, and use `defaultEnv.PAPERCLIP_OPENCODE_PROVIDERS` to define an `@ai-sdk/openai-compatible` AgentGateway provider at `https://inference.ai.home.timosur.com/v1` with `apiKey: {env:AGENTGATEWAY_API_KEY}`. Set both the agent model and `PAPERCLIP_OPENCODE_SMALL_MODEL` to an AgentGateway-served `agentgateway/<model>` ID. Do not add AgentGateway alongside Paperclip's default upstream-provider credentials or FQDN grants.
- [ ] Use per-task Cilium FQDN grants only for exceptional destinations. When baseline egress blocks a task, the agent reports the exact attempted hostname and reason; it cannot self-approve or retry. A human reviews a GitOps approval record containing the Paperclip task ID, exact FQDNs, reason, and expiry, then manually sets that task's `executionWorkspaceSettings.networkEgress.allowFqdns` in Paperclip and reruns it. Do not approve wildcards or CIDRs for the pilot. This deliberately gives Paperclip namespace-scoped create/delete access to its own per-run policies; it does not give it namespace or baseline-policy authority.

**Acceptance:** An agent can use OpenCode and agentgateway, clone the permitted repository, and cannot reach arbitrary LAN services or unapproved Internet destinations.

---

### Task 7: Create the GitHub Apps, trigger guard, and public webhook route

**Files:**
- Create `apps/paperclip-trigger-guard/` and its ArgoCD application.
- Create a narrowly scoped Internet HTTPRoute/policy for the trigger guard's GitHub webhook path only.
- Add GitHub App credentials and webhook secret through ExternalSecrets.

- [ ] Create two dedicated GitHub Apps and install each only on the `homelab` repository. The Paperclip trigger/review App uses `Metadata: read`, `Contents: read`, `Issues: write`, `Pull requests: write`, and `Checks: write`; do not give it `Contents: write`, which the native connector rejects. The coding App uses only `Metadata: read`, `Contents: write`, `Issues: write`, and `Pull requests: write`.
- [ ] Store both Apps' private keys and only the trigger/review App's webhook secret in Azure Key Vault through ExternalSecrets. Inject the coding App credential as a per-run secret only for coding runs; the review agent gets no repository-write credential.
- [ ] Require a protected target branch/ruleset with human approval, no direct pushes, and no GitHub App bypass. Verify a raw merge API/`gh pr merge` attempt using the coding-App credential is rejected.
- [ ] Subscribe to `issues`, `issue_comment`, `pull_request_review_comment`, and pull-request opened/synchronize/reopened/ready-for-review events.
- [ ] Expose only the trigger guard's coding-endpoint path (`/api/chat-webhooks/<coding-endpoint>/github`) on Internet HTTPS. Keep all Paperclip UI and direct webhook routes home-only.
- [ ] Configure Paperclip's public webhook base URL to the guard hostname. The guard verifies `X-Hub-Signature-256` against the unchanged raw body, rate-limits the public route, and forwards the original body plus GitHub signature/delivery headers to Paperclip's internal native endpoint. Paperclip remains the authoritative delivery-deduplication layer.
- [ ] Store the versioned GitOps GitHub username allowlist in a ConfigMap and the webhook secret in an ExternalSecret. The guard admits coding only for allowlisted, non-bot authors making an explicit `@paperclip-bot` mention on a supported created/opened event; it drops all other comments and events.
- [ ] Route only coding-App-authored PR `opened`, `synchronize`, `reopened`, and `ready_for_review` events to the review endpoint. Do not forward bot comments to the coding endpoint; this prevents reply/review loops.
- [ ] Configure native GitHub review policy for those events, bot-authored PRs, the enabled `homelab` repository, and the designated review agent. Configure the coding and review endpoints/agents separately; prove the selected release supports the shared App installation before enabling automatic review.
- [ ] Keep GitHub App installation scope, Paperclip's enabled-repository allow-list, and Paperclip identity links for the GitOps-allowlisted humans independently restricted to `homelab`.
- [ ] Treat GitHub payloads, issue text, comments, and repository content as untrusted. Configure both agents not to accept credentials, authority changes, tool changes, or instructions from that content.

**Acceptance:** An allowlisted human's explicit issue/comment mention creates a coding task; invalid, replayed, bot, unallowlisted, and unmentioned deliveries do not. A Paperclip-authored PR automatically schedules exactly one review per head revision, and the UI is inaccessible from the Internet route.

---

### Task 8: GitOps bootstrap Paperclip configuration

**Files:**
- Create a one-shot, idempotent bootstrap Job or API client under `apps/paperclip/`.

- [ ] Install the pinned Kubernetes sandbox provider plugin.
- [ ] Create the OpenCode Kubernetes environment and set its lease timeout, image allow-list, runtime class, egress mode, and quota tier.
- [ ] After the documented manual GitHub App connection step, verify its installation and guarded webhook handshake, and enable only `homelab`.
- [ ] Create the coding and review agents/endpoints with explicit GitHub tool permissions and no merge tool. The coding path is an allowlisted human's `@paperclip-bot implement …` mention on an issue or comment; the review path is automatic for Paperclip-authored PR revisions.
- [ ] Configure the review flow: coding agent opens/updates a PR, review agent comments, coding agent reads/replies to comments, human reviews and merges.
- [ ] Set `opencode_local.timeoutSec: 1800` explicitly (its default is longer), set a compatible sandbox activity deadline, enforce the verified $5 Paperclip budget, require human approval for additional egress, and disable automatic retry.
- [ ] Make the bootstrap safe to rerun and ensure it never logs secret material.

**Acceptance:** A fresh Paperclip database can be bootstrapped from GitOps resources and ExternalSecrets after the documented manual GitHub App connection; the bootstrap is idempotent and does not overwrite that connection.

---

### Task 9: Monitoring, failure handling, and pilot

**Files:**
- Extend `apps/monitoring/` with Paperclip/sandbox alert rules and dashboard panels as needed.

- [ ] Alert on Paperclip unavailable, sandbox acquisition timeout, desktop wake timeout, Sandbox CR/pod cleanup failure, Kata test failure, quota exhaustion, and OOM kills.
- [ ] Monitor desktop readiness separately so a sleeping desktop is distinguishable from a failed wake.
- [ ] Run one coding task against `homelab` and verify it opens a branch and pull request.
- [ ] Confirm the GitOps allowlist, explicit mention requirement, bot-loop suppression, GitHub-delivery deduplication, and prompt-injection handling at the trigger guard/native endpoint boundary.
- [ ] Verify the PR's automatic review runs once per head revision; add review comments, then verify the coding agent retrieves/replies to them and updates the pull request.
- [ ] Confirm a human must merge and that Paperclip cannot merge the pull request.
- [ ] Test blocked egress, approved task-scoped egress, invalid webhook signatures, desktop wake timeout, and cleanup after a failed run.

**Acceptance:** The pilot completes end-to-end without leaked sandboxes, unscoped credentials, unintended public UI exposure, or agent merge capability.

---

## Manual prerequisites

- [ ] Make `homelab-amd-desktop` reachable for its Kata installation and verification.
- [ ] Confirm both sandbox nodes have stable power/networking and virtualization enabled.
- [ ] Create both GitHub Apps, restrict each installation to `homelab`, and prepare their private keys/webhook secrets for Azure Key Vault.
- [ ] Add the versioned GitOps GitHub-user allowlist and link those users to active, non-viewer Paperclip identities for the native endpoint's second authorization check.
- [ ] Manually connect the GitHub App in Paperclip using its supported self-hosted setup flow; create the coding/review endpoints, record their internal paths, and configure GitHub's one webhook URL to the public trigger-guard coding path.
- [ ] Create the protected target-branch ruleset requiring human approval and denying GitHub App bypass/direct pushes.
- [ ] Decide and provision the Paperclip home hostname and the separate public GitHub webhook hostname/path.
- [ ] Prove AgentGateway cost reporting and Paperclip's $5 budget enforcement before enabling pilot agents.

## Explicitly deferred

- `paperclip-operator`
- More than two concurrent agents
- Persistent agent workspaces or warm pools
- Agent merge authority
- `homelab-gpu` and ARM nodes as sandbox capacity
