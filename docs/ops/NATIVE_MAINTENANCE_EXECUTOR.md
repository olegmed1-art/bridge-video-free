# Journalled native permission executor (dormant)

Date: 2026-09-26. Governance: ASSURED / I2 before merge.

The individually reviewed pause/API/HOLD/DB-session primitives need a single
ordering contract. `ops/native_maintenance_executor.py` now composes them:

1. Bind target, exact source and original authenticated run, permission manifest,
   route, approved private HOLD identity and workflow plan into a private journal.
2. Verify independently supplied owner/admin/rerun coordination, authenticated
   running job and supervised host lifetime; pause only approved workflows.
3. Verify workflows remain paused, existing jobs/remote work/backends are drained,
   and the actual approved Light HOLD identity remains unchanged.
4. Persist `SESSION_INTENT` with UNKNOWN outcome **before** entering the real
   permission session. That session holds the route fence and PG writer locks
   across the GRANT/REVOKE commit and fresh observer.
5. Record the result only after continuity rechecks. Never automatically restore
   workflows on success, exception, cancellation or expiry.
6. In a separate restore call, independently re-observe the exact DB manifest and
   completion of remote processes/backends before every workflow enable. Preserve
   workflows that were already disabled. Record restore intent and completion.

A recorded session intent permanently forbids replay through the same journals,
including after process restart or in a new authenticated run. A new run can
reconstruct the bound scope and restore after independent reconciliation, but
cannot start the original session even when its journal contains only BOUND.
Intent without acknowledgement remains UNKNOWN. An ambiguous workflow PUT is
not retried or reversed by this executor. It requires the pause primitive's
separate operator reconciliation. Corrupt/private-journal failures block reuse.

## Required production assembly

This is a callable component, **not** an activated production service. There is
no CLI, credential loading, production entry point, or permissive default guard.
The component imports the real WorkflowAPI, WorkflowPause, HoldMaintenanceGuard
and permission session. Deployment must supply all of the following:

- An authenticated `RunBinding`, exact reviewed current source and accepted
  manifests; the API adapter also checks main before mutation.
- Durable, private, exclusive operator storage for two separate journals. Runner
  temporary disks do not satisfy persistence. Do not publish their contents:
  scope includes the approved private HOLD continuity identity.
- Actual scoped coordination of privileged writers, administrators and rerun
  actors; authoritative closure of the affected workflow/callee writer set.
  `operator.assert_held(scope_digest)` establishes that coordination;
  `assert_drained(scope_digest)` additionally establishes existing job/process/
  backend drain. An empty Actions list or group ownership does not establish it.
- Independently supervised execution with `lifetime.assert_alive()`. Owner-capable
  DB access and host root authority must be supplied by the approved runtime.
- Independent `release.assert_reconciled(scope_digest, outcome)` confirming the
  actual BEFORE/AFTER database state and completion of all remote work/backends.
  A saved SESSION_RESULT alone cannot authorize enable. Restore verifies run,
  operator and lifetime continuously; HOLD is a session prerequisite, while
  restore relies on independently reconciled completion rather than a new HOLD.

The existing run binding expires within 60 seconds and the route fence defaults
to 30 seconds (maximum 60). Repeated authenticated API and host observations must
fit the actual supervised window. CI does not prove live timing feasibility.
The exact original source remains required for recovery through this component;
source drift fails closed and needs separately reviewed operator recovery.

## Verification and recovery

Unit contracts exercise persistence-before-session, lost return, session replay,
missing continuity/drain/HOLD, scope drift, rejected release, and ambiguous
workflow disable/enable. The disposable PG18 fixture executes actual apply,
rollback, apply with a deliberately lost return after real commit, then rollback.
It verifies a real route client is blocked during SQL, reconstructs journals in
a new simulated run, refuses replay, independently inspects the DB, and confirms
connections and fixture ACLs are restored. GitHub, run, supervisor, HOLD and
operator coordination in that fixture are **explicit CI stubs**, not live proof.

No workflow is disabled and no production SQL is executed by these CI tests.
The change does not retire existing HOLD, grant, route, lifetime or writer gates.
Source rollback is a revert; a revert cannot reconcile an in-progress session.
For operational failure, preserve both journals and disabled workflows, inspect
DB/remote completion independently, then use a fresh accepted run for explicit
restore. Do not delete journals, replay GRANT/REVOKE, blindly enable workflows,
or infer a rollback from a disconnected client.
