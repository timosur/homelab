# Spec Delta

## ADDED Requirements

### Requirement: Per-route required worker sets
Each `wol-proxy` route SHALL have a required worker set: the GPU worker for Ollama, the desktop worker for desktop node-group routes, and every pool worker for Dark Factory sandbox and Coder workspace starts. A route MAY narrow its set with a `workers` list of pool worker names, defaulting to all pool workers.

#### Scenario: Default required set
- **WHEN** a backend omits `workers`
- **THEN** its required set is every worker in the sandbox pool

#### Scenario: Fixed sets
- **WHEN** no `workers` override applies
- **THEN** Ollama requires only the GPU worker, desktop routes only the desktop worker, and sandbox and Coder starts every worker

#### Scenario: Invalid worker set
- **WHEN** a route's `workers` list names a worker not in the pool, is empty, contains duplicates, or excludes the worker that hosts its own target
- **THEN** the proxy refuses to start with a configuration error

#### Scenario: Workers set on a route outside a pool
- **WHEN** a backend or node-group backend that is not part of a sandbox pool declares `workers`
- **THEN** the proxy refuses to start with a configuration error

### Requirement: Per-worker admission gate
The proxy SHALL track, for each pool worker, whether it is Ready, fenced under its current epoch, and has renewed its lease since the fence. A route SHALL be admitted only when every worker in its required set is open under that definition, independent of the state of workers outside the set. Errors determining worker state SHALL fail closed for the affected route.

#### Scenario: Ollama with the desktop unavailable
- **WHEN** the GPU worker is Ready, fenced and freshly leased and the desktop worker is asleep
- **THEN** an Ollama request is proxied and the desktop is not woken

#### Scenario: Desktop route with the GPU asleep
- **WHEN** the desktop worker is open and the GPU worker is asleep
- **THEN** a request for a desktop route is proxied and the GPU is not woken

#### Scenario: Required worker not yet open
- **WHEN** a route's required worker is Ready but not yet fenced or its lease has not advanced
- **THEN** the route is refused with a retryable response and no packet is sent to that worker

#### Scenario: Unknown worker state
- **WHEN** readiness, lease or interlock state for a required worker cannot be determined
- **THEN** the route remains closed and the proxy does not infer that the worker is asleep or safe

### Requirement: Whole-pool gate for protected work
Dark Factory sandbox gate release, workspace admission and Coder workspace start SHALL require every pool worker to be Ready, fenced under its current epoch and freshly leased. A pool in which only some workers are open SHALL NOT admit or release any protected workload, regardless of which routes are currently being served.

#### Scenario: Partially awake pool
- **WHEN** a gated sandbox or Coder pod exists, the desktop worker is open and the GPU worker is not
- **THEN** the pod's scheduling gate is not removed and the GPU worker is woken

#### Scenario: All workers open
- **WHEN** every worker is Ready, fenced under its current epoch and freshly leased
- **THEN** pending protected workloads are released using the existing interlock rules

#### Scenario: Peer worker closes after open
- **WHEN** a worker closes (NotReady, stale lease or retired epoch) before a gate PATCH
- **THEN** the release is not issued and is retried after the pool is fully open again

### Requirement: Targeted wake
A wake request SHALL carry the route's required worker set. The proxy SHALL send a wake packet only to workers in that set that are not Ready, and SHALL NOT send a packet to a worker that is Ready or outside the set. A wake SHALL be rate limited to once per 15 seconds per host. Failure to wake one worker SHALL keep only the routes that require that worker closed.

#### Scenario: Ollama wake
- **WHEN** an Ollama request arrives while the GPU worker is NotReady and the desktop worker is Ready or asleep
- **THEN** only the GPU worker receives a wake packet

#### Scenario: Desktop route wake
- **WHEN** a desktop route request arrives while the desktop worker is NotReady
- **THEN** only the desktop worker receives a wake packet

#### Scenario: Sandbox wake
- **WHEN** a gated sandbox needs a wake while some workers are NotReady
- **THEN** every NotReady worker receives a wake packet and workers already Ready do not

#### Scenario: Wake rate limit per host
- **WHEN** repeated demand arrives within 15 seconds for the same worker
- **THEN** at most one packet is sent to that worker in that window while a wake for a different worker is not suppressed

### Requirement: Per-worker idle sleep
A worker SHALL be suspended only through the host power guard and only when no route requiring it has demand, in-flight requests, a pending wake or a reservation. Protected sandbox and Coder work SHALL count as demand on every worker. Eligibility SHALL be re-checked under the activity lock before power requests, and only the affected worker's suspend epoch SHALL be retired afterwards.

#### Scenario: Single-route idle
- **WHEN** only Ollama has been used, the idle timeout has elapsed, and no protected work exists
- **THEN** the GPU worker may be suspended and the desktop worker is not suspended solely because of that

#### Scenario: Demand on a required worker
- **WHEN** a request that requires a worker is in flight, or a wake for it is pending
- **THEN** that worker is not suspended

#### Scenario: Protected work pins all workers
- **WHEN** a sandbox workload, gated sandbox, or Coder start reservation exists
- **THEN** no worker is suspended

#### Scenario: Demand arrives during suspend preparation
- **WHEN** a request requiring the worker is admitted after eligibility is determined but before the suspend request is sent
- **THEN** the suspend is aborted and the worker remains open

#### Scenario: Uncertain suspend
- **WHEN** a worker's fence or suspend outcome is unknown
- **THEN** that worker stays closed until a new epoch fence and fresh lease succeed, and it is not treated as asleep

### Requirement: Compatibility with degraded-pool sleep
This capability SHALL preserve the `suspend-degraded-worker-pool` guarantees: a NotReady worker is never assumed asleep, an unresolved host guard blocks reopening, and an unavailable worker does not prevent an independent attempt on another eligible worker.

#### Scenario: One worker NotReady
- **WHEN** a worker is NotReady and the other worker is idle and eligible
- **THEN** the eligible worker may be suspended and the NotReady worker is not declared asleep, and protected admission stays blocked
