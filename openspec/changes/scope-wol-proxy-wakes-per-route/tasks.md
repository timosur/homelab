# Tasks

## 1. Configuration and validation

- [ ] 1.1 `config.py`: optional `workers` on `BackendConfig` and `NodeGroupBackend`; `sandboxPool.preference` (distinct non-empty worker names, default worker order); per-worker `capacity` (positive int, optional).
- [ ] 1.2 `server.py:build_proxies` (~27-55): for port backends in `sandboxPool.backends` validate `workers` against pool workers and require the worker matching `targetHost`/`wolMac`; **reject `workers` on any port backend whose pool has `sandboxPowerInterlockEnabled` false** and on port backends outside every pool; validate `preference` subset of workers.
- [ ] 1.3 `proxy.py` `NodeGroupProxy.__init__` (~426-455): relax the `len(workers) < 2` check to `< 1` (single-worker pool allowed); validate node-group backend `workers` as a non-empty pool subset; reject them for groups without a pool or with the interlock disabled; resolve defaults to a frozenset.
- [ ] 1.4 Tests: valid, default, unknown/duplicate/empty worker, port backend excluding its own host (rejected), node-group backend subset (accepted), `workers` with the interlock disabled (rejected in `build_proxies` for port backends and in `NodeGroupProxy` for node-group backends), `preference` validation, `capacity` validation, single-worker pool.

## 2. Gate state, close helpers and failure classes

- [ ] 2.1 `pool.py`: `readiness(workers)`, `node_ready(workers)`, `nodes_schedulable(workers)` and a per-node `node_lease_revision(workers)` mapping; keep all-worker defaults. Update `test_pool.py`.
- [ ] 2.2 `coordinator.py`: per-worker gate state (`fenced_epoch`, `lease_revision`, `open`), `accepting_for(workers)`, `close(workers)`/`close_all()`, per-worker `leases_advanced`, per-host `last_wake`. Replace scattered `accepting = False` (`proxy.py:298,315,689,706`, `coder_preflight.py:50`, coordinator resets) with `close`/`close_all`. Add a test helper that opens specific gates. Update `test_coordinator.py`, `test_coordinated_backend.py`, `test_pool_wiring.py`.
- [ ] 2.3 `pool.py` `PoolPowerClient`: per-worker `fence(workers, epochs, owner, anchor)` and `suspend(worker, epoch, owner, anchor)` using each worker's own epoch; `call`/`suspend_ready` adapted. Tests: both worker orders, one worker's failure does not affect the other.
- [ ] 2.4 Failure classes in `tick()`: worker-local errors close only that worker's gate; shared errors (`verify_admission`, interlock, `workloads_by_worker`, `coder_pods`, unknown) call `close_all()`; **wake failures** (`wol.py` `RuntimeError`, `wake_all` re-raise) are caught per worker in `_wake_if_needed`, logged, update `last_wake[node]` and close no gate. Tests for each class including that a wake failure leaves gates open and the rate limit holds.

## 3. Interlock v2 and migration

- [ ] 3.1 `interlock.py`: record fields `version`, `v2_epoch`, `workers` (`epoch`, `phase`, `http_active`), `coder_workers`, plus legacy aggregate `phase`/`http_active`/`coder_starts`; per-worker `transition(node, expected, target)`, `retire_suspend_epoch(node)`, `http_change(workers, amount)` (raises only if a required worker is `suspending`), `coder_reservation(workspace_id, node, reserve)`; `claim()` still raises while any worker is `releasing` and while another owner's HTTP activity is nonzero.
- [ ] 3.2 Migration: detect `version` missing or `epoch != v2_epoch`; convert in one CAS PUT (every worker epoch = old epoch + 1, no open gates, Coder reservations -> `"*"`, `v2_epoch` set, legacy fields rewritten). Tests: fresh v1 record, v1 mid-`suspending`, v1 with reservations, v2 touched by a legacy writer (mismatch -> re-migrate), restart during `suspending`/`releasing`, ownership takeover (`test_interlock.py`).
- [ ] 3.3 Per-worker `suspending -> idle` (rework of the earlier M1): when a worker's gate opens and its `needs_retire` is false transition that worker only, independent of the peer. Tests: Ollama served after sleep with the desktop NotReady; no release or Coder reservation attempted for a worker that is not `idle`/open.
- [ ] 3.4 Verify `apps/wol-proxy/deployment.yaml` `strategy: Recreate` and one replica remain; document it as the rollout assumption.

## 4. Power guard (no wire change)

- [ ] 4.1 Confirm `power_guard.py`, `dark-factory-power-sudoers`, the sleep hook and `ansible/roles/dark-factory-power-guard` need no change; record the rationale in the PR.
- [ ] 4.2 `tests/test_power_guard.py`: independent epoch sequences per host; a migrated epoch (`old + 1`) accepted over a guard at `old`; retired per-worker epoch rejected on that host only; unresolved suspend blocks only that host's fence.
- [ ] 4.3 `tests/test_power_client.py`: per-worker epoch passed unchanged to each host.

## 5. Sandbox admission, choice and pinning

- [ ] 5.1 New `choose_worker` (preference, open gate, capacity, fallbacks) with an in-process view of gate state shared by webhook and coordinator; lock-serialized choices. Tests for every fallback in the spec (preferred open, asleep, peer open, capacity, none open).
- [ ] 5.2 `admission.py:76-94` mutate: annotation `dark-factory.home.timosur.com/worker` and required node affinity appended to every term; reject conflicting `nodeSelector`. Validate: annotation and affinity required on CREATE for protected pods, immutable on UPDATE, only the controller may remove the gate. Unannotated protected pods remain "all workers". Tests in `test_admission.py`, `test_sandboxes.py`, `test_workspace_admission.py`.
- [ ] 5.3 `sandboxes.py` `can_release`/`release_gates`: token bound to node and worker epoch; webhook callbacks stay phase/token checks; release iterates gated pods per worker. `workspace.py`: reservations record the pinned node instead of the whole pool in `possible_nodes`, keeping the "previous pod gone" rule across workers (tests in `test_workspace_reservations.py`, `test_workspace_gate.py`).
- [ ] 5.4 Optional `capacity` counting and tests, including the all-full fallback (queue, never reject).

## 6. Coder

- [ ] 6.1 `coder_preflight.py`: choose a worker, `_wake_requested` for that worker only (line ~47), wait on its gate and `nodes_schedulable({node})`, reserve `coder_workers[workspace_id] = node`, return the node. Tests in `test_coder_preflight.py` (desktop open, all asleep wakes only the preferred worker, peer NotReady, timeout, reservation holds the worker awake).
- [ ] 6.2 `coder_admission.py`: mutate pins the pod from the reservation (annotation plus affinity, no template change); validate annotation vs reservation, immutability, controller-only gate removal; missing reservation stays gated. Tests in `test_coder_admission.py`.
- [ ] 6.3 `release_coder_gates`: per-worker barrier and worker-bound token; `can_release_coder` checks the worker's phase and token.

## 7. Coordinator: release barrier, wake and per-worker sleep

- [ ] 7.1 `coordinator.py` `_tick`: immediately before the worker's `idle -> releasing` transition (currently `:166-168`), under the activity lock, recompute that worker's gate from state read in the same tick and release only if open. **Test: the worker's gate opens, then closes before the release tick; assert no release is attempted and the pod stays gated.** Also: peer asleep, pinned worker open -> released and the peer not woken.
- [ ] 7.2 Targeted wake: `_wake_if_needed(workers)`, `_send_desktop_wake(workers=None)` (only not-Ready workers in the set; readiness-read failure wakes the required set), a gated pod pinned to a NotReady worker wakes only that worker. Split `tests/test_pool_wiring.py:55-59` into per-set cases.
- [ ] 7.3 `SandboxMonitor`: `workloads_by_worker()` (annotated pods and reservations per node, unannotated or `"*"` -> all); replace `any_workload`/`has_gated_pods` call sites.
- [ ] 7.4 Per-worker sleep `_sleep_worker(W)`: per-worker eligibility, per-worker idle timers (max over routes), claim, `W: idle -> suspending`, `verify_admission`, recheck, fence, suspend, `retire_suspend_epoch(W)`, close only W's gate, per-worker 60 s retry. Per-worker `http_active` in `ProxyBackend.handle_request` and `NodeGroupProxy.handle_request`. Tests: GPU sleeps while a sandbox is pinned to the desktop; a pinned pod, reservation or unannotated pod blocks sleep; pod admitted during suspend preparation aborts; uncertain suspend keeps only that worker closed; both orders; degraded-pool cases from `suspend-degraded-worker-pool` still pass (`test_coordinator.py`, `test_pool.py`).
- [ ] 7.5 Pinned worker NotReady: pod stays gated and the worker is woken at the rate limit; no re-pin. Test.

## 8. Docs

- [ ] 8.1 `docs/dark-factory-onboarding.md`: line 21 ("deliberately commented out" is stale; the settings are enabled); lines 23-27 (the "two-worker proxy configuration remains commented out" and "Ollama proxy can suspend the GPU independently" statements); rewrite the "one conservative pool" paragraphs (lines 28-31 and the degraded-pool paragraph at 33-40 including line 37) for the per-worker model: protected work pinned to one worker by preference, per-worker gates, per-worker sleep, interlock v2 recovery notes.
- [ ] 8.2 Capacity: document `runs.maxConcurrent: 8` (`apps/dark-factory/values.yaml:72`), that one worker can take all 8, how `capacity` limits that, and the stale `defaultRequestMemory` comment ("spreads sandboxes across both workers").
- [ ] 8.3 State that `workers:` entries must be removed in the same change that sets `sandboxPowerInterlockEnabled: false`, otherwise wol-proxy refuses to start.
- [ ] 8.4 Operator note: pods pinned to a NotReady worker stay gated; recovery is waking the worker or recreating the pod, not editing the interlock.

## 9. Config, verification and archive

- [ ] 9.1 `apps/wol-proxy/configmap.yaml`: add `workers:` to `ollama` and desktop routes, `preference`, and `capacity` after the image is running. Extend `tests/test_pool_wiring.py:99-121` to assert resolved sets and preference from the real manifest.
- [ ] 9.2 Run the full `services/wol-proxy` test suite, linters and `openspec validate scope-wol-proxy-wakes-per-route --strict`. Controlled-window verification per the design's migration plan.
- [ ] 9.3 Archive order: archive `suspend-degraded-worker-pool` first, amend the `coder-dev-workspaces` requirement it adds ("keep workspace admission closed until the complete pool is ready") to the per-worker rule in the same step, then archive this change.
