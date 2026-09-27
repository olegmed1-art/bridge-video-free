# One accepted Light native pilot

This source adds a dormant, explicitly invoked loader. It does not change the
worker entrypoint, install control files, grant permissions, enable the native
queue, publish a dispatch, or release production HOLD.

The reviewed deployment controller must install an immutable source release and
invoke `python -m oracle_autopilot.light_native_loader` in that exact release
under the existing Light service identity and hardening. It must not start the
legacy scheduler concurrently. `NoNewPrivileges` remains enabled.

## Admission and evidence

The root controller owns `/etc/bridge-school/light-native-pilot`. All parent
directories are root-owned, without group/world write or symlinks; private
control leaves are root:Light-service-group 0640. The service needs directory
traversal. Files are `permit.json`, `accepted-sha256`, and `admission`.

Both the process environment `AUTOPILOT_ADMISSION_MODE=PILOT` and live admission
bytes `PILOT\n` are required at every boundary. `HOLD\n`, missing files,
replacement, expiry, identity drift, loss of either lock, or failed I/O deny
further effects and latch the process. A deny does not cancel an already-running
Cloud request atomically. It also denies ACK and collection: preserve the
provider journal and reconcile the exact task ID through a separately reviewed
recovery attempt.

The accepted canonical permit binds one source, one published shared-admission
dispatch, exact open PR/head/branch, READ_ONLY audit scope, zero repair/paid
budget, verified Cloud environment evidence, and an expiry of at most two hours.
It must contain a separately accepted `light_native_preflight.observe` result.
That owner observation binds Neon identity, task/role/work item, original goal
digest, absent successor, repair-disabled role, v3 PUBLISHED outbox, publication
comment ID, and zero competing native receipts. The controller must authenticate
the real GitHub publication and Cloud environment independently; a syntactically
valid ID or a hash alone is not that evidence.

The agreement digest is an independently obtained, scoped director no-write
commitment. Its window must cover preflight, reserve, provider execution,
terminal acceptance, and independent task/role/continuation readback. In
particular, privileged edits to task goals and roles are excluded by that
operational agreement. This is **not database-enforced exclusion** of owner
changes. If the window expires before completion, quarantine rather than finish.

## Durable claim and recovery

The controller pre-creates
`/opt/bridge-school/school-autopilot-production-light/runtime/native-single-pilot`
as a private service-owned 0700 directory. The loader retains canonical permit
and actual DB-issued reservation bytes with exclusive creation and fsync. It
holds a local flock and one advisory lock on the same verified direct DB session
used for all six native RPCs. There is no transparent database reconnect.

Restart can resume the exact retained request without requiring the PR to return
to its original head. The delivery adapter still checks authority before any new
creation and before terminal acceptance. Lost create outcomes and lost database
authority quarantine; they do not select another request. Changing or clearing
the retained claim is not a supported recovery action.

On terminal or quarantine, the loader closes the DB connection and remains
alive without retrying so `Restart=always` cannot form a retry loop. The external
controller must restore the admission file and service to HOLD and read back the
terminal receipt, original task/role, successor/repair rows and provider journal.
Service `ActiveState=active` alone is not completion evidence. Logs contain fixed
audit codes, IDs, and a digest, never model result text or raw exceptions.

## Rollback boundary

Before activation the controller needs an independently reviewed, exact-source
installer and restore-tested backup of prior unit/drop-in/release/control state.
If no provider call began, restore HOLD and prior service configuration. Once a
provider creation may have happened, first quarantine and retain its exact ID or
UNKNOWN journal; do not erase evidence, resubmit, or revoke permissions needed
for a separately authorized reconciliation blindly. The production permission
transaction has its own manifest-bound rollback; the loader does not replace it.

## Verification

The unit suite exercises one-task completion/restart, connection loss, two local
controllers, stale PR recovery, live HOLD, expired permits, retained-record loss,
root file privacy and routing reconstruction. The disposable PG18 fixture uses
real shared admission and native RPCs with a synthetic provider; it makes no
claim of a live GitHub publication, Neon identity, or Cloud task.
