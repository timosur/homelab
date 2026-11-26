# Design

## Context

Verified against the current tree:

- `proxy.py` `NodeGroupProxy._send_desktop_wake` sends a WoL packet to every `sandbox_pool.workers` entry via `pool.wake_all`; it is also the `wake` callback of `SleepCoordinator`.
- `PoolMonitor.node_ready()` is `all(readiness())`. `SleepCoordinator._tick` keeps one `accepting` flag, one `fenced_epoch`, one `lease_revision` tuple and one `last_wake` timer; it opens only after every worker is fenced under `interlock.epoch` with every lease advanced. `tick()` resets the gate on any exception.
- The interlock (`interlock.py`) is one ConfigMap record: `epoch`, `owner`, `phase` (`idle|releasing|suspending`), `http_active`, `coder_starts` (list), plus resourceVersion/uid (`anchor`). `claim()` raises while `releasing`, and while `http_active` belongs to a previous owner. `http_change` and `coder_reservation` raise unless the phase allows them (`suspending` blocks `http_change`).
- Idle sleep (`_sleep_pool`, from `suspend-degraded-worker-pool`) fences then suspends each Ready worker with the single epoch, then `retire_suspend_epoch()` bumps it, invalidating every worker's fence. After the sleep the phase stays `suspending` until every worker is open (`coordinator.py:152-153`); in `suspending`, `http_change` and `_tick` (`coordinator.py:159-160`) raise.
- Host guard (`power_guard.py`, installed by `ansible/roles/dark-factory-power-guard`): per-host state `{epoch, owner, anchor, phase}`. `fence` rejects `epoch < state.epoch`, a changed anchor, or the same epoch under another owner; `suspend` requires an exact `(epoch, owner, anchor)` match and an unresolved suspend blocks the next fence. The sudoers file allows only `fence *` and `suspend *`. The guard is already independent per host.
- Sandbox admission (`admission.py:76-94`) mutates pod CREATE to add the `desktop-awake` scheduling gate; the validating webhook lets only the controller remove it, and `SandboxMonitor.can_release` requires `phase == releasing` and a token `epoch:owner:anchor`. Coder admission (`coder_admission.py`) does the same with `pool-awake`. Both apps run in the wol-proxy process (`server.py:build_admission_app` shares `group._sandbox_monitor`), so a webhook can read the coordinator's gate state in-process.
- `release_gates`/`release_coder_gates` (`sandboxes.py`) patch every gated pod after a workspace reservation (`workspace.py` records `possible_nodes`). Coder preflight (`coder_preflight.py`) reserves via `interlock.coder_reservation` once `coordinator.accepting`.
- `apps/wol-proxy/deployment.yaml` runs one replica with `strategy: Recreate`, so two controller images never run together. Sandboxes mount NFS-backed `ReadWriteOncePod` claims (`dark-factory-nfs`) that can attach on either worker; `runs.maxConcurrent` is **8** (`apps/dark-factory/values.yaml:72`, raised from 4 in `bc84607`).

## Goals / Non-Goals

**Goals:** a consumer wakes and waits only for the worker it needs; protected work (sandboxes, Coder) uses one worker chosen by preference and pinned at admission; a worker sleeps independently when it has no demand; safety is not weaker than today for any worker.

**Non-Goals:** changing the host guard wire contract or sudoers; changing `wake-gpu-nodes.sh`; treating NotReady as asleep; re-pinning a running pod; automatic re-pin of gated pods; any live rollout.

## Decisions

### 1. Required sets

| Consumer | Required workers |
|---|---|
| Ollama | `workers:` (deployed `{homelab-gpu}`; default all) |
| Desktop node-group routes | `workers:` (deployed `{homelab-amd-desktop}`; default all) |
| Sandbox pod | its pinned worker (annotation); unannotated protected pod -> all workers |
| Coder workspace | its reserved worker (reservation); unreserved -> never released |

HTTP routes keep an explicit list because their targets are fixed. Protected work is chosen per workload. Pool config adds an ordered list `sandboxPool.preference: [homelab-amd-desktop, homelab-gpu]` (default: worker order). Workers absent from the list host no protected work (for example, to keep the GPU free for Ollama). A single-worker pool becomes valid when only one worker exists and the preference list has one entry; the proxy still requires >= 1 worker, distinct hosts/MACs, and an exact match of the primary node; the former "len(workers) >= 2" check (`proxy.py:~436`) is relaxed to `>= 1`. The default for backends with no `workers:` remains "all pool workers".

### 2. Worker choice and capacity

`choose_worker(state)` runs in the webhook/preflight, under the coordinator's lock, and returns the first worker in preference order that is (a) open on the coordinator's in-memory gate state and (b) under capacity; otherwise the first under capacity; otherwise the first in the list. Open-state at admission is a *hint*: correctness never depends on it, because release re-checks fresh state (Decision 5). A worker whose phase is `suspending`/`releasing` is not open.

Capacity: the old `defaultRequestMemory` comment says the scheduler "spreads sandboxes across both workers"; with pinning one worker can take all `maxConcurrent: 8` runs. Optional `capacity: N` per pool worker counts non-terminal protected pods (sandbox plus Coder) annotated for that worker; omitted means unlimited and the Kubernetes scheduler and the namespace ResourceQuota queue the excess (Pending). When every worker is full the pod is pinned to the first in preference order and queues, so run creation never fails. Recommended value is set from allocatable CPU/memory after measuring (open question 3). Counting is best-effort under concurrent creates; the webhook serializes choices with a lock.

### 3. Pinning at admission (sandboxes)

The mutating webhook on pod CREATE, in addition to the existing gate: sets annotation `dark-factory.home.timosur.com/worker: <node>` and a required node affinity on `kubernetes.io/hostname In [<node>]`. Existing `nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution.nodeSelectorTerms` are ORed, so the expression is appended to **every** term (or a single term is created). A `nodeSelector` with a conflicting `kubernetes.io/hostname` rejects the pod. The validating webhook (CREATE and UPDATE) requires the annotation to name a pool worker in the preference list, the affinity to contain the matching expression in every term, forbids changing either, and keeps allowing gate removal only by the controller. The annotation (not a label) is authoritative: it is read by demand, sleep, readiness and release; the affinity is what binds the scheduler. Pods without the annotation (created before rollout or by an older image) require *every* worker open to release and pin all workers awake, preserving today's conservative behavior until they drain.

Why annotation + affinity rather than release-time patching: the choice must exist while the pod is gated so the wake, demand and sleep logic can attribute it to a worker. Kubernetes allows only narrowing of node affinity on gated pods, so the pin cannot be added later without recreating the pod.

### 4. Coder pinning

The preflight chooses the worker (Decision 2), wakes and waits only for it (`accepting_for({node})` and `nodes_schedulable({node})`), and records the reservation as `coder_starts[workspace_id] = node`. The existing Coder mutating webhook already runs on every workspace pod CREATE (it adds the `pool-awake` gate), so it also reads the reservation for `workspace_id(pod)` and adds the annotation and node affinity exactly as for sandboxes. **Decision: pin in the webhook, no template change.** Reasons: it is the trusted, fail-closed enforcement point already required for Coder pods; a template-supplied node would be controlled by workspace parameters and would need a Terraform template push plus workspace rebuilds; the template's affinity (`apps/coder/templates/board-member/main.tf:125-135`, hostname `In [desktop, gpu]`) is a superset and intersects safely with the appended expression. A pod created without a reservation (or with one the webhook cannot read) is gated but unpinned and, as today, never released. The validating webhook treats annotation/affinity as immutable and rejects pods whose annotation disagrees with the reservation. Residual risk: the template's Terraform may see drift from server-side mutation; the existing gate mutation shows this is tolerated, but verify (open question 5).

Reservations (`interlock.coder_reservation`) become per worker: the stored map is `{workspace_id: node}`, clearing is unchanged (on observation of the ungated pod), and a reservation for node W counts as demand on W.

### 5. Release barrier and failure classes (replaces the "all workers" barrier)

The barrier that matters is in `_tick`, immediately before `interlock.transition(<worker>: idle -> releasing)` (`coordinator.py:166-168`), under the activity lock: for each worker with gated pinned pods, recompute that worker's gate from state read **in this tick** (Ready, fenced under its current epoch, lease advanced) and release only if open. The webhook callbacks `can_release`/`can_release_coder` (`sandboxes.py:209,273`) stay phase/token checks: they only see the interlock record (the worker's phase is `releasing` and the token binds `epoch:owner:anchor:<node>:<worker_epoch>`). A worker that opens and closes between ticks therefore gets no release.

Failure classes:
- *Worker-local* (that worker's readiness/lease read, its fence failing): close only that worker's gate.
- *Shared* (`verify_admission`, interlock claim/transition, `any_workload`/`coder_pods`, unknown exceptions): `close_all()`.
- *Wake failure* (`wol.py:45,49` raise `RuntimeError`; `pool.py:20-23` `wake_all` re-raises): its own class. `_wake_if_needed` catches and logs per worker, still updates `last_wake[node]` so the 15 s limit holds, and closes no gate.

A pod pinned to a worker that becomes NotReady stays gated and the worker is woken (rate limited). It is **not** silently rescheduled. A re-pin would mean deleting and recreating the pod (affinity cannot be widened on a gated pod) and is the control plane's decision, not the proxy's; open question 2.

### 6. Interlock v2 and host guards

Record (same ConfigMap and name):

```
version: "2"
epoch: <ownership epoch, bumped on claim>          # legacy-compatible
v2_epoch: <epoch as last written by a v2 writer>
owner, anchor: unchanged
workers: JSON {node: {"epoch": N, "phase": "idle|releasing|suspending", "http_active": n}}
coder_starts: JSON list of ids                      # legacy-compatible union
coder_workers: JSON {workspace_id: node}
phase: aggregate (legacy view): "releasing" if any worker releasing, else "suspending" if any suspending, else "idle"
http_active: sum of workers' counts (legacy view)
```

- **Per-worker epoch** `workers[node].epoch` is strictly increasing per host. A takeover (new owner) sets every worker epoch to `max(workers[*].epoch, epoch) + 1`; `retire_suspend_epoch(node)` increments only that worker's epoch. The value is passed unchanged as the guard's `epoch`: the guard already accepts any larger epoch with a new owner and rejects any smaller or equal-different-owner, so stale-epoch rejection stays per host and **the guard contract, sudoers file and `wake-gpu-nodes.sh` do not change**. This is verifiable in `tests/test_power_guard.py` (independent epoch sequences per host). Each host's guard already holds an epoch <= the old global epoch (the old controller sent the global epoch to both hosts), so starting every worker above the old global epoch keeps the guard's monotonic rule satisfied.
- **Phases:** per worker. `releasing` blocks that worker's sleep and any takeover (claim still raises if any worker is `releasing`, preserving the operator-recovery rule). `suspending` is the existing post-sleep marker, cleared by `suspending -> idle` for that worker (below).
- **`suspending -> idle` (rework of M1):** when worker W's gate opens (Ready, fenced under `workers[W].epoch`, lease advanced) and `needs_retire[W]` is false, the coordinator transitions W `suspending -> idle` under the activity lock, independent of the peer. Safe because W's epoch was retired and W is fenced under the new epoch (the guard rejects older requests); release and Coder reservation to W require W `idle` and open on fresh state; the peer's phase is irrelevant. `http_change(workers, +1)` raises only if one of *its required* workers is `suspending`.
- **Tokens:** `epoch:owner:anchor:<node>:<worker_epoch>`. `can_release` for a pod reads the pod's worker from its annotation and compares with that worker's record; a token for another worker or epoch fails.
- **Migration (v1 -> v2):** on first claim, a record without `version` (or with `epoch != v2_epoch`, meaning a legacy writer touched it) is converted in one CAS PUT: every worker `epoch = old_epoch + 1`, every worker phase = `suspending` if the legacy phase was `suspending` else `idle` but with **no open gate** (fresh fence and lease required before anything is served), `http_active` split as 0 per worker (a nonzero legacy count belonging to another owner still blocks `claim()` as today), each legacy Coder reservation mapped to `"*"` (unknown worker: pins every worker awake, released only after the pod is observed or an operator clears it), `v2_epoch` = new epoch, legacy aggregate fields rewritten. The same conversion re-runs if `epoch != v2_epoch`, which also makes a rollback to the old image safe: the old image keeps preserving unknown keys while only touching `epoch/owner/phase/http_active/coder_starts`, so the next v2 claim detects the mismatch and re-migrates conservatively.
- **Rollout order:** (1) deploy the new image (guard role unchanged, so no Ansible run is needed). `Recreate` strategy means the old controller is stopped before the new one claims; (2) the new image migrates the record, enables per-worker gates, and treats existing unannotated pods as all-worker; (3) only then enable pinning in the webhook (config flag `sandboxPinningEnabled`, default on in the same image after verification) and the configmap `preference`/`workers` entries. If the image is rolled back after step 2, the old controller sees conservative legacy fields (`phase` aggregate, `http_active` sum) and cannot release pinned pods (their tokens carry the node and worker epoch), so they stay gated; they are released after the roll-forward. Webhook and controller are one image, so tokens are never validated by a mismatched version.
- A guard that is out of date or a host with an unresolved suspend simply keeps only that worker's gate closed (existing fail-closed behavior); nothing here requires reinstalling guards. Binding the node name into the guard request as defense against mis-routed SSH is *not* done here (needs a sudoers change); see open question 4.

### 7. Wake targeting

`_wake_if_needed(workers)` replaces `_wake_if_needed(demand)`; `_send_desktop_wake(workers=None)` sends packets only to workers in the set that are not Ready per `PoolMonitor.readiness(workers)` (if that read raises, the whole required set, never other workers). The per-host 15 s limit is a map `last_wake[node]`. Wake failures follow Decision 5. `ProxyBackend.wake_and_wait` is the uncoordinated path (own host, `_wake_lock`, no 15 s limit) and is unchanged. The shared WoL relay (one `wolHost` for both workers) is a common-mode failure; per-route scoping does not remove it.

### 8. Per-worker demand and idle sleep

Demand per worker W:
- HTTP: `workers[W].http_active > 0`, in-process `_active_requests` for routes whose set contains W, a pending `_wake_requested` entry for W, and `last_activity[W]` within `idleTimeoutMinutes` (max over routes needing W).
- Protected: any non-terminal sandbox or Coder pod annotated W (gated, pending, running, or terminating), any Coder reservation for W, or any unannotated protected pod / reservation `"*"` (pins every worker). `SandboxMonitor.any_workload()` becomes `workloads_by_worker()` returning the set of pinned workers (plus `ALL`).

`_sleep_pool` becomes `_sleep_worker(W)`: under the activity lock, claim the interlock, transition `W: idle -> suspending`, `verify_admission`, recheck W's demand (abort and restore `idle` if any), then fence W with `workers[W].epoch`, suspend W through the guard, `retire_suspend_epoch(W)`, close W's gate. The 60 s retry limit is per worker. A suspend of W never touches the peer's epoch, phase or gate, so the peer keeps serving. Workers are iterated independently so one failure does not block another (degraded-sleep behavior kept).

A pod admitted for W after the recheck is safe: it is gated and annotated, so W is seen as demand, woken and fenced before release.

### 9. Safety invariants (replacing "all workers ready and fenced")

1. Protected work is admitted/released only onto a worker whose own gate is open on fresh state, and only with a token bound to that worker and its current epoch.
2. A worker is never suspended while it hosts or is reserved for protected work, or has HTTP demand/in-flight requests from a route that needs it. Unannotated or `"*"` work pins every worker.
3. A pod pinned to a NotReady/closed worker stays gated and is not rescheduled.
4. Order of checks in the sleep path is unchanged (claim, transition, `verify_admission`, recheck, then power calls).
5. After any fence/suspend attempt, including uncertain ones, that worker's epoch is retired and its gate closed; reopening requires a new fence and fresh lease.
6. NotReady means unknown, not asleep.
7. Stale-epoch rejection stays per host; a worker's request can only be valid for that worker's epoch.

### 10. Supersession of `suspend-degraded-worker-pool`

That change (idle sleep of reachable workers when a peer is NotReady, workspace release only on a fully fenced pool) is superseded: per-worker sleep subsumes its "pool idle" condition and per-worker release replaces "complete pool ready and fenced" for protected work. Its guarantees that survive are listed in the `Supersession` requirement (NotReady not assumed asleep, unresolved guard keeps only that worker closed, independent attempts). Ordering: archive `suspend-degraded-worker-pool` first (its code is already in `main`), then archive this change; this change's spec deltas modify no requirement owned by it except the base "Demand-woken workspace lifecycle", and the `coder-dev-workspaces` requirement it ADDED ("keep workspace admission closed until the complete pool is ready") must be amended in the same archive step (tasks 9.3).

### 11. Config and validation

```yaml
backends:
  - name: ollama
    workers: [homelab-gpu]                 # optional; default all
nodeGroups:
  - name: desktop
    sandboxPool:
      preference: [homelab-amd-desktop, homelab-gpu]   # optional; default worker order
      workers:
        - node: homelab-amd-desktop
          capacity: 8                      # optional
          ...
    backends:
      - hostname: vinyl.home.timosur.com
        workers: [homelab-amd-desktop]     # optional; default all
```

- `config.py`: parse `workers` (backends, node-group backends), `preference` and per-worker `capacity` (positive int).
- `server.py:build_proxies` (`:27-55`): for port backends in `sandboxPool.backends`, `workers` must name pool workers and include the worker matching the backend's `targetHost`/`wolMac`; **reject `workers:` on any port backend whose pool has `sandboxPowerInterlockEnabled` false** (here, not in `NodeGroupProxy`, which only sees node-group backends); reject on port backends outside every pool. `preference` must be distinct pool workers (non-empty).
- `proxy.py:426-455`: relax `len(workers) < 2` to `< 1`; node-group backend `workers` must be a non-empty subset of the pool and are rejected when the interlock is disabled or there is no pool.
- Removing the interlock flag and `workers:` must happen in the same change, otherwise the proxy refuses to start (record this in the migration plan and docs).

### 12. Status

`/wol-proxy/status` reports the requested host's required set. Cached Ollama paths and the wake page are unchanged.

## Risks / Trade-offs

- [Per-worker epochs and aggregated legacy fields increase interlock complexity] -> one persisted-format change, explicit `v2_epoch` mismatch detection, conservative migration, tests for forward, backward and mixed writers.
- [A worker-local gate may be left open by a mis-classified error] -> unknown exceptions are shared and close everything.
- [One worker can take all `maxConcurrent: 8` runs] -> optional `capacity`; recommend setting it after measuring; excess queues Pending, never fails creation.
- [Pod pinned to a dead worker stays gated indefinitely] -> by design; surfaced through logs/metrics; control plane may recreate (open question 2).
- [Webhook choice is made on a hint] -> correctness relies only on the release barrier.
- [Template drift from webhook-mutated Coder pods] -> verify before rollout.
- [Shared WoL relay] -> common-mode, documented.

## Migration Plan

1. Land code and tests. Rollout order in Decision 6: image first (guard unchanged), conservative v2 migration, then pinning and config. 2. Config: add `workers:`/`preference`/`capacity` to `apps/wol-proxy/configmap.yaml` only after the image runs. 3. Docs update (task 8). 4. Controlled-window verification: GPU-only Ollama wake, desktop-only vinyl wake, sandbox pinned to the desktop with the GPU asleep, GPU sleeps while the desktop serves a sandbox, closing a worker between gate-open and release, restart during `suspending`, and roll back/roll forward of the image. 5. Never reset the interlock ConfigMap or guard state as routine recovery. No live actions are part of authoring this change.

## Open Questions

1. **Per-worker `capacity` value and whether it should be derived from node allocatable** instead of configured (default is unlimited).
2. **Re-pin of a gated pod whose worker is down for a long time:** this change keeps it gated. Should the control plane delete/recreate it after a timeout, and does the proxy need to expose "stuck pinned pod" metrics?
3. **Whether workers excluded from `preference` (e.g. keep the GPU Ollama-only) is desired** for Coder, which currently allows either node in the template.
4. **Guard sudoers contract.** Not changed here. Optionally bind the worker node name into the guard request/state as defense against mis-routed SSH; needs a sudoers and Ansible change and a per-host rollout.
5. **Coder template drift:** confirm Terraform tolerates a webhook-added affinity and annotation on the workspace pod (no template push is planned; if it does not, the template needs an `ignore_changes` edit and a push, which needs approval).
6. **`/wol-proxy/status`:** expose per-worker gates? Safe to extend?
7. **Per-route idle timeouts:** worker timeout = max over routes needing it (proposed).
8. **Wake timeout semantics** when the pinned worker never wakes: fail the Coder preflight after `wakeTimeoutSeconds` (current behavior) and leave sandbox pods gated indefinitely (proposed).
