# Tasks

## 1. Configuration and validation (stage 1)

- [ ] 1.1 `config.py`: add optional `workers: list[str] | None` to `BackendConfig` and `NodeGroupBackend`; parse `workers` (`load_config`) as non-empty distinct strings.
- [ ] 1.2 `server.py` (~lines 27-53): validate backend `workers` against the pool's worker names, require the backend's own worker (matching target host/MAC) to be included, reject `workers` on backends outside a pool.
- [ ] 1.3 `proxy.py` `NodeGroupProxy.__init__` (~426-455): validate each `NodeGroupBackend.workers` as a non-empty subset of pool workers; reject for non-pool groups; resolve defaults to the full tuple.
- [ ] 1.4 Tests: extend config/server/pool-wiring tests for valid, default, unknown, duplicate, empty, self-excluding and no-pool cases.

## 2. Per-worker gate and targeted wake (stage 1)

- [ ] 2.1 `pool.py`: add `node_ready(workers=None)`/`readiness(workers)` and `nodes_schedulable(workers)`; keep the all-workers default for existing callers. Update `test_pool.py`.
- [ ] 2.2 `coordinator.py`: replace `accepting`, `fenced_epoch`, `lease_revision` by per-worker gate state; add `accepting_for(workers)`; keep `accepting` as the all-workers view; make `last_wake` per host; make `_wake_if_needed` take the required set. Update `test_coordinator.py` (gate opens per worker; all-workers gate still needs every worker; failed fence on one worker does not close an unrelated open worker; lease advance per worker).
- [ ] 2.3 `proxy.py`: `_send_desktop_wake(workers=None)` sends packets only to not-Ready workers in the set; `wake_and_wait`/`check_node_health`/`/wol-proxy/status` use the route's required set; `_wake_requested` becomes a worker set. Update `tests/test_pool_wiring.py` (`test_pool_wake_targets_both_hosts` at lines 55-59 must be split: all-workers wake still targets both; GPU-only and desktop-only wakes target one host; Ready workers get no packet).
- [ ] 2.4 `ProxyBackend.handle_request` (Ollama) gate on `accepting_for(GPU)`; desktop `NodeGroupProxy.handle_request` gate on `accepting_for(route set)`. Update `test_coordinated_backend.py` (Ollama served with the desktop down; desktop route served with the GPU down; both closed paths still wake only their set).
- [ ] 2.5 `coder_preflight.py` and gate release keep `accepting_for(all)`; add tests that a partially open pool never reserves a Coder start or releases sandbox/Coder gates (`test_coder_preflight.py`, `test_workspace_gate.py`/`test_sandboxes.py` as applicable).
- [ ] 2.6 Regression test for wake rate limit per host and failure of one wake packet not raising into other routes.

## 3. Per-worker idle sleep (stage 2; blocked on open question 1)

- [ ] 3.1 Get approval of the interlock/host-guard contract change; then design the versioned interlock record with a backward-compatible read and a rollback path.
- [ ] 3.2 `interlock.py`: per-worker suspend epoch, phase and `http_active`; keep ownership epoch/anchor semantics; migration and takeover tests (`test_interlock.py`).
- [ ] 3.3 `power_client.py`/`power_guard.py` call sites: pass per-worker epoch; guard rejects stale epoch per worker; update `test_power_client.py`, `test_power_guard.py`.
- [ ] 3.4 `coordinator.py` `_sleep_pool`: per-worker eligibility (no demand, no in-flight, no pending wake, no protected work, own idle timer); recheck under activity lock; retire only the affected worker's epoch. Tests: Ollama-only idle sleeps GPU while desktop stays open; sandbox or Coder activity pins all; demand arriving during preparation aborts; uncertain suspend keeps only that worker closed; both worker orders (`test_coordinator.py`, `test_pool.py`).
- [ ] 3.5 Per-worker in-flight accounting in `ProxyBackend`/`NodeGroupProxy` request handlers; tests that http_active counts only the route's required workers.

## 4. Config, docs and verification

- [ ] 4.1 `apps/wol-proxy/configmap.yaml`: add `workers: [homelab-gpu]` to the `ollama` backend and `workers: [homelab-amd-desktop]` to the desktop routes, after the code is deployed.
- [ ] 4.2 `docs/dark-factory-onboarding.md`: fix the stale sentence at ~line 21 that the settings in `apps/wol-proxy/configmap.yaml` are "deliberately commented out" (they are enabled) and the ~line 28 "two-worker proxy configuration remains commented out" / "Ollama proxy can suspend the GPU independently" statements; replace the "one conservative pool" paragraph (~lines 21-58) with the per-route model: sandboxes and Coder still need all workers fenced and freshly leased, other routes need only their worker, Ollama no longer wakes the desktop and vice versa; keep the degraded-pool paragraph and add that idle sleep is per worker only when no route requiring it has demand.
- [ ] 4.3 Run the full `services/wol-proxy` test suite, linters and `openspec validate scope-wol-proxy-wakes-per-route --strict`.
- [ ] 4.4 Controlled-window verification plan (no routine state resets): GPU-only wake from Ollama, desktop-only wake from vinyl, sandbox blocked while one worker is down, per-worker idle sleep (stage 2).
- [ ] 4.5 After `suspend-degraded-worker-pool` is archived, archive this change.
