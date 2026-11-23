# Proposal

## Why

`wol-proxy` treats the two sandbox workers (`homelab-amd-desktop`, `homelab-gpu`) as one conservative pool for every consumer. One shared gate (`SleepCoordinator.accepting`) opens only when every worker is Ready, fenced under the single interlock epoch and freshly leased; every wake sends a packet to every worker; and the whole pool sleeps or stays awake together. So:

- An Ollama request needs only `homelab-gpu` but also wakes the desktop and is refused until the desktop is fenced too.
- A vinyl request needs only `homelab-amd-desktop` but wakes the GPU.
- A Dark Factory sandbox or Coder workspace runs on one node but wakes and waits for both, and any worker being unavailable blocks them.
- One busy worker keeps its idle peer awake.

## What Changes

- Per-worker gate state (Ready, fenced under that worker's epoch, lease advanced). A route or workload is admitted when the workers it needs are open.
- HTTP routes keep a configurable required set (`workers:`, default all; deployed config sets Ollama to the GPU and the desktop routes to the desktop).
- **Protected work needs one worker.** An ordered pool preference list (desktop, then GPU) chooses it: use a preferred worker that is already open, else wake the first in preference order. The sandbox mutating webhook pins the pod to that worker (annotation plus required node affinity); Coder preflight chooses and reserves a worker and the Coder webhook pins the pod, with no template change. Release targets only that worker, on state read in the same tick, with a token bound to the worker.
- Wake requests carry the required set and send a packet only to not-Ready workers in it (15 s limit per host); wake failures close no gate.
- **Per-worker idle sleep:** a worker with no hosted or reserved protected work and no HTTP demand sleeps through its host guard while its peer keeps serving.
- The interlock record becomes per worker (epoch, phase, HTTP count, Coder reservations) with a conservative migration from the single-epoch record. The host-guard wire contract and sudoers are unchanged: each host already tracks its own epoch.
- Failure isolation: worker-local errors close only that worker; shared errors close all; a pod pinned to a NotReady worker stays gated.
- This supersedes the pool-wide conditions of `suspend-degraded-worker-pool`; the surviving guarantees are restated here.

## Capabilities

### New Capabilities

- `wol-proxy-power`: per-route and per-workload required workers, worker choice and pinning, per-worker gates, targeted wake, per-worker idle sleep, per-worker interlock state.

### Modified Capabilities

- `coder-dev-workspaces`: the demand-woken lifecycle wakes and pins one worker instead of the whole pool.

## Impact

- `services/wol-proxy`: `config.py`, `server.py`, `proxy.py`, `pool.py`, `coordinator.py`, `interlock.py`, `sandboxes.py`, `admission.py`, `coder_admission.py`, `coder_preflight.py`, `workspace.py`, `power_client.py` and their tests (including `test_power_guard.py`).
- `apps/wol-proxy/configmap.yaml` (workers, preference, capacity) and `docs/dark-factory-onboarding.md`.
- Behavior of sandbox capacity: one worker can now host all `runs.maxConcurrent` (8) sandboxes unless `capacity` is set.
- Unchanged: `power_guard.py`, its sudoers/Ansible role, `wake-gpu-nodes.sh`, Coder's template. Live behavior changes only after deployment; this change is documentation only. Open questions (capacity value, re-pin of gated pods, guard node binding, Coder template drift) are listed in `design.md`.
