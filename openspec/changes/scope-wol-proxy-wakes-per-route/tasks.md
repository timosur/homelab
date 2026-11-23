# Tasks

## 1. Configuration and validation

- [ ] 1.1 `config.py`: add optional `workers: list[str] | None` to `BackendConfig` and `NodeGroupBackend`; parse it in `load_config` as non-empty, distinct strings.
- [ ] 1.2 `server.py` (~lines 27-53): for port backends in `sandboxPool.backends`, validate `workers` against pool worker names and require the worker matching the backend's `targetHost`/`wolMac` to be included. Reject `workers` on a port backend outside every pool. Do not apply the self-inclusion check to node-group backends (Service DNS targets).
- [ ] 1.3 `proxy.py` `NodeGroupProxy.__init__` (~426-455): validate each `NodeGroupBackend.workers` as a non-empty subset of pool workers; reject for non-pool groups; reject `workers` on any backend when `sandboxPowerInterlockEnabled` is false; resolve defaults to a frozenset of all workers.
- [ ] 1.4 Tests: valid, default (all), unknown worker, duplicate, empty, port backend excluding its own host (rejected), node-group backend with a subset not containing any specific host (accepted), `workers` outside a pool (rejected), `workers` with the interlock disabled (rejected).

## 2. Gate state, close helpers and failure isolation

- [ ] 2.1 `pool.py`: `PoolMonitor.readiness(workers=None)`, `node_ready(workers=None)`, `nodes_schedulable(workers=None)`, and `node_lease_revision(workers=None)` returning a node -> revision mapping; keep all-workers defaults for existing callers. Update `test_pool.py`.
- [ ] 2.2 `pool.py`/`coordinator.py`: per-worker lease comparison (`leases_advanced` per worker) and `PoolPowerClient.fence(workers, epoch, owner, anchor)` that fences only the named workers (existing `call` keeps fencing all). Tests for both worker orders and a failed fence on one worker.
- [ ] 2.3 `coordinator.py`: replace `accepting`, `fenced_epoch`, `lease_revision` with per-worker gate state; add `accepting_for(workers)`; keep `accepting` as the all-workers view. Add `close(workers)` / `close_all()`.
- [ ] 2.4 Replace the scattered `accepting = False` writes with `close_all()`/`close(workers)`: `proxy.py:298,315,689,706`, `coder_preflight.py:50`, and the coordinator's own resets. Add a test helper (for example `open_gates(coordinator, *nodes)`) that opens specific gates, and use it in `test_coordinator.py`, `test_coordinated_backend.py`, `test_coder_preflight.py` and `test_pool_wiring.py`.
- [ ] 2.5 Narrow `tick()`'s blanket reset: worker-local errors (that worker's readiness/lease lookup, its fence failing) close only that worker's gate; shared errors (`verify_admission`, interlock, `any_workload`, `coder_pods`, unknown exceptions) call `close_all()`. Tests for each class, including an unknown exception closing everything.

## 3. `suspending` -> `idle` after pool or degraded sleep

- [ ] 3.1 `coordinator.py` `_tick`: when the first worker gate opens (Ready, fenced under the current epoch, lease advanced) and `needs_retire` is false, transition `suspending` -> `idle` under the activity lock. Keep `_sleep_pool` accepting both phases.
- [ ] 3.2 Make `release_gates`, Coder reservations and `can_release*` reachable only when `accepting_for(all workers)`, independent of phase.
- [ ] 3.3 Tests: Ollama served after a degraded sleep while the desktop is NotReady (`http_change` no longer raises); no gate release attempted and no Coder reservation made in that state; release happens only after all workers open; a restart during `suspending` re-fences and transitions correctly; a stale-epoch fence failure keeps the worker's gate closed (`test_coordinator.py`, `test_coordinated_backend.py`, `test_interlock.py`).

## 4. Targeted wake and route gating

- [ ] 4.1 `proxy.py`: `_send_desktop_wake(workers=None)` sends packets only to not-Ready workers in the set; a failed readiness lookup wakes the whole required set; per-host 15 s rate limit in the coordinator; `NodeGroupProxy.wake_and_wait`, `check_node_health` and `/wol-proxy/status` use the route's set; `_wake_requested` becomes a set of workers. `ProxyBackend.wake_and_wait` stays unchanged.
- [ ] 4.2 `ProxyBackend.handle_request` (Ollama) gates on `accepting_for(its set)`; `NodeGroupProxy.handle_request` gates on `accepting_for(backend set)`.
- [ ] 4.3 `coder_preflight.py:47`: `_wake_requested` becomes all workers, the approval check uses `accepting_for(all)`. Test that a Coder preflight with a partially open pool wakes every NotReady worker and makes no reservation (`test_coder_preflight.py`).
- [ ] 4.4 Tests: split `tests/test_pool_wiring.py:55-59` (`test_pool_wake_targets_both_hosts`) into all-workers, GPU-only, desktop-only and Ready-worker-skipped cases; rate limit per host; a failed packet for one worker leaves other routes served; `test_coordinated_backend.py` covers Ollama served with the desktop down and a desktop route served with the GPU down.

## 5. Configuration, docs and verification

- [ ] 5.1 `apps/wol-proxy/configmap.yaml`: add `workers: [homelab-gpu]` to the `ollama` backend and `workers: [homelab-amd-desktop]` to the desktop node-group backends, only after the code is deployed. Extend `tests/test_pool_wiring.py:99-121` (`test_enabled_gitops_pool_uses_the_power_coordinator`) to assert the resolved sets from the real manifest.
- [ ] 5.2 `docs/dark-factory-onboarding.md`:
  - line 21: replace "deliberately commented out until the checks below pass" (the settings are enabled);
  - lines 23-27: remove "the two-worker proxy configuration remains commented out" and fix the Ollama sentence (the coordinator sleeps the GPU, not an independent proxy);
  - lines 28-31: replace the "one conservative pool" paragraph with the per-route model: sandboxes and Coder need all workers fenced and freshly leased; Ollama and desktop routes need only their worker; one route no longer wakes or waits for the other's worker; idle sleep remains pool-wide;
  - line 37: HTTP activity is no longer blocked on all workers (only new workspace/sandbox activity is); update the degraded-pool paragraph accordingly.
- [ ] 5.3 Run the full `services/wol-proxy` test suite and `openspec validate scope-wol-proxy-wakes-per-route --strict`.
- [ ] 5.4 Controlled-window verification (no routine interlock or guard resets): GPU-only wake from Ollama, desktop-only wake from vinyl, Ollama served after a degraded sleep with the desktop down, a sandbox stays gated while a worker is down.
- [ ] 5.5 After the implementation is merged and `suspend-degraded-worker-pool` is archived, archive this change. Every requirement here is delivered by sections 1-5; per-worker idle sleep is a separate follow-up proposal and must not be archived into the spec here.
