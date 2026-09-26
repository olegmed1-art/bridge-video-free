# Critical catalog boundary for dormant grants

2026-09-26; ASSURED design reassessment after PR #1975.

The grant-only promise is narrow: add the reviewed six direct EXECUTE entries,
keep native admission disabled, and finish fresh inspection before releasing
the table fence. It is not activation and does not guarantee that a trusted
administrator will never change the system after this operation ends.

## Reassessment

The existing full-catalog manifest is conservative: unrelated function or role
changes can cause refusal even when they cannot affect native RPC authority.
The prior broad inventory of credential candidates does not prove that every
candidate must be stopped. Replacing that inventory with a smaller critical
writer set is reasonable only after proving which interleavings matter.

Table DML is already excluded by the separate write fence across COMMIT and
fresh inspection. Privileged updates to native RPC definitions, helper/callee
definitions, effective recipient authority and configuration dependencies are
a different class. Metadata drift detection does not prevent a newly granted
capability from being used before the drift is detected. Restoring metadata
after an unsafe call can also conceal its transient cause.

## New disposable PostgreSQL evidence

`native_cli_catalog_conflict_rehearsal.py` runs inside the existing CI fixture.
It injects a contender at the third MaintenanceGuard check, after the engine's
last catalog snapshot and before its COMMIT. The maintenance guard is explicitly
a test stub, not a production exclusion mechanism.

For each of the six functions, an independent privileged connection attempts:
an ACL grant, ALTER FUNCTION settings, an owner change, and CREATE OR REPLACE
with a harmless body comment. The test requires actual bounded lock refusal;
the outer grant transaction is rolled back and the original manifest verified.
The contender is a superuser to avoid mistaking missing permission for a
conflicting lock; the grant engine remains a non-superuser owner.

Two further cases commit a helper ACL grant or a recipient membership grant
after that final snapshot, while table locks remain held. The engine may return
AFTER under the test stub, but fenced fresh inspection must return DRIFT.
The fixture explicitly restores its own injected catalog delta, then revokes
only its six grants and verifies the baseline. It invokes no native RPC.

Expected markers (actual CI must confirm):

- `NATIVE_GRANTED_FUNCTION_CATALOG_CONFLICTS_PASS`
- `NATIVE_LATE_CATALOG_WINDOW_CONFIRMED helper-acl`
- `NATIVE_LATE_CATALOG_WINDOW_CONFIRMED recipient-membership`

The latter two markers demonstrate a limitation, not production safety. These
tests do not inject a lost COMMIT response, transient modify/use/restore attack,
or concurrent runtime RPC effects. They do not establish that all six RPCs and
their dependencies have no effects under disabled configuration.

## Next bounded decision

Retain the refusing default MaintenanceGuard. Identify and coordinate actual
writers of the critical objects and authority, using reviewed definitions and
workflow/host entry points. Ordinary unrelated readers and backups should not
be stopped merely because they carry credentials. Complete relevant runtime
and post-COMMIT race evidence before narrowing an implemented production gate.
Do not claim complete project maintenance from a session snapshot or a CI pass.

No production grants, role changes, service restarts or activation occur here.
Rollback is a code revert; all test data lives in disposable CI.
