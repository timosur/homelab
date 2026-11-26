# Spec Delta

## MODIFIED Requirements

### Requirement: Demand-woken workspace lifecycle
The system SHALL use `wol-proxy` to wake one worker, chosen by the pool preference order, before workspace provisioning, and SHALL pin the workspace pod to that worker. The start handoff SHALL prevent suspension of that worker until the workload is safely observed. Coder SHALL stop the workspace after 30 minutes without activity. `wol-proxy` SHALL suspend a worker only when no protected workload is pinned to or reserved for it and existing power-safety conditions permit it.

#### Scenario: Start while a pool worker is asleep
- **WHEN** the user starts the workspace while no worker is open
- **THEN** only the first worker in preference order is woken and observed Ready and schedulable before the workspace is allowed to run on it

#### Scenario: Start with a worker already open
- **WHEN** the user starts the workspace while the preferred worker is already open
- **THEN** the workspace is pinned to it and no other worker is woken

#### Scenario: Start races suspension
- **WHEN** workspace start is in progress or its outcome is uncertain
- **THEN** suspension of the reserved worker remains blocked until the handoff to an observed workload is safe or an operator resolves the uncertainty

#### Scenario: Workspace becomes inactive
- **WHEN** no Coder workspace activity is detected for 30 minutes
- **THEN** Coder stops the workspace and the persistent development home remains available for the next start

#### Scenario: Other protected workload remains active
- **WHEN** the Coder workspace has stopped but a protected workload is pinned to the same worker
- **THEN** `wol-proxy` does not suspend that worker
