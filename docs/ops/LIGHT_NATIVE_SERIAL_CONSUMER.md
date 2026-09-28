# Serial native consumer — dormant implementation

Status: ASSURED source candidate, 2026-09-28. This removes the consumer's
single-dispatch limitation. It does not install a service, change admission,
issue permits, turn on the old scheduler or claim continuous production.

The one-task READ_ONLY pilot on main526d344 passed the actual Cloud→Oracle
terminal path. The persistent `worker_v17` still uses legacy task/planner/outbox
lanes; changing that worker's HOLD to ACTIVE does not activate this consumer.

## Scope and handoff

`python -m oracle_autopilot.light_native_lane` is an independent, source-pinned
entrypoint. It uses the existing runtime identity check, Permit, Session,
NativeQueue, LightNativeAdapter and target-bound LightProvider. Only the proven
READ_ONLY scope is admitted by Permit. No automatic task producer, repair,
paid fallback, general scheduler or permission activation is added.

A trusted owner controller supplies already-published dispatches. Runtime needs
both process environment `AUTOPILOT_ADMISSION_MODE=NATIVE` and root-owned bytes
`RUN\n` at `/etc/bridge-school/light-native-lane/admission`. Idle HOLD creates
no database/provider effects. Every Session RPC/provider boundary rechecks live
admission, current cursor, permit expiry, immutable claim and both locks.

The root-owned `current.json` is canonical JSON:

```
{version:1, source:<40-hex>, sequence:<integer starting at 0>,
 dispatch_id:<UUID>, permit_sha256:<64-hex>, previous_terminal_sha256:<null or 64-hex>}
```

This notation describes fields, not executable JSON. The permit is at
`jobs/<dispatch_id>/permit.json` in the same control root. The controller must
independently verify publication, exact PR/head, owner preflight, environment,
and the finite no-write agreement before retaining it. The consumer cannot
renew or mint it; a persistent process does not create permanent authority.
Existing permit scope and expiry checks are unchanged.

The installer pre-creates `runtime/native-lane` as a service-owned private0700
directory. The consumer holds a global flock, retains fsynced create-only
`NNNNNNNN-intent.json` before reservation, and creates a separate private
`claim-<dispatch_id>` directory. Every terminal claim remains intact. The next
intent must be consecutive, name a new dispatch and bind the previous terminal
record digest. Holes, orphan claims, changed/missing files and partial records
fail closed.

After Session independently reads back the database terminal, the consumer
records `NNNNNNNN-terminal.json` and reports AWAITING_OWNER. It performs no new
provider work while the same cursor remains selected. The controller must then:

1. Run `ops.light_native_lane_owner.verify_terminal` with the retained exact
   intake Plan/receipt, intent, terminal and permit, on its verified read-only
   owner connection and verified immutable source. The helper freshly reads the
   original provider journal and Cloud status/diff under the service profile,
   compares exact evidence/status/result, and requires no source changes. It
   reuses `intake.observe_terminal` for native receipt/task/work/outbox closure,
   original goal, one mapped task and no successors.
2. Retain the returned canonical bytes as root-owned
   `jobs/<dispatch_id>/accepted-terminal.json` with mode0640/service group,
   create-only fsync/readback. Merely computing `acceptance_record` or copying a
   model report is not this primary-source verification.
3. Verify the next dispatch independently, retain its fresh permit, and publish
   the next root cursor only after the acceptance file is durable. No cursor
   transition or file write is supplied by the verifier in this change.

Control parents/leaves use existing symlink-free root metadata checks. Runtime
has no write access to owner controls. No append, overwrite, deletion or reset
API exists for runtime evidence.

## Failure, restart and rollback

Any exception after entering the lane writes `quarantine.json` and latches the
process. Admission loss during work denies later effects and quarantines; it
cannot atomically cancel a Cloud operation already in flight. Unknown creation
never advances to a different item. Quarantine survives process restart.

A killed process may not have written its latch. An intent without a durable
terminal is therefore quarantined on startup, even if its Cloud task actually
finished. There is no automatic recovery or permission renewal. An operator
must reconcile the original reservation/provider ID through a separately
reviewed recovery; deleting state or replaying the dispatch is not recovery.
A completed record is checked against retained claim bytes on restart, then
waits for owner acceptance without submitting again.

This source-only change is reversible by reverting its commit. Deployment must
supply a verified installer, source manifest, hardened unit/UID, owner-feed and
acceptance writer, live database scope/authority, and restore-tested HOLD
rollback. These are concrete remaining deployment work, not missing user
permission. The old worker remains HOLD, and this module is not inserted into
its imports or execution path. No fresh production run has validated the lane.

## Validation

Tests use real filesystem locks/create-only records and the actual
Session/NativeQueue/adapter/delivery composition with deterministic DB/provider
ports. They prove two distinct jobs in sequence, exact owner handoff, zero
second submit after crash/unknown, durable HOLD quarantine, source/cursor
binding, corrupted/orphan evidence refusal and terminal restart. Owner tests
exercise existing transactional intake readback and reject Cloud/DB drift.
These are local/CI contracts, not claims of live multi-task production.


## Dormant installation and owner acceptance storage

`light-native-lane-install.yml` accepts only exact current main, the reviewed
package digest, and `install-hold`, `observe-hold` or `stop-hold`. Installation
stages immutable source using the existing read-only DB/profile preflight and
creates a separate native service with both environment HOLD and root HOLD.
The unit has no credentials, no network, no boot enable and no auto-restart.
It attests actual process uid/cwd/argv/environment, source, hardening, and a
fresh invocation-specific HOLD audit. An active but quarantined process fails.
A stop/empty-cgroup/restart rehearsal proves dormant reentry, retaining private
before/stop/installed receipts. Interrupted installation is never replayed or
cleared; a started unit is stopped on failure. Legacy HOLD is not restarted.

This is a dormant installation, not production activation. Activation needs a
separate reviewed unit update, finite owner feed and live DB/control agreement.
`retain_acceptance` now reads exact host intent/terminal/permit bytes, reruns
fresh Cloud/DB verification and stores a create-only root0640 acceptance. A retry
reruns primary verification and requires identical retained bytes. It never
writes current, RUN, a new permit, or a task. The authenticated owner controller
must still bind its original retained Plan/receipt and immutable release before
calling it. No owner-feed issuer or acceptance CLI is exposed by this change.


The first live install of source f82d58e retained the unit and state but refused
before any service start: systemctl omitted the empty EnvironmentFiles property.
The installer now explicitly requests `show --all`; missing properties still
fail closed. `complete-retained-hold` accepts separately hashed current helper
and original f82d58e packages. It verifies the original root before record,
immutable runtime, exact original unit/control and pristine empty state, plus
no prior invocation/PID and empty cgroup, before the dormant rehearsal. It does
not restage, overwrite the unit, or clear prior evidence. Retained observe/stop
use the same old runtime with fresh current-controller source checks. Any
already-started or partially rehearsed installation refuses completion replay.
