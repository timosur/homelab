# Design

## Context

Verified against the current tree:

- `proxy.py` `NodeGroupProxy._send_desktop_wake` sends a WoL packet to every `sandbox_pool.workers` entry via `pool.wake_all`; the same method is the `wake` callback of `SleepCoordinator`.
- `PoolMonitor.node_ready()` is `all(readiness())`. `SleepCoordinator._tick` keeps one `accepting` flag, one `fenced_epoch`, one `lease_revision` tuple and one `last_wake` timer. `accepting` opens only after every worker is fenced under `interlock.epoch` and every lease has advanced.
- Ollama (`ProxyBackend.handle_request`) and desktop routes (`NodeGroupProxy.handle_request`) both refuse traffic while `coordinator.accepting` is false and set the shared `proxy._wake_requested`. Desktop `check_node_health` returns `PoolMonitor.node_ready()` (all workers). Coder preflight (`coder_preflight.py`) waits for `accepting` and `nodes_schedulable()` (all workers).
- Idle sleep (`_sleep_pool`, from `suspend-degraded-worker-pool`) uses one shared idle timer and one interlock phase (`idle` -> `suspending`), fences then suspends each Ready worker, then retires the single suspend epoch. Retiring bumps the one global epoch, which invalidates *every* worker's fence. The phase stays `suspending` until the whole pool is fenced and leased (`coordinator.py` ~145-150); until then `Interlock.http_change` (`interlock.py:141`) and `_tick` (`coordinator.py:159-160`) raise, and `tick()` resets the gate on any exception.
- `PoolPowerClient.call` fences all clients; `PoolMonitor.node_lease_revision()` returns a tuple for all workers and `leases_advanced` requires every entry to change.
- Interlock state (`interlock.py`) is one ConfigMap record: `epoch`, `owner`, `phase`, `http_active`, `coder_starts`, anchor.
- Dark Factory sandbox pods are scheduling-gated at admission (`admission.py`) before a node is chosen; the controller removes the gate only after the pool is fenced.

## Goals / Non-Goals

**Goals:** a consumer wakes and waits only for workers it needs; a worker unavailable to one route does not block unrelated routes; sandbox and Coder protection is no weaker than today.

**Non-Goals:** per-worker idle sleep (see Future work); knowing a sandbox's node before scheduling; letting Coder pick "any one worker"; changing the host guard protocol, the interlock record format or `wake-gpu-nodes.sh`; treating NotReady as asleep; any live rollout.

## Decisions

### 1. Per-worker gate, per-route required set

The coordinator keeps per-worker gate state: `fenced_epoch`, `lease_revision` (taken at fence) and `open` (Ready, fenced under the current epoch, lease advanced). It exposes `accepting_for(workers)` = all requested gates open. `accepting` stays as `accepting_for(all workers)` and keeps its meaning for sandboxes and Coder.

| Consumer | Required workers | Reason |
|---|---|---|
| Ollama (`ProxyBackend`) | `workers:` in config; deployed `{homelab-gpu}` | Fixed target (`targetHost` is the GPU). |
| Desktop node-group routes | `workers:` in config; deployed `{homelab-amd-desktop}` | Fixed target; backends run on that node. |
| Dark Factory sandboxes (gate release) | all workers, not configurable | Node unknown until scheduling, so every node must be safe before the gate is removed. |
| Coder workspace start (`/coder/preflight`, gate release) | all workers, not configurable | See below. |

The code default for an omitted `workers:` is all pool workers, so existing configs route as before. The per-route narrowing is therefore a *deployed configuration* choice (task 4.1), not a code default.

**Coder: all workers (decision).** The board-member template allows either node (`kubernetes.io/hostname In [homelab-amd-desktop, homelab-gpu]`) and the pod is protected by the same scheduling-gate/release mechanism as sandboxes. "Any one schedulable worker" would need an affinity rewrite at release time or a pinned-node reservation in the interlock, plus a rule keeping that node awake; both add a race and a new correctness surface for a modest saving on infrequent starts. Keeping all workers means a Coder pod is never released while a worker it may land on is asleep, unfenced or unleased. Revisit in a follow-up (open question 2).

### 2. Wake targeting

`SleepCoordinator._wake_if_needed(demand)` becomes `_wake_if_needed(workers)` and the `wake` callback takes a worker set: `NodeGroupProxy._send_desktop_wake(workers=None)`, where `None` means all workers (sandboxes, Coder, non-pool groups). The workers actually woken are those in the set that are not Ready per `PoolMonitor.readiness(workers)`. A Ready-but-unfenced worker is not woken, only fenced. If the readiness lookup raises, wake the whole *required* set (never workers outside it), still subject to the rate limit, rather than skipping the wake. `wake_all` is kept as the concurrent helper over the selected subset and propagates failures, but callers catch them per route: one worker's failed packet only keeps routes that require it closed.

The 15 s limit moves from the single pool `last_wake` to a per-host map in the coordinator, so a GPU wake does not suppress a desktop wake. `ProxyBackend.wake_and_wait` (`proxy.py:148-181`) is the uncoordinated path: it sends a packet only to its own host, is serialized by `_wake_lock`, and has no 15 s limit; it is not reached in coordinated mode, where the 503 path sets `_wake_requested` and the coordinator wakes. It is unchanged. `NodeGroupProxy.wake_and_wait` (used only without a coordinator) keeps waking the whole group and is unchanged apart from passing its worker set to `_send_desktop_wake`.

The shared WoL relay (`wolHost`/ssh key, the same `192.168.2.100` host for both workers) is a common-mode failure: if it is down, no worker can be woken and every route that needs a sleeping worker stays closed. Per-route scoping does not remove that dependency; it only stops an unrelated route from being affected by a *worker* failure.

### 3. Sleep stays pool-wide in this change

Idle sleep (`_sleep_pool`) keeps today's rules: one shared idle timer, all demand sources (any route's in-flight HTTP, `_wake_requested`, any protected workload or reservation) block it, and each Ready worker is fenced then suspended through the guard. Because demand on *any* route blocks sleep of *every* worker, no worker is ever suspended while a route that needs it has demand or in-flight requests. After the attempt the single epoch is retired and every worker gate is closed. Per-worker sleep is deferred (Future work).

### 4. `suspending` -> `idle` after a pool sleep (needed to serve Ollama)

Today the interlock stays `suspending` after a pool or degraded sleep until the whole pool opens, and while `suspending` both `http_change` and `_tick` raise. If the desktop is down, the GPU could be fenced and leased but Ollama could never be served. New rule: when the first worker gate opens (Ready, fenced under the current epoch, lease advanced) and `needs_retire` is false, the coordinator performs `suspending` -> `idle` under the activity lock, and per-worker gates keep tracking the rest.

Why this is safe. The `suspending` phase protects against two things:

1. *A stale suspend still in flight for a worker.* The suspend epoch was retired (`retire_suspend_epoch`) after the attempt. A gate only opens after that worker was fenced under the new epoch and its lease advanced, and the host guard rejects any request carrying an older epoch. A worker whose gate is closed (still unfenced) may still receive a stale suspend, but that harms only that worker's own routes, which remain closed.
2. *Releasing protected work onto a pool that might sleep.* Sandbox gate release (`idle` -> `releasing`), `can_release*`, and Coder reservations (`/coder/preflight`) are reachable only through `accepting_for(all workers)` (new explicit check in `_tick` and `coder_preflight.py`), never through the interlock phase alone. They therefore stay blocked until every worker is open, which is exactly the condition that previously moved the phase to `idle`.

The sleep path itself is unchanged: `_sleep_pool` accepts `phase == idle` (starting a new sleep) or `suspending` (resuming one). A restart claims a new epoch, closes all gates in memory, and re-runs the same open/transition sequence.

### 5. Gate close and failure isolation

All writes of `accepting = False` become calls to `coordinator.close(workers)` / `close_all()` (`proxy.py:298,315,689,706`, `coder_preflight.py:50`, and the coordinator's own resets). `tick()`'s blanket reset is narrowed:

- *Worker-local errors* (readiness or lease lookup for one worker, one worker's fence failing with `PowerClientError`) close only that worker's gate.
- *Shared errors* (`verify_admission`, interlock claim/transition, `any_workload`, `coder_pods`, lease/phase inconsistencies and any unknown exception) close all gates, as today.

This needs: a per-worker fence on `PoolPowerClient` (`fence(workers, epoch, owner, anchor)`), `PoolMonitor.node_lease_revision(workers)` returning a node -> revision map, and a per-worker `leases_advanced`.

### 6. Sandbox admission safety (invariants)

1. **No partial admission.** Sandbox gate release, `/coder/preflight` approval and `can_release*` require `accepting_for(all workers)`, regardless of interlock phase. A pool with only some workers open admits nothing protected.
2. **No suspend under need.** No worker is fenced-for-suspend or suspended while any route has demand, in-flight requests, a pending wake or a reservation (pool-wide idle only).
3. **Order of checks is unchanged.** Under the activity lock: claim interlock, transition, `verify_admission`, recheck `any_workload`/active requests, only then power calls.
4. **Retire after any attempt.** The epoch is retired after fence/suspend calls, including failed or uncertain ones, and all gates close; reopening requires a new fence and a fresh lease per worker.
5. **NotReady is unknown, not asleep.** A route requiring a NotReady worker stays closed and triggers a targeted wake.
6. **Degraded sleep.** `suspend-degraded-worker-pool` stays intact; its release rule becomes invariant 1 and its idle rule is invariant 2.

### 7. Config shape and validation

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

`workers` entries are worker `node` names. Sandboxes and Coder are not configurable.

- `config.py`: parse optional `workers` on `BackendConfig` and `NodeGroupBackend` as non-empty, distinct strings.
- `server.py`: for port backends named in `sandboxPool.backends`, each name must be a pool worker, and the worker whose `suspend_host`/`wol_mac` matches the backend's `targetHost`/`wolMac` must be in the set (a port backend's own host is known). Node-group backends target Service DNS names, so no self-inclusion check applies to them. `workers` on a port backend outside every pool is rejected.
- `proxy.py` `NodeGroupProxy.__init__`: each `NodeGroupBackend.workers` must be a non-empty subset of `sandbox_pool.workers`; a group without a pool rejects it. Resolve defaults to a frozenset on the backend.
- *Interlock disabled:* `workers:` on any backend in a pool whose `sandboxPowerInterlockEnabled` is false is **rejected**. There is no coordinator to honor it, and the uncoordinated Ollama path wakes only its own host, so silently ignoring the list would mislead operators. Staged configs simply omit `workers:` until the interlock is on.

### 8. Status and wake page

`/wol-proxy/status` for a node group reports state for the requested host's required set, not the whole pool. The wake page and 503 messages are unchanged. Cached Ollama paths while closed are unchanged.

## Risks / Trade-offs

- [Early `suspending` -> `idle` weakens the phase as a barrier] -> release and Coder reservation require the explicit all-workers gate; tests assert no release attempt and no reservation in the partial state.
- [Narrowed `tick()` reset leaves a gate open on a mis-classified error] -> classify conservatively: unknown exceptions are shared and close everything.
- [Per-worker gates introduce interleavings (GPU fenced while the desktop wakes)] -> targeted tests with both worker orders.
- [Shared WoL relay is a common-mode failure] -> documented; out of scope.
- [Ollama or desktop traffic keeps the whole pool awake] -> accepted until per-worker sleep exists.
- [A misconfigured `workers:` makes a port backend skip its own host] -> startup validation.

## Migration Plan

1. Land code and tests with `workers` defaulting to all workers: routing is unchanged until `workers:` is set, but the `suspending` -> `idle` transition and per-worker gate isolation apply immediately, so verify those in tests first.
2. Deploy, then add `workers:` to `apps/wol-proxy/configmap.yaml` and verify in a controlled window: Ollama wakes only the GPU, vinyl only the desktop, a sandbox stays gated while a worker is down.
3. Roll back by reverting the image and removing `workers:`; the interlock format is unchanged. No live actions are part of authoring this change.

## Future work / follow-up change

Per-worker idle sleep (a GPU used only by Ollama sleeping independently of the desktop) is **not** part of this change. It needs per-worker suspend epochs so that one worker's suspend does not invalidate an awake peer's fence, per-worker `http_active`, per-worker idle timers, a versioned interlock record with backward-compatible read and rollback, and a host-guard request contract change. That is persisted-state and safety-critical work requiring explicit approval and its own proposal.

## Open Questions

1. Approve a follow-up change for per-worker idle sleep and its interlock/host-guard contract?
2. Coder "any one worker": revisit only if wanted; needs a mechanism pinning the pod to an open node and a rule preventing that node's sleep.
3. Once a sandbox is bound to a node, could the peer idle? Currently no (pool-wide).
4. Should `/wol-proxy/status` expose per-worker gate states, and is extending that endpoint safe?
5. When only one of several required workers wakes before `wakeTimeoutSeconds`: fail the route (proposed) and keep waking in the background rate-limited?
