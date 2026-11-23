# Spec Delta

## ADDED Requirements

### Requirement: Per-route required worker sets
Each `wol-proxy` route SHALL have a required worker set. Dark Factory sandbox and Coder workspace starts SHALL always require every pool worker. A port backend or node-group backend MAY narrow its set with a `workers` list of pool worker names; when omitted the set SHALL be every pool worker. The deployed configuration SHALL set the Ollama backend to the GPU worker and the desktop node-group routes to the desktop worker.

#### Scenario: Default required set
- **WHEN** a backend omits `workers`
- **THEN** its required set is every worker in the sandbox pool

#### Scenario: Deployed sets
- **WHEN** the deployed configuration sets `workers` on the Ollama backend and the desktop routes
- **THEN** Ollama requires only the GPU worker and the desktop routes only the desktop worker, while sandbox and Coder starts still require every worker

#### Scenario: Invalid worker set
- **WHEN** a `workers` list names a worker not in the pool, is empty, or contains duplicates
- **THEN** the proxy refuses to start with a configuration error

#### Scenario: Port backend excludes its own host
- **WHEN** a port backend in `sandboxPool.backends` lists `workers` that omit the worker whose host and MAC match its target
- **THEN** the proxy refuses to start with a configuration error

#### Scenario: Workers without a coordinator
- **WHEN** a backend or node-group backend declares `workers` but it is not served by an enabled power-interlock pool
- **THEN** the proxy refuses to start with a configuration error

### Requirement: Per-worker admission gate
The proxy SHALL track, for each pool worker, whether it is Ready, fenced under the current epoch, and has renewed its lease since the fence. A route SHALL be admitted only when every worker in its required set is open, independent of workers outside the set. Errors local to one worker SHALL close only that worker's gate; errors in shared state (admission verification, interlock, workload checks) or unknown errors SHALL close every gate.

#### Scenario: Ollama with the desktop unavailable
- **WHEN** the GPU worker is open, the desktop worker is asleep, and no protected work or reservation exists
- **THEN** an Ollama request is proxied and the desktop is not woken

#### Scenario: Desktop route with the GPU asleep
- **WHEN** the desktop worker is open, the GPU worker is asleep, and no protected work or reservation exists
- **THEN** a request for a desktop route is proxied and the GPU is not woken

#### Scenario: Required worker not yet open
- **WHEN** a route's required worker is Ready but not yet fenced or its lease has not advanced
- **THEN** the route is refused with a retryable response and no packet is sent to that worker

#### Scenario: Unknown worker state
- **WHEN** readiness or lease state for a required worker cannot be determined
- **THEN** that worker's gate stays closed and the proxy does not infer that the worker is asleep or safe

#### Scenario: Shared-state error
- **WHEN** admission verification, interlock access or a workload check fails
- **THEN** every gate closes

#### Scenario: Serving after a degraded or pool sleep
- **WHEN** the pool slept (the interlock is still `suspending`), the GPU worker has since been fenced under the new epoch with a fresh lease, the desktop worker is NotReady, and no stale suspend is pending
- **THEN** the interlock returns to `idle`, Ollama requests are proxied, and no gate release or Coder reservation is attempted

### Requirement: Whole-pool gate for protected work
Dark Factory sandbox gate release, workspace admission and Coder workspace start SHALL require every pool worker to be open. A pool in which only some workers are open SHALL NOT admit or release any protected workload, whatever routes are being served or the interlock phase.

#### Scenario: Partially awake pool
- **WHEN** a gated sandbox or Coder pod exists, the desktop worker is open and the GPU worker is not
- **THEN** the pod's scheduling gate is not removed, no Coder reservation is made, and the GPU worker is woken

#### Scenario: All workers open
- **WHEN** every worker is open
- **THEN** pending protected workloads are released using the existing interlock rules

#### Scenario: Peer worker closes after open
- **WHEN** a worker closes (NotReady, stale lease or retired epoch) before a gate PATCH
- **THEN** the release is not issued and is retried after every worker is open again

### Requirement: Targeted wake
A wake request SHALL carry the route's required worker set. The proxy SHALL send a wake packet only to workers in that set that are not Ready, at most once per 15 seconds per host.

#### Scenario: Ollama wake
- **WHEN** an Ollama request arrives while the GPU worker is NotReady
- **THEN** only the GPU worker receives a wake packet

#### Scenario: Desktop route wake
- **WHEN** a desktop route request arrives while the desktop worker is NotReady
- **THEN** only the desktop worker receives a wake packet

#### Scenario: Sandbox or Coder wake
- **WHEN** protected work needs a wake while some workers are NotReady
- **THEN** every NotReady worker receives a wake packet and Ready workers do not

#### Scenario: Wake rate limit per host
- **WHEN** repeated demand arrives within 15 seconds for the same worker
- **THEN** at most one packet is sent to that worker in that window, and a wake for a different worker is not suppressed

#### Scenario: Readiness unreadable
- **WHEN** worker readiness cannot be read for a route requiring only the GPU worker
- **THEN** every worker in the required set (only the GPU worker) is woken, still rate limited

#### Scenario: One worker fails to wake
- **WHEN** a wake packet for one worker fails
- **THEN** only routes requiring that worker stay closed and other routes are unaffected

### Requirement: Sleep never under demand
Pool idle sleep SHALL remain gated on the whole pool being idle: no route has demand, in-flight requests or a pending wake, and no protected sandbox or Coder work or reservation exists. Demand on any route SHALL keep every Ready worker awake.

#### Scenario: Single-route demand
- **WHEN** only Ollama has an in-flight request or recent activity within the idle timeout
- **THEN** no worker is suspended

#### Scenario: Demand arrives during suspend preparation
- **WHEN** a request is admitted after idleness is determined but before suspend requests are sent
- **THEN** the suspend is aborted

### Requirement: Compatibility with degraded-pool sleep
This capability SHALL preserve the `suspend-degraded-worker-pool` guarantees: a NotReady worker is never assumed asleep, an unresolved host guard blocks reopening, protected admission requires every worker open, and an unavailable worker does not prevent an independent suspend attempt on another eligible worker.

#### Scenario: One worker NotReady
- **WHEN** a worker is NotReady and the pool is idle
- **THEN** the Ready worker may be suspended, the NotReady worker is not declared asleep, and protected admission stays blocked
