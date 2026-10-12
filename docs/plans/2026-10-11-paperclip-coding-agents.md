# Paperclip Coding-Agent Platform Implementation Plan

**Goal:** Run Paperclip coding, review, and platform-engineering agents across the selected GitHub repositories. Paperclip issues start work through its UI/API; each repository has its own project workspace and produces its own pull request. Humans remain the only merge authority.

**Architecture:** One Paperclip Company, `timosur`, runs in the homelab cluster with central CNPG and a home-only UI. Its native Kubernetes sandbox provider creates ephemeral Agent Sandbox workloads. Workloads run as Kata microVMs only on `homelab-amd` and `homelab-amd-desktop`, call models through the internal `agentgateway`, and use Cilium allow-list egress. Paperclip's managed GitHub connection is enrolled through the Paperclip Cloud Connector; the Paperclip server makes the required outbound Cloud connection and brokers task-scoped GitHub credentials to sandboxes. Neither the UI nor a GitHub webhook is public.

**Decisions:**

- Use Paperclip's native Kubernetes sandbox provider; do **not** install `paperclip-operator`.
- Use `sandbox-cr`, not the Job backend, because OpenCode requires multi-command execution.
- Use Kata and Cilium. Do not schedule agents on `homelab-gpu`, `homelab-arm-large`, or `homelab-arm-small`.
- Start with at most two concurrent agents, one per eligible node, enforced through required hostname pod anti-affinity.
- Create one Company named `timosur`. Create one Paperclip project per Git repository, each with an isolated workspace and its own upstream URL/base branch; use goals and linked issues for product work that spans repositories.
- Use three shared company agents: `coder`, `reviewer`, and `platform-engineer`. The coder delegates a review task after opening a PR. The platform engineer is assigned only to the `homelab` project and makes deployment-manifest changes only after the source change is merged and its immutable image digest is published.
- Use the existing Paperclip-managed GitHub App connection, installed on every repository explicitly approved by the owner. This is an intentional shared-trust boundary: every GitHub-enabled agent run can act with the App's selected-repository authority. Humans merge all pull requests.
- Start work only through the Paperclip UI/API. Do not receive GitHub mentions, issue events, review events, or webhooks in the pilot.
- Keep Paperclip logs/artifacts for 30 days on a best-effort basis. Sandboxes, workspaces, and run secrets are ephemeral.
- Store each Paperclip environment payload as versioned JSON and reconcile it with `paperclipai environment create/update`. The Cloud Connector enrollment and managed GitHub connection/grants are documented manual prerequisites until their self-hosted APIs are stable.
- Use Paperclip local authentication, with bootstrap/admin secret material from ExternalSecrets.
- Run the pilot only after a smoke test proves AgentGateway returns OpenCode-compatible cost data: enforce a 30-minute and $5 per-task cap, with no automatic retry.
- GitOps pre-creates one fixed tenant namespace and its base RBAC/quota/policies. Paperclip may create/delete only its per-run Cilium policies in that namespace for human-approved extra egress.
- The Kubernetes provider has no `nodeSelector`, affinity, toleration, or pod-template configuration. `runtimeClassName: kata-paperclip` supplies the two-node selector and desktop toleration. A narrowly scoped mutating webhook supplies only the required hostname anti-affinity so the second sandbox must wait for the desktop node.
- Build and digest-pin the OpenCode runtime image in this repository. The provider's built-in `ghcr.io/paperclipai/agent-runtime-opencode:v1` reference currently does not exist.

---

## Validated provider constraints (2026-10-12)

- The current Kubernetes plugin schema accepts `runtimeClassName`, but not `nodeSelector`, `affinity`, `tolerations`, or a generic `podTemplate`. Its Sandbox builder emits only its fixed pod template plus `runtimeClassName` and `imagePullSecrets`. This proves that node selectors are unsupported **in Paperclip's environment JSON**.
- Kubernetes RuntimeClass scheduling supplies `nodeSelector` and `tolerations` to every Pod using that class. `kata-paperclip` therefore confines every provider-created sandbox to the two labelled AMD nodes. RuntimeClass cannot express the required one-sandbox-per-host anti-affinity. Mutate the `Sandbox` CR's `spec.podTemplate.spec.affinity` before the Agent Sandbox controller creates its Pod; then the second sandbox stays Pending while the desktop is asleep and triggers wake-on-LAN.
- The upstream provider defaults `opencode_local` to `ghcr.io/paperclipai/agent-runtime-opencode:v1`. An anonymous OCI manifest request for that tag returned HTTP 404 on 2026-10-12. Upstream's image workflow publishes `git-<sha>` on relevant `master` changes (or an explicit dispatch tag), not `v1`; do not rely on an undocumented upstream tag.
- `imageAllowList` does not select the adapter runtime image: it permits an agent-supplied `target.imageOverride`. Keep it empty/omitted. Set the custom image in the authoritative `adapters[].runtimeImage` entry instead.
- Provider bootstrap is create-if-absent, not reconciliation. It still reads the cluster-scoped tenant Namespace and reads the fixed tenant resources. GitOps must create them before the first run; Paperclip needs cluster-scoped `get namespaces`, but no cluster-scoped create/update/delete permission.
- `adapters[].envKeys` copies named Paperclip-server process variables into every run for that environment. Do not put a GitHub App key, installation token, `GH_TOKEN`, or `GITHUB_TOKEN` there. The managed GitHub runtime broker resolves task-scoped credentials for `git`/`gh`; grant the connection to the three company agents through Paperclip.

References: [plugin schema](https://github.com/paperclipai/paperclip/blob/master/packages/plugins/sandbox-providers/kubernetes/src/types.ts), [Sandbox builder](https://github.com/paperclipai/paperclip/blob/master/packages/plugins/sandbox-providers/kubernetes/src/sandbox-cr-builder.ts), [adapter defaults](https://github.com/paperclipai/paperclip/blob/master/packages/plugins/sandbox-providers/kubernetes/src/adapter-defaults.ts), [image workflow](https://github.com/paperclipai/paperclip/blob/master/.github/workflows/agent-runtime-images.yml), and [Kubernetes RuntimeClass scheduling](https://kubernetes.io/docs/concepts/containers/runtime-class/#scheduling).

### Environment payload shape

`apps/paperclip/environment.json` is the source of truth. Replace placeholders only in the GitOps change that pins a built image and chosen AgentGateway model; it contains no secret value.

```json
{
  "name": "timosur-opencode",
  "driver": "sandbox",
  "config": {
    "provider": "kubernetes",
    "inCluster": true,
    "namespacePrefix": "paperclip-",
    "companySlug": "timosur",
    "backend": "sandbox-cr",
    "runtimeClassName": "kata-paperclip",
    "egressMode": "cilium",
    "egressAllowFqdns": ["github.com", "api.github.com"],
    "adapterType": "opencode_local",
    "podActivityDeadlineSec": 1800,
    "defaultResources": {
      "requests": { "cpu": "250m", "memory": "512Mi" },
      "limits": { "cpu": "2", "memory": "4Gi" }
    },
    "adapters": [
      {
        "adapterType": "opencode_local",
        "runtimeImage": "ghcr.io/timosur/homelab/paperclip/agent-runtime-opencode:<source-sha>@sha256:<digest>",
        "envKeys": ["AGENTGATEWAY_API_KEY"],
        "allowFqdns": ["inference.ai.home.timosur.com"],
        "probeCommand": ["opencode", "--version"],
        "defaultEnv": {
          "PAPERCLIP_OPENCODE_SMALL_MODEL": "agentgateway/<model>",
          "PAPERCLIP_OPENCODE_PROVIDERS": "{\"agentgateway\":{\"npm\":\"@ai-sdk/openai-compatible\",\"name\":\"AgentGateway\",\"options\":{\"baseURL\":\"https://inference.ai.home.timosur.com/v1\",\"apiKey\":\"{env:AGENTGATEWAY_API_KEY}\"},\"models\":{\"<model>\":{}}}}"
        }
      }
    ]
  }
}
```

`podActivityDeadlineSec` is applied to the Job backend, not the current Sandbox CR template. For the selected `sandbox-cr` backend, Paperclip's explicit 1800-second OpenCode timeout is the run-time ceiling; GitOps `LimitRange`/`ResourceQuota` enforce CPU, memory, and ephemeral-storage limits. The provider's `defaultResources` only supports CPU and memory.

---

### Task 1: Validate version and API compatibility

**Purpose:** The Paperclip Kubernetes provider and Agent Sandbox APIs evolve independently. Pin mutually compatible versions before adding cluster resources.

- [ ] Pin a Paperclip server image and `@paperclipai/plugin-kubernetes` version.
- [ ] **Block implementation until Paperclip PR [#13734](https://github.com/paperclipai/paperclip/pull/13734) is merged and released.** It discovers the served Agent Sandbox API version and supports current `agents.x-k8s.io/v1beta1`; do not pin a deprecated `v1alpha1` controller merely to work around the provider.
- [ ] Pin the Paperclip release containing #13734 and a non-`latest` Agent Sandbox v1.x release. Record the exact image digests and verify the generated Sandbox uses `agents.x-k8s.io/v1beta1`.
- [ ] Pin a Paperclip release at or newer than `v2026.1005.0`, which contains the self-hosted Paperclip Cloud Connector's managed GitHub `github.code` profile and the task-scoped `git`/`gh` credential broker. Verify the selected release retains both before relying on the managed App.
- [ ] Verify the provider supports `runtimeClassName`, `egressMode: cilium`, in-cluster authentication, and `opencode_local` at the selected versions.
- [ ] Validate the committed environment JSON with `paperclipai environment probe-config` before applying it. Confirm that its only placement field is `runtimeClassName`; reject any configuration that purports to set a node selector or pod template.
- [ ] Prove that the self-hosted instance can enroll its Cloud Connector, exposes the managed GitHub connection, and brokers a task-scoped credential to an assigned sandbox. Verify `git`/`gh` can clone a selected private repository, push a disposable branch, and open a draft PR; confirm the credential is unavailable after the run.
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
- [ ] Create `RuntimeClass kata-paperclip` with the Kata handler, `scheduling.nodeSelector: {workload-type: paperclip-sandbox}`, and the existing `availability=daytime:NoSchedule` toleration.
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

### Task 3a: Build the pinned OpenCode sandbox runtime

**Files:**
- Create `.github/workflows/build-paperclip-runtime.yml`.
- Record the upstream Paperclip source commit and resulting image digest in `apps/paperclip/environment.json`.

- [ ] Check out the exact Paperclip source commit selected in Task 1 and build its upstream `docker/agent-runtime` `base` and `opencode` Bake targets for `linux/amd64`.
- [ ] Push the result to `ghcr.io/timosur/homelab/paperclip/agent-runtime-opencode:<paperclip-source-sha>`; record and deploy by its immutable `@sha256:` digest.
- [ ] Verify the image contains `opencode`, `git`, `gh`, `rg`, `/usr/bin/tini`, and `/usr/local/bin/paperclip-agent-shim`, and runs as UID/GID 1000 with a read-only root filesystem plus the provider's standard writable mounts. `gh` is required for the managed GitHub broker workflow to create and review PRs.
- [ ] Do not copy upstream Dockerfiles into this repository. The workflow builds the pinned upstream source so the image, plugin, and server can be upgraded together. Its current Dockerfile installs `opencode-ai@latest`; record the installed OpenCode version in the build artifact and rebuild only for an intentional Paperclip or OpenCode update.
- [ ] Use an `imagePullSecret` only if the GHCR package is private; create the same named Secret in `paperclip-timosur` before the first lease and list it in `imagePullSecrets`.

**Acceptance:** The digest-pinned image passes `opencode --version` in a Kata sandbox and no reference to an upstream `:v1` runtime tag remains.

---

### Task 4: Add required spreading and desktop wake control without forking Paperclip

**Files:**
- Create `apps/paperclip-sandbox-controller/` and its ArgoCD application.
- Update `apps/wol-proxy/` with an internal wake endpoint and its policy.

**Design:** `kata-paperclip` provides the node selector and desktop toleration. One small cluster-local service has two tightly scoped responsibilities:

1. Its mutating admission endpoint accepts only `CREATE` requests for the selected Agent Sandbox API version, resource `sandboxes`, in namespace `paperclip-timosur`. It adds `requiredDuringSchedulingIgnoredDuringExecution` pod anti-affinity matching `paperclip.io/role=agent` with `topologyKey: kubernetes.io/hostname` at `/spec/podTemplate/spec/affinity`. It rejects an unexpected pre-existing affinity rather than overwriting it.
2. Its controller watches only Pods with `paperclip.io/role=agent` in `paperclip-timosur` and the desktop Node. When the anti-affinity-constrained second Pod is Pending and the desktop is NotReady, it calls `wol-proxy` and waits for Kubernetes `Ready`.
3. The normal scheduler creates and schedules the Pod. The service never schedules or deletes Pods or Sandboxes.

- [ ] Add cert-manager-backed TLS and a `MutatingWebhookConfiguration` with `failurePolicy: Fail`, `sideEffects: None`, a short timeout, a namespace selector limited to `paperclip-timosur`, and a resource rule limited to the pinned `agents.x-k8s.io` Sandbox API version.
- [ ] Inject only this pod template fragment; reuse Paperclip's existing Pod label instead of inventing another label:

  ```yaml
  affinity:
    podAntiAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        - labelSelector:
            matchLabels:
              paperclip.io/role: agent
          topologyKey: kubernetes.io/hostname
  ```

- [ ] Add the wake controller with RBAC limited to read/watch the selected Pods and the desktop Node. The admission endpoint needs no Kubernetes write permission.
- [ ] Add a dedicated authenticated internal `wol-proxy` endpoint for waking `homelab-amd-desktop`; it must not be exposed through an HTTPRoute.
- [ ] Allow only the wake controller to reach that endpoint with Cilium policy. Sandbox pods get neither the endpoint nor node-management credentials.
- [ ] Make wake requests idempotent and wait at most 180 seconds for Kubernetes `Ready`.
- [ ] Set Paperclip lease acquisition above that wake window. Do not auto-retry or delete pending Sandboxes: image-pull, resource, or other scheduling failures remain visible to the operator.
- [ ] Verify with a hand-created Sandbox that the Agent Sandbox controller propagates the mutated `podTemplate.spec.affinity` into its Pod before enabling Paperclip. Fail closed if it does not.

**Acceptance:** With desktop asleep, the second sandbox is Pending because the first occupies `homelab-amd`, wakes the desktop, and then runs there. With both nodes ready, two sandboxes occupy different hosts. A third run cannot acquire capacity because of the two-sandbox quota.

---

### Task 5: Deploy Paperclip server statefully

**Files:**
- Create `apps/paperclip/namespace.yaml`, `appdb.yaml`, `external-secret.yaml`, `configmap.yaml`, `deployment.yaml`, `service.yaml`, `pvc.yaml`, retention `CronJob`, `network-policy.yaml`, and `kustomization.yaml`.
- Create `apps/_argocd/paperclip-app.yaml`.
- Create a home HTTPRoute under `networking/httproutes/home/`.

- [ ] Create the `paperclip` namespace with restricted pod-security labels.
- [ ] Create an `AppDBClaim` on central CNPG; use its generated connection secret rather than a per-app PostgreSQL cluster.
- [ ] Create minimal persistent storage only for Paperclip server state, plugin storage, the Paperclip Cloud Connector identity, and retained artifacts/logs. The connector identity contains generated signing/sealing private keys; persist it outside Git with mode `0600` and do not replace it during ordinary Pod rollout.
- [ ] Deploy one Paperclip replica with resource requests/limits, probes, non-root security context, and a dedicated service account.
- [ ] Expose the UI only at a home HTTPS route. Disable public signup and bootstrap the initial local administrator through the supported Paperclip flow using ExternalSecrets-backed bootstrap/admin material. The administrator's home browser must be able to complete the Cloud Connector enrollment redirect; the UI itself remains non-public.
- [ ] Retain the local Paperclip log/artifact volume for 30 days with a daily best-effort pruning Job; confirm central CNPG backups cover its database. Do not claim Paperclip has a built-in retention setting unless the selected release proves it.

**Acceptance:** The home UI is reachable and authenticated, while its service has no Internet HTTPRoute.

---

### Task 6: Configure provider RBAC, isolation, and agentgateway access

**Files:**
- Extend `apps/paperclip/` with provider RBAC and Cilium policies.

- [ ] Pre-create `paperclip-timosur` and the provider's exact expected names (`paperclip-tenant-sa`, `paperclip-tenant-role`, `paperclip-tenant-rb`, `paperclip-quota`, `paperclip-limits`, and the baseline policies) through GitOps. The provider is create-if-absent, not a reconciler: its values are frozen after first creation.
- [ ] Grant Paperclip's server service account fixed-tenant create/read/delete permission for Sandbox CRs, pods/logs, per-run Secrets, and its labelled per-run `CiliumNetworkPolicy` resources. Also grant the reads the provider performs for the GitOps-owned tenant resources and cluster-scoped `get` on only the `paperclip-timosur` Namespace (`resourceNames`); grant no cluster-scoped write permission and no namespace/quota/limit-range/baseline-policy create permission.
- [ ] Use that one tenant namespace for the pilot so its two-sandbox `ResourceQuota` is effective. Do not represent the namespace-scoped quota as a cluster-wide concurrency control if more tenants are added later.
- [ ] Commit `apps/paperclip/environment.json` with `driver: sandbox`, `config.provider: kubernetes`, `inCluster: true`, `namespacePrefix: paperclip-`, `companySlug: timosur`, `backend: sandbox-cr`, `runtimeClassName: kata-paperclip`, `egressMode: cilium`, and `adapterType: opencode_local` so its derived namespace is the GitOps-created tenant. Do not add unsupported selector, affinity, toleration, or pod-template fields.
- [ ] Configure the GitOps-owned namespace quota for two sandboxes. Use the environment's `defaultResources` for CPU/memory; use the GitOps `LimitRange`/`ResourceQuota` for ephemeral storage. Do not claim `podActivityDeadlineSec` limits `sandbox-cr` Pods: enforce the 30-minute run limit with `opencode_local.timeoutSec` and prove cleanup after timeout.
- [ ] Default-deny sandbox ingress/egress. Baseline policy permits only DNS, Paperclip callback traffic, `github.com`, `api.github.com`, and internal `inference.ai.home.timosur.com`; do not allow package registries in the pilot runtime path.
- [ ] Create a dedicated `paperclip-agentgateway-api-key` in Azure Key Vault. Mirror it with ExternalSecrets into the Paperclip namespace as `AGENTGATEWAY_API_KEY` and into `agentgateway-system` as a labeled API-key Secret. Change the strict AgentGateway policy from a single `secretRef` to a `secretSelector` that admits the existing clients and the Paperclip key independently, so Paperclip access can be revoked without rotating other clients.
- [ ] Mount `AGENTGATEWAY_API_KEY` into the Paperclip server from its ExternalSecret and list it as the sole `opencode_local` adapter `envKey`. The Kubernetes provider then copies only that configured value into its Sandbox-owned per-run Secret, which is deleted with the Sandbox. OpenCode's OpenAI-compatible provider sends it as `Authorization: Bearer …` to AgentGateway; AgentGateway validates it before applying its separate upstream-provider credential. Do not inject upstream model-provider credentials into sandbox pods.
- [ ] Configure the provider's authoritative `adapters` registry with its sole `opencode_local` entry: set `runtimeImage` to the Task 3a digest, `envKeys` to only `AGENTGATEWAY_API_KEY`, `allowFqdns` to only `inference.ai.home.timosur.com`, and `defaultEnv.PAPERCLIP_OPENCODE_PROVIDERS` to an `@ai-sdk/openai-compatible` AgentGateway provider at `https://inference.ai.home.timosur.com/v1` with `apiKey: {env:AGENTGATEWAY_API_KEY}` and an explicit `models` map. Set both the agent model and `PAPERCLIP_OPENCODE_SMALL_MODEL` to an AgentGateway-served `agentgateway/<model>` ID. Do not set `imageAllowList`: it authorizes agent overrides, not this runtime image. Do not add AgentGateway alongside the built-in upstream-provider credentials or FQDN grants.
- [ ] Allow the Paperclip **server** (not sandbox pods) outbound HTTPS to `my.paperclip.app` for the managed Cloud Connector. Keep the sandbox baseline limited to DNS, Paperclip callback traffic, `github.com`, `api.github.com`, and internal AgentGateway. The managed runtime broker delivers GitHub credentials through the callback bridge; do not mount static GitHub credentials into the server or sandbox.
- [ ] Use per-task Cilium FQDN grants only for exceptional destinations. When baseline egress blocks a task, the agent reports the exact attempted hostname and reason; it cannot self-approve or retry. A human reviews a GitOps approval record containing the Paperclip task ID, exact FQDNs, reason, and expiry, then manually sets that task's `executionWorkspaceSettings.networkEgress.allowFqdns` in Paperclip and reruns it. Do not approve wildcards or CIDRs for the pilot. This deliberately gives Paperclip namespace-scoped create/delete access to its own per-run policies; it does not give it namespace or baseline-policy authority.

**Acceptance:** An agent can use OpenCode and agentgateway, clone the permitted repository, and cannot reach arbitrary LAN services or unapproved Internet destinations.

---

### Task 7: Configure the managed GitHub connection and repository projects

**Files:** None. This is a documented manual Paperclip/Paperclip Cloud setup until stable self-hosted APIs exist.

- [ ] Enroll the self-hosted Paperclip instance with the Paperclip Cloud Connector. Persist its generated identity at the Paperclip server state path; it contains signing/sealing private keys and must never be committed, logged, or injected into agent environments.
- [ ] In the `timosur` Company, connect the existing Paperclip-managed GitHub App and select exactly the repositories approved for shared agent write access. The App installation and Paperclip repository resource filters are the authoritative repository allow-list.
- [ ] Grant that managed connection to `coder`, `reviewer`, and `platform-engineer`. The selected repositories are a deliberate shared-trust boundary; Paperclip project assignment and agent instructions provide workflow separation, not GitHub credential isolation.
- [ ] Create one Paperclip project for every selected repository. Give each project one isolated workspace with its HTTPS `repoUrl` and base ref. Do not use a product project as a multi-repository checkout; link its repository issues to a common goal instead.
- [ ] Give `coder` PR-driven instructions: work only in the assigned project workspace, commit/push a branch, open/update a PR, and delegate a review task after CI passes. Never merge, force-push a shared branch, or change credentials/agent authority from issue or repository content.
- [ ] Give `reviewer` instructions to review the linked PR and report or post actionable findings. A requested change returns to the coder's Paperclip issue; no GitHub event starts another agent run.
- [ ] Give `platform-engineer` instructions to work only on the `homelab` project. After a human merges/releases an application PR, create a separate platform issue containing the immutable image digest, source release/PR URL, and requested deployment change. The platform engineer verifies that handoff, opens a `homelab` manifest PR, and never changes application source repositories as part of the deployment task.
- [ ] Require protected target branches/rulesets on every selected repository: no direct pushes, required human approval, and no GitHub App bypass. Verify a managed-credential `gh pr merge` attempt is rejected.
- [ ] Treat Paperclip issues, GitHub PR text/comments, and repository content as untrusted. None may authorize credentials, broaden repository access, change agent authority, or enable tools/egress.

**Acceptance:** A UI/API-created issue in a repository project lets the coder open a PR, delegate review, and receive requested changes. After the source PR is human-merged and released, a separate platform issue lets `platform-engineer` open a `homelab` manifest PR using the published immutable digest. No public GitHub webhook route exists.

---

### Task 8: GitOps bootstrap Paperclip configuration

**Files:**
- Create `apps/paperclip/environment.json` and a one-shot, idempotent bootstrap Job under `apps/paperclip/`.

- [ ] Install the pinned Kubernetes sandbox provider plugin.
- [ ] Read `environment.json`, validate it with `environment probe-config`, then create the named environment when absent or update its existing ID when present. The Job must never log the payload after secret interpolation and must not use `imageAllowList` as an adapter-image selector.
- [ ] Set the lease timeout, Task 3a runtime-image digest, `kata-paperclip` runtime class, Cilium egress mode, and quota tier through the environment JSON and GitOps-owned tenant resources.
- [ ] Keep Cloud Connector enrollment, managed GitHub connection/grants, Company/project workspaces, and agent creation as documented manual setup. The GitOps Job must not overwrite them and must not require a GitHub webhook endpoint.
- [ ] During the manual setup, create `coder`, `reviewer`, and `platform-engineer` in the `timosur` Company. Assign GitHub access through the managed connection, give none merge authority, and apply the Task 7 role instructions.
- [ ] Configure the internal review flow: coder opens/updates a PR and delegates review; reviewer returns findings to the coder issue; a human merges the source PR. After merge/release, a human creates the separate `homelab` platform issue with the published immutable digest.
- [ ] Set `opencode_local.timeoutSec: 1800` explicitly (its default is longer), enforce the verified $5 Paperclip budget, require human approval for additional egress, and disable automatic retry. Do not represent `podActivityDeadlineSec` as a `sandbox-cr` wall-clock limit.
- [ ] Make the bootstrap safe to rerun and ensure it never logs secret material.

**Acceptance:** A fresh Paperclip database can be bootstrapped from GitOps resources and ExternalSecrets after the documented manual Cloud Connector/GitHub setup. Re-running the Job updates the named environment from committed JSON without duplicating it or overwriting the connection, grants, company, projects, or agents.

---

### Task 9: Monitoring, failure handling, and pilot

**Files:**
- Extend `apps/monitoring/` with Paperclip/sandbox alert rules and dashboard panels as needed.

- [ ] Alert on Paperclip unavailable, sandbox acquisition timeout, desktop wake timeout, Sandbox CR/pod cleanup failure, Kata test failure, quota exhaustion, and OOM kills.
- [ ] Alert on Cloud Connector enrollment/credential-broker failure and managed GitHub credential denial; distinguish these from GitHub repository permission failures.
- [ ] Monitor desktop readiness separately so a sleeping desktop is distinguishable from a failed wake.
- [ ] Run one UI/API-created coding task in a selected repository and verify the managed connection opens a branch and PR without a static GitHub credential in the sandbox.
- [ ] Verify coder-to-reviewer delegation, review findings returned to the coder issue, and the coder's PR update. Confirm no GitHub delivery or mention creates work.
- [ ] Human-merge/release that source PR, then create a platform-engineer issue containing its immutable image digest. Verify the platform agent opens the corresponding `homelab` manifest PR.
- [ ] Confirm a human must merge and that Paperclip cannot merge the pull request.
- [ ] Test blocked egress, approved task-scoped egress, Cloud Connector unavailability, desktop wake timeout, and cleanup after a failed run.

**Acceptance:** The pilot completes end-to-end without leaked sandboxes, unscoped credentials, unintended public UI exposure, or agent merge capability.

---

## Manual prerequisites

- [ ] Make `homelab-amd-desktop` reachable for its Kata installation and verification.
- [ ] Confirm both sandbox nodes have stable power/networking and virtualization enabled.
- [ ] Decide and provision the Paperclip home HTTPS hostname. An administrator browser must reach it to complete Cloud Connector enrollment; do not provision a public GitHub webhook hostname/path.
- [ ] Enroll the self-hosted instance with Paperclip Cloud, connect the existing Paperclip-managed GitHub App, and select the repositories approved for the shared-trust pilot.
- [ ] Define each selected repository's Paperclip project/base branch and configure its protected target-branch ruleset requiring human approval, denying GitHub App bypass, and denying direct pushes.
- [ ] For every application that deploys through `homelab`, ensure its release process publishes an immutable image digest before a platform-engineer deployment issue is created.
- [ ] Prove AgentGateway cost reporting and Paperclip's $5 budget enforcement before enabling pilot agents.

## Explicitly deferred

- `paperclip-operator`
- More than two concurrent agents
- Persistent agent workspaces or warm pools
- Agent merge authority
- `homelab-gpu` and ARM nodes as sandbox capacity
- GitHub-initiated task creation, webhook triggers, and automatic GitHub-event review scheduling
