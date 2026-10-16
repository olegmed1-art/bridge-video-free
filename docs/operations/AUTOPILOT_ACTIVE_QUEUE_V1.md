# Autopilot active queue and retained history

Change: 0403, 2026-10-16. Governance: ASSURED; production application requires
the coordinated database window. Source, CI and actual activation are separate
evidence stages.

## Working capacity

The canonical records remain in `autopilot.role_dispatch_outbox`. The active
view excludes only `CALLBACK_ACCEPTED` and `FAILED_CLOSED`. The archive view
contains those terminal states. `DELIVERY_FAILED`, retries, published commands
and waiting callbacks remain active until the existing protocol closes them.
A failed task result can have a successfully accepted callback; task success
is not the predicate for leaving the dispatch queue.

Archiving is a logical partition of the same records. A terminal commit makes
the record disappear from the active view immediately. There is no cleanup job,
copy/delete window, second receipt store or counter to repair after a crash.
Task IDs, dispatch IDs, receipt uniqueness, callback deduplication, evidence and
links remain intact. Terminal dispatches cannot return to active states or move
to another mailbox. Existing authenticated receipt replay remains valid.

The existing registry limit remains 40 active dispatches. At 32 the existing
health monitor warns; at 40 admission waits. The AFTER INSERT guard serializes
on the registry row, then counts active rows in a separate READ COMMITTED
statement. The next insertion rolls back. Conflict-skipped duplicates do not
run this guard. Other isolation levels fail closed because a waiting lock alone
cannot refresh a REPEATABLE READ snapshot.

The planner leaves registered work intact when full. Task claims preserve READY
state and attempt budgets until a slot is available. Concurrent claimants use
the existing capacity lock and count RUNNING tasks without an outbox as short
reservations. The separate six-worker/five-normal limit remains in force.
Supported runtime entry points are the existing registration, claim and prepare
RPCs; direct owner inserts in tests are not a second production ingress.

## Monitoring and transport history

The existing hourly capacity workflow only reads the authoritative leased
backend. It no longer creates rotation branches or PRs. The existing database
health monitor reports active capacity, archived count, and stalled dispatches.
Expired claim/delivery/callback deadlines and missing required delivery or
callback deadlines are stalled; overdue queue/retry waits also alert. A stalled
record still consumes capacity. Time passing does not prove external execution
stopped and must not authorize duplicate work.

`autopilot_transport_history` is a separate warning for legacy commands whose
actual Codex command context is the mailbox PR. It cannot turn into a critical
global gate. Commands in a target PR (0362) and NATIVE work do not accumulate
that mailbox's command context. Each target PR still needs its normal bounded
scope and fresh evidence. No automatic deletion of external comments is added.

The former lifetime limit of 40 was a local safety heuristic after a mailbox
with 421 records/comments and roughly one million body characters experienced
provider failures. This does not establish an external hard limit of 40 or
prove those failures' sole cause. Raising the number would hide the conflation
between working pressure and retained transport history. Rotation remains an
optional, explicitly reviewed transport maintenance operation when its actual
context requires it; it is no longer required merely because tasks finished.

The old `mailbox_rotation_readiness()` signature and ACL remain for existing
clients. Its counters now describe active rows and its states are `NORMAL`,
`NEAR_CAPACITY`, `CAPACITY_REACHED`. Historical `mailbox_rotation_signal` peaks
are retained and never used as current occupancy. The E2E acceptance counters
remain historical, not counts of active jobs or proof of current health.

## Application and recovery

Before application: reconcile exact main, acquire the coordinator's database
window, retain Light HOLD and video/IBM holds, capture the affected definitions
and aggregate record/receipt identities, and verify the existing restore proof.
Apply only 0403 to the confirmed current backend with bounded statement/lock
timeouts. This change does not choose or migrate a backend, create credentials,
change grants, activate the worker, replay an incident or clear owner holds.

Read back active+archive=total, the exact original receipt/dispatch identities,
current capacity, guard definitions and unchanged ACLs before leaving the
window. Expected on the observed mailbox #1703: 40 archived, 0 active, 40 free.
No historical task is dispatched again to demonstrate the repair.

The migration stores exact predecessor function/view definitions. Rollback
restores them without removing task/dispatch/receipt/evidence records. Since
that restores the old lifetime admission block, retain HOLD until a reviewed
forward recovery. Once processing resumes, all later writes must be reconciled
before choosing a larger rollback or storage migration.

The accompanying connection repair removes an unsupported startup GUC from the
single read-only probe and keeps `SET TRANSACTION READ ONLY` as its first SQL
statement. Neon documents that pooled connections can reject unsupported
startup options: https://neon.com/docs/connect/connection-errors. This corrects
a supported-route incompatibility; only an actual production read can show
whether it resolves the observed `MAILBOX_CONNECT` failure.
