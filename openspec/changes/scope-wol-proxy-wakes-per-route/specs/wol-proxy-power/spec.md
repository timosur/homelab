# Spec Delta

## ADDED Requirements

### Requirement: Per-route required worker sets
HTTP routes (port backends and node-group backends) SHALL have a required worker set that defaults to every pool worker and MAY be narrowed with a `workers` list of pool worker names. The deployed configuration SHALL set the Ollama backend to the GPU worker and desktop node-group routes to the desktop worker. Protected work (Dark Factory sandboxes and Coder workspaces) SHALL instead require exactly one worker, chosen per workload.

#### Scenario: Default HTTP set
- **WHEN** a backend omits `workers`
- **THEN** its required set is every pool worker

#### Scenario: Deployed HTTP sets
- **WHEN** the deployed configuration sets `workers` on the Ollama backend and the desktop routes
- **THEN** Ollama requires only the GPU worker and the desktop routes only the desktop worker

#### Scenario: Invalid worker list
- **WHEN** a `workers` list names a worker not in the pool, is empty, or has duplicates
- **THEN** the proxy refuses to start with a configuration error

#### Scenario: Port backend excludes its own host
- **WHEN** a port backend in `sandboxPool.backends` lists `workers` that omit the worker whose host and MAC match its target
- **THEN** the proxy refuses to start with a configuration error

#### Scenario: Workers without a coordinator
- **WHEN** any backend declares `workers` but the power interlock is not enabled for its pool
- **THEN** the proxy refuses to start with a configuration error

### Requirement: Worker choice for protected work
The pool SHALL declare an ordered worker preference list, defaulting to desktop then GPU in the deployed configuration. For each new protected workload the system SHALL choose the first worker in preference order whose gate is open and that has capacity; if none is open it SHALL choose the first worker in preference order that has capacity, or the first worker in preference order if none has capacity. Workers absent from the list SHALL NOT receive protected work.

#### Scenario: Preferred worker open
- **WHEN** the desktop worker's gate is open and a sandbox is admitted
- **THEN** the sandbox is pinned to the desktop worker and the GPU worker is not woken

#### Scenario: Preferred worker asleep
- **WHEN** no worker gate is open and a sandbox is admitted
- **THEN** the sandbox is pinned to the first worker in preference order and only that worker is woken

#### Scenario: Preferred worker unavailable but peer open
- **WHEN** the desktop worker is NotReady and the GPU worker's gate is open
- **THEN** new protected work is pinned to the GPU worker

#### Scenario: Preferred worker at capacity
- **WHEN** the preferred worker has reached its configured capacity and the next worker has room
- **THEN** new protected work is pinned to the next worker in preference order

### Requirement: Pinning at admission
The sandbox mutating webhook SHALL, on pod creation, add the scheduling gate, record the chosen worker in a pod annotation and add a required node affinity to that worker. The validating webhook SHALL reject creation without a matching annotation and affinity, SHALL reject any change to the annotation or to the pinned node affinity, and SHALL reject any gate removal not performed by the controller. A protected pod without a worker annotation SHALL be treated as requiring every worker.

#### Scenario: Sandbox created
- **WHEN** a sandbox pod is created in the sandbox namespace
- **THEN** it is scheduling-gated, annotated with its chosen worker and constrained by required node affinity to that worker

#### Scenario: Existing affinity
- **WHEN** the pod already has node affinity terms
- **THEN** the worker constraint is added to every term so the pod can only schedule on the chosen worker

#### Scenario: Conflicting placement
- **WHEN** the pod's node selector or affinity cannot be satisfied together with the chosen worker
- **THEN** admission rejects the pod

#### Scenario: Tampering
- **WHEN** a non-controller attempts to change the worker annotation or affinity of a protected pod
- **THEN** admission rejects the change

### Requirement: Per-worker admission gate
The proxy SHALL track, for each pool worker, whether it is Ready, fenced under that worker's current epoch, and has renewed its lease since the fence. A route or workload SHALL be admitted only when every worker it requires is open, independent of other workers. Errors local to one worker SHALL close only that worker's gate; errors in shared state (admission verification, interlock, workload checks) or unknown errors SHALL close every gate.

#### Scenario: Ollama with the desktop unavailable
- **WHEN** the GPU worker's gate is open and the desktop worker is asleep
- **THEN** an Ollama request is proxied and the desktop is not woken

#### Scenario: Desktop route with the GPU asleep
- **WHEN** the desktop worker's gate is open and the GPU worker is asleep
- **THEN** a request for a desktop route is proxied and the GPU is not woken

#### Scenario: Required worker not yet open
- **WHEN** a required worker is Ready but not yet fenced or its lease has not advanced
- **THEN** the request is refused with a retryable response and no packet is sent to that worker

#### Scenario: Unknown worker state
- **WHEN** readiness or lease state for a required worker cannot be determined
- **THEN** that worker's gate stays closed and the proxy does not infer that it is asleep or safe

#### Scenario: Shared-state error
- **WHEN** admission verification, interlock access or a workload check fails
- **THEN** every gate closes

#### Scenario: Serving after a worker sleep
- **WHEN** a worker slept, its suspend epoch has been retired (`needs_retire` is false for it), and it has been fenced under the new epoch with a fresh lease while its peer is NotReady
- **THEN** that worker's interlock phase returns to `idle` and requests that require only it are proxied

### Requirement: Release only onto an open worker
The controller SHALL release a protected pod's scheduling gate only to the pod's pinned worker, and only when that worker's gate is open on readiness, epoch and lease state read in the same tick, under the activity lock, immediately before the release transition. The release token SHALL be bound to that worker and its current epoch. A pod pinned to a worker that is NotReady or closed SHALL stay gated and SHALL NOT be rescheduled to another worker.

#### Scenario: Target worker open
- **WHEN** the pinned worker is Ready, fenced under its current epoch and freshly leased
- **THEN** the gate is released with a token bound to that worker, regardless of the state of the peer worker

#### Scenario: Peer worker closed
- **WHEN** the pinned worker is open and the other worker is asleep
- **THEN** the pod is released and the other worker is not woken

#### Scenario: Worker closes before release
- **WHEN** the pinned worker's gate opens and then closes before the release tick
- **THEN** no release is attempted and the pod stays gated

#### Scenario: Pinned worker NotReady
- **WHEN** the pinned worker is NotReady
- **THEN** the pod stays gated, the pinned worker is woken (rate limited), and the pod is not moved to another worker

#### Scenario: Wrong-worker token
- **WHEN** a gate removal carries a token bound to a different worker or epoch
- **THEN** the validating webhook rejects it

### Requirement: Coder worker selection and pinning
Coder start preflight SHALL choose a worker using the pool preference list, wake and wait only for that worker, and persist the reservation bound to that worker. The Coder admission webhook SHALL pin the workspace pod to the reserved worker with a required node affinity and an annotation, and release SHALL be gated on that worker only. A workspace pod without a reservation SHALL remain gated.

#### Scenario: Coder start with the desktop open
- **WHEN** a Coder workspace starts and the desktop worker's gate is open
- **THEN** preflight reserves the desktop worker, the pod is pinned to it, and the GPU worker is not woken

#### Scenario: Coder start with all workers asleep
- **WHEN** no worker is open
- **THEN** preflight wakes only the first worker in preference order and waits for its gate

#### Scenario: Reservation holds the worker awake
- **WHEN** a Coder reservation exists for a worker
- **THEN** that worker is not suspended until the reservation is cleared after the pod is observed ungated

### Requirement: Targeted wake
A wake request SHALL carry the required worker set. The proxy SHALL send a wake packet only to workers in that set that are not Ready, at most once per 15 seconds per host. If readiness cannot be read it SHALL wake the required set, never workers outside it. A wake failure SHALL be logged per worker, SHALL still count toward that host's rate limit, and SHALL NOT close any gate.

#### Scenario: Ollama wake
- **WHEN** an Ollama request arrives while the GPU worker is NotReady
- **THEN** only the GPU worker receives a wake packet

#### Scenario: Protected work wake
- **WHEN** a gated pod is pinned to a NotReady worker
- **THEN** only that worker receives a wake packet

#### Scenario: Wake rate limit per host
- **WHEN** repeated demand arrives within 15 seconds for the same worker
- **THEN** at most one packet is sent to that worker, and a wake for a different worker is not suppressed

#### Scenario: Readiness unreadable
- **WHEN** worker readiness cannot be read for a route requiring only the GPU worker
- **THEN** only the GPU worker is woken, still rate limited

#### Scenario: One worker fails to wake
- **WHEN** a wake packet for one worker raises an error
- **THEN** the failure is logged, the rate limit is updated, and no gate closes, so other routes are unaffected

### Requirement: Per-worker idle sleep
A worker SHALL be suspended only through its host power guard, and only when it is Ready; it hosts or is reserved for no protected pod; no route that requires it has in-flight requests, durable HTTP activity or a pending wake; and its own idle timer has elapsed. A worker's suspension SHALL NOT close or fence its peer, and SHALL retire only that worker's epoch.

#### Scenario: Ollama idle with desktop busy
- **WHEN** a sandbox is pinned to the desktop and Ollama has been idle past its timeout
- **THEN** the GPU worker is suspended and the desktop keeps serving

#### Scenario: Worker hosts protected work
- **WHEN** a sandbox or Coder workspace is pinned to, running on, or reserved for a worker
- **THEN** that worker is not suspended

#### Scenario: Demand on a required worker
- **WHEN** a request that requires a worker is in flight, or a wake for it is pending
- **THEN** that worker is not suspended

#### Scenario: Demand arrives during suspend preparation
- **WHEN** a pod pinned to the worker or a request requiring it is admitted after eligibility is decided but before the suspend request
- **THEN** the suspend is aborted

#### Scenario: Unannotated protected pod
- **WHEN** a protected pod exists without a worker annotation
- **THEN** no worker is suspended

#### Scenario: Uncertain suspend
- **WHEN** a worker's fence or suspend outcome is unknown
- **THEN** only that worker stays closed until a new-epoch fence and fresh lease succeed, and it is not treated as asleep

### Requirement: Per-worker interlock state and host guards
The interlock record SHALL hold a suspend epoch, a phase (`idle`, `releasing` or `suspending`), an HTTP-activity count and a Coder-reservation set for each worker. A worker's epoch SHALL be strictly increasing per host and sent to that host's guard unchanged, so stale-epoch rejection stays per host. Release tokens SHALL be bound to the worker and its epoch. A record from the earlier single-epoch version SHALL be migrated conservatively.

#### Scenario: Stale suspend after a worker retires its epoch
- **WHEN** a delayed suspend carries a retired epoch for a worker
- **THEN** that host's guard rejects it and the peer worker is unaffected

#### Scenario: Migration from a single-epoch record
- **WHEN** the new version first claims a record that carries only a global epoch
- **THEN** each worker epoch becomes greater than the global epoch, every worker phase is non-`idle` until re-fenced, and existing Coder reservations without a worker pin every worker

#### Scenario: Legacy writer touched the record
- **WHEN** the record's global epoch differs from the last epoch written by this version
- **THEN** the record is re-migrated conservatively before any gate opens

#### Scenario: Uncertain release
- **WHEN** a gate release outcome is uncertain on a worker
- **THEN** takeover is blocked until an operator resolves it, as with the single-epoch record

### Requirement: Supersession of pool-wide degraded sleep
This capability SHALL supersede the pool-wide conditions of `suspend-degraded-worker-pool`. A NotReady worker SHALL never be assumed asleep, an unresolved host guard SHALL keep only that worker closed, an unavailable worker SHALL NOT prevent an independent attempt on another eligible worker, and protected work SHALL be admitted only onto its open pinned worker.

#### Scenario: One worker NotReady
- **WHEN** a worker is NotReady and its peer is idle and eligible
- **THEN** the peer may be suspended, the NotReady worker is not declared asleep, and protected work pinned to a NotReady worker stays gated

#### Scenario: Pinned worker recovers
- **WHEN** a worker with gated pinned pods returns and is open
- **THEN** its pods are released without waiting for the other worker
