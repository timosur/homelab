# Spec Delta

## ADDED Requirements

### Requirement: Idle sleep with an unavailable pool worker
When the pool is idle and power-safety checks pass, the system SHALL attempt to suspend each reachable eligible worker even if another worker is NotReady or unreachable. It SHALL keep workspace admission closed until the complete pool is ready and safely fenced, and SHALL NOT treat unavailability as proof of suspension.

#### Scenario: Idle pool with one unavailable worker
- **WHEN** the idle timeout has elapsed, no protected workloads or requests exist, and one worker is unavailable
- **THEN** the system safely attempts to suspend the other reachable eligible worker without declaring the unavailable worker asleep

#### Scenario: Worker ordering or failure
- **WHEN** one worker is unreachable or its power operation fails during an otherwise safe idle sleep
- **THEN** that failure does not by itself prevent an independent safe attempt on another eligible worker

#### Scenario: Protected activity or uncertain safety state
- **WHEN** a protected workload, start reservation, proxied request, or unverified safety state prevents suspension
- **THEN** the system does not issue a suspend request that would bypass that protection

#### Scenario: Partial or uncertain suspend
- **WHEN** any worker's suspend outcome is uncertain or the pool is not fully ready and fenced
- **THEN** the system keeps workspace release blocked and prevents stale suspend operations from racing later protected activity

#### Scenario: Pool becomes available again
- **WHEN** all workers return and are verified ready and fenced under the current ownership epoch
- **THEN** the existing safe workspace release and idle sleep behavior can resume
