# Design

## Context

Verified against the current tree:

- `proxy.py` `NodeGroupProxy._send_desktop_wake` sends a WoL packet to every `sandbox_pool.workers` entry via `pool.wake_all`; the same method is the `wake` callback of `SleepCoordinator`.
- `PoolMonitor.node_ready()` is `all(readiness())`. `SleepCoordinator._tick` keeps one `accepting` flag, one `fenced_epoch`, one `lease_revision` tuple and one `last_wake` timer. `accepting` opens only after every worker is fenced under `interlock.epoch` and every lease has advanced.
- Ollama (`ProxyBackend.handle_request`) and desktop routes (`NodeGroupProxy.handle_request`) both refuse traffic while `coordinator.accepting` is false and set the shared `proxy._wake_requested`. Desktop `check_node_health` returns `PoolMonitor.node_ready()` (all workers). Coder preflight (`coder_preflight.py`) waits for `accepting` and `nodes_schedulable()` (all workers).
- Idle sleep (`_sleep_pool`, from `suspend-degraded-worker-pool`) uses one shared idle timer and one interlock phase (`idle` -> `suspending`), fences then suspends each Ready worker, then retires the single suspend epoch. Retiring bumps the one global epoch, which invalidates *every* worker's fence.
- Interlock state (`interlock.py`) is one ConfigMap record: `epoch`, `owner`, `phase`, `http_active`, `coder_starts`, anchor.
- Dark Factory sandbox pods are scheduling-gated at admission (`admission.py`) before a node is chosen; the controller removes the gate only after the pool is fenced.

## Goals / Non-Goals

**Goals:** a consumer wakes and waits only for workers it needs; a worker unavailable to one route does not block unrelated routes; idle sleep is per worker where provably safe; sandbox and Coder protection is no weaker than today.

**Non-Goals:** knowing a sandbox's node before scheduling; letting Coder pick "any one worker"; changing the host guard protocol or `wake-gpu-nodes.sh`; treating NotReady as asleep; any live rollout.

## Decisions

### 1. Per-worker gate, per-route required set

Introduce `WorkerGate` state per worker node: `fenced_epoch`, `lease_revision` (taken at fence), `open` (fenced under the current epoch for that worker *and* lease advanced *and* still Ready). The coordinator keeps `gates: dict[node, WorkerGate]` and exposes `accepting_for(workers: frozenset[str]) -> bool` = all requested gates open. `accepting` becomes `accepting_for(all workers)` and keeps its meaning for sandboxes.

Required sets:

| Consumer | Required workers | Reason |
|---|---|---|
| Ollama (`ProxyBackend`) | `{homelab-gpu}` | Fixed target (`targetHost` is the GPU). |
| Desktop node-group routes | `{homelab-amd-desktop}` | Fixed target; backends run on that node. |
| Dark Factory sandboxes (admission, gate release) | all workers | Node unknown until the scheduler runs, so every node must be safe before the gate is removed. Unchanged. |
| Coder workspace start (`/coder/preflight`, gate release) | all workers | See below. |

**Coder: all workers (decision).** The board-member template allows either node (`kubernetes.io/hostname In [homelab-amd-desktop, homelab-gpu]`), and the pod is protected by the same scheduling-gate/release mechanism as sandboxes. "Any one schedulable worker" would need a way to keep the pod off a sleeping or soon-to-sleep node: either rewriting node affinity at release time (a Terraform template or admission-mutation change plus a race between node selection and a per-worker suspend), or a per-pod pinned-node reservation held in the interlock. Both add a new correctness surface for a modest power saving on an infrequent, already-slow start. The safe default is to keep Coder on the all-workers set, so a Coder pod is never released while a worker it may land on is asleep, unfenced or unleased. Revisit in a follow-up (open question 2) if Coder starts become frequent enough to matter.

### 2. Wake targeting

`SleepCoordinator._wake_if_needed(demand)` becomes `_wake_if_needed(workers)` and `wake` takes a worker set: `NodeGroupProxy._send_desktop_wake(workers=None)`; `None` means all (sandboxes, Coder, non-pool groups). Only workers in the set that are not Ready (per `PoolMonitor.readiness()`) receive a packet. The 15 s rate limit becomes per host (`last_wake[node]`) instead of one pool timer, so a GPU wake does not suppress a later desktop wake and vice versa; a worker that is Ready but unfenced is *not* woken, it is only fenced. `wake_all` is retained as the concurrent helper over the selected subset and still propagates failures, but a failed packet for one worker only keeps the routes that need that worker closed; it never raises into routes with a different required set. `wake_and_wait` on `NodeGroupProxy` and `ProxyBackend` polls `node_ready(required)` rather than the all-workers check, and the wake timeout is evaluated per required set.

### 3. Per-worker demand and sleep

Demand state is keyed by worker:

- `_wake_requested` becomes a set of nodes (union of required sets of refused or pending requests). The accepting-time reset clears only nodes that just opened.
- In-flight HTTP: `proxy._active_requests` and `interlock.http_active` become per node, incremented for each node in the route's required set. Ollama requests pin the GPU only; vinyl/paperless pin the desktop only.
- Protected work pins all workers: any sandbox workload, durable Coder reservation or unreleased gated pod (`any_workload()`, `coder_starts`, `has_gated_pods()`) counts as demand on every worker, because the destination node is unknown or may be either.

A worker W is *idle-eligible* when: it is Ready; no protected sandbox/Coder work exists; `http_active[W] == 0`, no in-flight request requires W and no wake is pending for W; and W's own idle timer (`now - last_activity[W]`) exceeds `idleTimeoutMinutes`. The existing admission verification, activity lock, final recheck, fence-then-suspend order and 60 s retry limit are preserved per worker. `_sleep_pool` iterates eligible workers instead of requiring pool-wide idleness; `PoolPowerClient.suspend_ready` already accepts per-worker booleans and needs a per-worker epoch (below).

Consequences: a GPU used only by Ollama sleeps independently of the desktop and vice versa. As soon as any sandbox or Coder activity exists, no worker sleeps, matching today's conservative behavior.

### 4. Epochs, fencing and the interlock (staged)

Today one global epoch is both controller ownership fencing and the suspend-retirement token. Retiring it on one worker's suspend would invalidate the fence on awake peers and close their gates (stalling, say, Ollama while the desktop sleeps). Per-worker sleep therefore needs a suspend epoch per worker.

Proposed interlock record: keep `owner`, `anchor` and ownership `epoch`; add `workers: {node: {suspend_epoch, phase, http_active}}`. Host guards on each worker receive `(ownership epoch, worker suspend_epoch, owner, anchor)`; a retired worker epoch invalidates only that worker's delayed suspend and fence. `phase` splits into a global `releasing` exclusion flag (gate release, unchanged) and per-worker `idle|suspending`. Gate release requires every worker `idle` and open.

This changes persisted state and the host-guard request contract, which are fail-closed and safety-critical; it must not be guessed. Rollout is therefore staged, and stage 1 is independently shippable:

- **Stage 1 (route-scoped wake and gate, pool-wide sleep):** per-worker gates, `accepting_for`, targeted wake, per-route health/status, `workers:` config. Sleep keeps the single global epoch and single idle timer, i.e. today's `_sleep_pool` rules, and the retire step still resets every worker gate (re-fence after sleep). Ollama and desktop routes already stop waking/blocking on the other worker; any HTTP activity on any route keeps *all* Ready workers awake exactly as now.
- **Stage 2 (per-worker idle sleep):** per-worker suspend epochs, per-worker `http_active`, per-worker timers, interlock schema migration with backward-compatible read of the old record (an old single record is read as all workers sharing the old epoch and phase) and a rollback path.

Stage 2 requires approval of the interlock/host-guard contract change (open question 1) before implementation.

### 5. Sandbox admission safety (invariants)

These hold in both stages and each has a scenario in the spec:

1. **No partial admission.** Sandbox gate release, `/coder/preflight` approval and `can_release*` require `accepting_for(all workers)`: every worker Ready, fenced under its current epoch and lease advanced. A pool where only the GPU or only the desktop is open admits nothing protected.
2. **No suspend under need.** A worker is never fenced-for-suspend or suspended while any route whose required set contains it has demand, in-flight requests, a pending wake or a reservation. Sandbox/Coder work contributes demand to every worker.
3. **Order of checks is unchanged.** Under the activity lock: claim interlock, transition, `verify_admission`, recheck `any_workload`/active requests, only then power calls; a request admitted between selection and fencing aborts the suspend (existing final recheck, per worker).
4. **Retire after any attempt.** After fence/suspend calls (including failed or uncertain ones) the affected worker's suspend epoch is retired and its gate closed; reopening requires a new fence and a fresh lease. Other workers' gates are untouched in stage 2 and all are reset in stage 1.
5. **NotReady is unknown, not asleep.** A route requiring a NotReady worker stays closed and triggers a targeted wake; it never infers sleep.
6. **Interaction with degraded sleep.** `suspend-degraded-worker-pool` already lets each Ready worker sleep when the pool is idle and keeps release blocked until the full pool is open. This change generalizes "pool idle" to "worker idle" without relaxing release. Its requirement and tests stay valid; its all-workers idleness condition becomes the stage 1 behavior.
7. **Uncertain gate or probe errors fail closed per worker:** an error determining readiness, lease or interlock state closes the affected gates (as `tick()` does today for the whole pool). An error for one worker's probe must not open or close an unrelated worker's gate except via the shared interlock/API errors that already close everything.

### 6. Config shape and validation

```yaml
backends:
  - name: ollama
    workers: [homelab-gpu]        # optional; default = all pool workers
nodeGroups:
  - name: desktop
    sandboxPool: { backends: [ollama], workers: [...] }
    backends:
      - hostname: vinyl.home.timosur.com
        workers: [homelab-amd-desktop]   # optional; default = all pool workers
```

`workers` entries are worker `node` names from `sandboxPool.workers`. Sandboxes and Coder are not configurable. Omitting the key means all workers, preserving current behavior, so the ConfigMap change can ship after (or with) the code.

Validation (fail at startup):

- `config.py`: parse optional `workers` on `BackendConfig` and `NodeGroupBackend` as a list of non-empty distinct strings, otherwise `ValueError`.
- `server.py` (cross-config, where pool backends are matched): for a backend named in `sandboxPool.backends`, each name must exist in the pool's workers; the worker whose `suspend_host`/`wol_mac` matches the backend's `targetHost`/`wolMac` (the existing "same sandbox worker" check) must be in the set, since the route cannot be served without its own host. `workers` on a backend that is not in any pool is rejected.
- `proxy.py` `NodeGroupProxy.__init__`: each `NodeGroupBackend.workers` must be a non-empty subset of `sandbox_pool.workers`; a node group without a pool rejects `workers`. Resolve defaults to the full worker tuple and store the resolved frozenset on the backend object so request paths use one lookup.

### 7. Status and wake page

`/wol-proxy/status` for a node group reports state for the requested host's required set, not the whole pool. `render_wake_page` and 503 messages are unchanged. Ollama cached-path serving (`/api/tags` etc.) while closed is unchanged.

## Risks / Trade-offs

- [Global-epoch retirement closes an awake peer's gate] -> stage 1 keeps pool-wide sleep so this cannot happen; stage 2 introduces per-worker epochs only with approval and migration tests.
- [A route is served while a different worker is half-woken] -> allowed by design for non-protected routes (they only need their worker); protected work still needs all workers open (Decision 5.1).
- [More concurrent wake/fence paths] -> per-host rate limits and per-worker gate state; tests cover interleavings (GPU wakes during desktop fence, etc.).
- [Per-worker idle keeps one host awake longer than before if the other is busy] -> acceptable; the change is power-saving in the dominant cases (single-node routes).
- [Interlock schema change] -> staged, versioned record with backward-compatible read and tested rollback; do not edit the live ConfigMap by hand.
- [Misconfigured `workers:` makes a route skip its own host] -> startup validation requires a backend's own worker in its set.

## Migration Plan

1. Land stage 1 code and tests with `workers` defaulting to all (no behavior change); deploy; then add `workers:` to `apps/wol-proxy/configmap.yaml` and verify wake targeting (GPU-only Ollama wake, desktop-only vinyl wake) in a controlled window, watching that no sandbox is released while a worker is down.
2. After approval, land stage 2 separately with the interlock migration and verify per-worker idle sleep.
3. Roll back by reverting the image; stage 1 keeps the existing interlock format. Stage 2 rollback requires the old reader to accept the migrated record or a documented manual reset by an operator (not a routine step).
4. Do not run live cluster actions as part of authoring this change.

## Open Questions

1. **Interlock/host-guard contract (stage 2).** Is per-worker suspend epochs, with the `workers` map in the interlock ConfigMap and a 4-field request to host guards, approved? Alternatives: per-worker interlock ConfigMaps; or stop at stage 1 and keep pool-wide sleep. Needs a human decision before stage 2.
2. **Coder "any one worker".** Revisit only if wanted; needs a mechanism pinning the pod to an open node (affinity rewrite at release or a reservation of node) and a rule preventing that node's sleep. Not in scope here.
3. **Refining protected-work pinning.** Once a sandbox pod is bound to a node, could its idle peer sleep? Currently no (conservative; a node's own sandbox and Coder pods are also tracked cluster-wide by `any_workload()`).
4. **Per-route idle timeouts.** Ollama uses 5 minutes and the desktop group 5 minutes today; with per-worker timers should each route keep its own `idleTimeoutMinutes` (worker takes the max over routes requiring it) or should there be a single worker-level value? Proposed: max over routes.
5. **Should `/wol-proxy/status` expose per-worker gate states** for debugging, and is that endpoint unauthenticated-safe to extend?
6. **Wake timeout semantics** when one required worker wakes and another does not: fail the route after `wakeTimeoutSeconds`, or keep waiting? Proposed: fail per route, keep waking in the background rate-limited.
