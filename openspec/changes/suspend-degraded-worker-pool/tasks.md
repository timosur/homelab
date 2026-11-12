# Tasks

## 1. Degraded pool sleep

- [x] 1.1 Implement per-worker eligibility and guarded suspension without weakening shared activity checks or all-worker gate release; verify both worker orderings and independent failures with coordinator and pool tests.
- [x] 1.2 Preserve fail-closed behavior across partial/uncertain operations, stale requests, restart, and new demand; verify these cases and protected workloads, HTTP, reservations, and recovery with regression tests.
- [x] 1.3 Update degraded-pool operator guidance and verify the full `wol-proxy` test suite and OpenSpec validation pass.
