# Proposal

## Why

`wol-proxy` treats the two sandbox workers (`homelab-amd-desktop`, `homelab-gpu`) as one conservative pool for every consumer. A single shared gate (`SleepCoordinator.accepting`) opens only when every worker is Ready, fenced under the current epoch and has a fresh lease, and every wake (`NodeGroupProxy._send_desktop_wake` -> `pool.wake_all`) sends a packet to every worker. That is required for Dark Factory sandboxes, whose target node is not known at wake time. It is wrong for consumers with a fixed node:

- An Open WebUI or agentgateway request to Ollama only needs `homelab-gpu`, but also wakes the desktop and is refused (503 "Backend is starting") until the desktop is fenced and leased too. A desktop that fails to wake blocks Ollama.
- A vinyl or paperless request only needs `homelab-amd-desktop`, but wakes the GPU (power cost) and is held on the wake page until the GPU is also ready.
- A single unavailable worker takes out every route, including routes that never touch it.

## What Changes

- Replace the single pool gate with per-worker gate state (Ready, fenced under the current epoch, lease advanced since the fence). A route is admitted when every worker in its *required worker set* is open.
- Each route declares its required set: Ollama -> `{homelab-gpu}`; desktop node-group routes -> `{homelab-amd-desktop}`; Dark Factory sandboxes and Coder workspace start -> all workers. Configuration gains an optional `workers:` list on backends and node-group backends (default: all pool workers, so existing configs behave as today).
- Wake requests carry the required set. A magic packet is sent only to workers in that set which are not Ready, keeping the existing 15 s per-host rate limit.
- Activity, in-flight requests and wake demand are tracked per worker. A worker is suspended only when no route that requires it has demand, in-flight requests or reservations. Sandbox and Coder activity pins every worker.
- Sandbox admission and gate release are unchanged in strength: they still require all workers open. A partially awake pool never admits sandboxes.
- Interlock state moves from one pool epoch/phase to a per-worker suspend epoch plus the existing controller ownership epoch, so that suspending one worker does not invalidate the fence on an awake peer (staged; see design).

## Capabilities

### New Capabilities

- `wol-proxy-power`: per-route worker requirements, per-worker gating, targeted wake and per-worker idle sleep for `wol-proxy`.

### Modified Capabilities

None. The `suspend-degraded-worker-pool` change adds its requirement to `coder-dev-workspaces`; this change builds on it, leaves it intact, and should be archived after it.

## Impact

- `services/wol-proxy`: `config.py`, `server.py`, `proxy.py`, `pool.py`, `coordinator.py`, `coder_preflight.py`, `interlock.py` (stage 2), `power_client.py` call sites, and their tests.
- `apps/wol-proxy/configmap.yaml` gains `workers:` entries (a separate implementation step).
- `docs/dark-factory-onboarding.md` (pool invariant and stale "commented out" text).
- No change to the Ansible `wake-gpu-nodes.sh` role, the host power guard protocol, or Coder's node affinity. Live behavior changes only after the new image is built and deployed; this change is documentation only.
