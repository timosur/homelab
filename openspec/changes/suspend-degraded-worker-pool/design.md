# Design

## Context

The coordinator currently requires every worker Ready before fencing or reaching the idle sleep path. Pool power calls also stop on the first failure. The common interlock serializes protected activity and gate release with suspension; host guards reject stale epochs and regard an unfinished suspend as unresolved.

## Goals / Non-Goals

**Goals:** Permit independent, guarded sleep attempts on eligible reachable workers while preserving the shared admission and activity safety boundary.

**Non-Goals:** Infer that a NotReady node is asleep; recover an unknown host, delete its state, or relax existing workspace-safety guarantees. This does not authorize a live rollout or manual suspension.

## Decisions

1. Separate readiness for workspace release from eligibility for idle suspension. Release still requires all workers Ready, freshly leased and fenced. For degraded idle sleep, determine worker readiness individually; never require a failed or unavailable peer to pass its fence before attempting an eligible Ready worker. A NotReady worker stays unknown and is not assumed safe to suspend. Alternative: remove the all-ready check without distinguishing release and sleep; rejected because it could release protected work onto an unsafe pool.
2. Keep the shared idle timer, workload and durable reservation checks, admission verification, activity lock and interlock transition, with a final workload/activity recheck before power requests. Fence each eligible worker with the current epoch before requesting its guarded suspend; do not send suspend if its fence fails. Continue independent attempts after a peer failure, tracking outcomes without asserting the entire pool slept. Alternative: bypass the host guard or directly invoke system suspend; rejected because delayed operations could race protected workloads.
3. Retire the shared suspend epoch after all attempted operations, including uncertain or partially failed operations, and retain fail-closed suspension/admission state. Before releasing any gate or admitting protected activity after recovery, require the existing new-epoch host fencing and fresh leases from all workers; an unresolved host guard or ambiguous state must remain blocked. Preserve restart and ownership-takeover safety, including in-flight and delayed SSH requests. Alternative: reopen admission immediately after partial success; rejected because a previously sent request might still execute.

## Risks / Trade-offs

- [Kubernetes NotReady does not prove a host is off, and a missing pod does not fence a surviving VM] → Treat the host as unknown; retain the documented node-failure limitations and never claim full-pool sleep.
- [Stale or partial suspend requests can arrive after new demand or restart] → Keep admission closed, retire epochs, and require each host guard to fence and report a resolved state before release.
- [Failure on one host might still abort work on another] → Exercise both worker orderings and independent per-host failure paths in regression tests.
- [The live installation has production workloads] → Validate safety races and restart behavior in tests first; conduct live verification only under an appropriate controlled window and do not reset interlock or guard state as routine recovery.

## Migration Plan

Build and validate the proxy, then deploy through the existing GitOps/image workflow when authorized. Verify degraded idle sleep, subsequent wake, protected workload behavior, and guard/lease recovery without forcing a host down or resetting state. Roll back the proxy image if validation fails; preserve durable interlock and guard state for operator investigation.
