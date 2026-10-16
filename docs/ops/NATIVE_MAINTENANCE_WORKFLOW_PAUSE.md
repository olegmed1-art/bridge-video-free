# Recoverable workflow exclusion for native maintenance

2026-09-26; ASSURED preparation; recovery #1946. Baseline main:
`2e20fd89d2470053496d26192a9162900591d815` (PR #1984).

## Problem and selected approach

GitHub concurrency ownership ends when a job is cancelled, potentially before
the remote process and database transactions finish. The existing maintenance
session already combines route exclusion, table fencing and fresh DB outcome
inspection. A second transaction engine is unnecessary.

The selected next mechanism is scoped, persistent workflow disabling with an
explicit recovery path. It does not alter every writer to use a new permanent
lock. Only reviewed conflicting workflows belong in the plan; source readers
and independent backups are not automatically included.

`native_maintenance_workflow_pause.py` is the dormant journalled primitive for
that mechanism. This revision supplies no HTTP adapter, credentials, production
entrypoint, workflow allowlist or DB permission. Its tests use a fake GitHub API;
the journal/crash tests use the actual filesystem and process termination.
There is intentionally no `assert_held(target, operation)` method: disabled
workflow observations do not constitute the complete DB maintenance guard.

## Execution and recovery contract

1. Independently review an exact plan: repository, source commit, workflow IDs,
   paths and observed initial states/timestamps. The supplied digest binds it.
2. Establish real operational coordination of relevant workflow administrators,
   rerun-capable actors and direct owner DB channels. A scope callback must check
   this; no self-issued approval implementation is supplied here.
3. Open an exclusive journal in a private persistent operator directory. The
   deployment must retain it independently of the controller and Actions runner.
   fsync on a disposable runner is not durable operational storage.
4. Before each disable, fsync a non-overwriting intent and its directory. Read
   back the API state after the request and journal the exact observation.
5. Drain historical/queued/running attempts and remote DB backends separately.
   Neither `pause()` nor `assert_paused()` claims drain or prevents direct writes.
6. Execute the existing reviewed session with the complete external writer guard,
   HOLD identity, protected DB manifest and supervised owner-capable runtime.
7. Recovery must independently establish that remote work/backends have ended and
   fresh protected DB inspection classifies BEFORE or AFTER. Only then may a
   separate `restore(release)` re-enable workflows that this operation confirmed
   it disabled. Workflows initially disabled remain unchanged.

Cancellation, deadline expiry, API failure and UNKNOWN never run automatic
restoration. A missing response after a disable/enable leaves an ambiguous
intent; reconstruction refuses retry/restore. The record must be separately
reconciled by a reviewed operator procedure. There is no journal-editing or
ambiguous-intent override API in this revision.

An incomplete/corrupt tail, substituted filesystem entry, changed workflow,
changed timestamp, changed plan or lost coordination refuses further writes.
Restarts can resume only fully recorded transitions. A failed instance cannot
recover silently just because the next observation succeeds.

## Boundaries and remaining production gates

GitHub's documented disable endpoint sets `disabled_manually` and requires
Actions write permission. Its documentation does not establish that every
historical rerun/reusable caller or already-dispatched job is prevented.
Those paths and their principals must be covered by actual operational exclusion
and drain before integrating this primitive as one input to a writer guard.
Workflow state has no exclusive ownership token; coordination must exclude
concurrent administration. State/timestamp comparisons are drift checks, not a
distributed lock or proof against API staleness.

Source: https://docs.github.com/en/rest/actions/workflows#disable-a-workflow

The bounded allowlist, real API adapter and durable host storage, direct-owner
coordination, remote/session integration and independent ambiguous-outcome
reconciliation remain production prerequisites. This revision must not be wired
to a secret-bearing workflow as a complete maintenance implementation.

## Verification and rollback

Tests cover restart, actual process death after durable intent, partial disable,
lost disable/enable replies, failed journal writes, lost coordination, unfinished
remote work, simulated unknown DB outcome, external enablement, corrupted
records, FIFO/symlink rejection, exclusive locking and preservation of initially
disabled workflows. Fake release callbacks exercise ordering only; they are not
live Oracle or PostgreSQL evidence.

Code rollback is a revert. No live workflows, DB grants, native configuration,
Light services, admission or tasks are changed by this revision. Future live
use requires preserving the journal even if code is rolled back; an interrupted
maintenance must be reconciled before workflows are resumed.
