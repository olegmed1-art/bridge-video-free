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
The installer now requests `show --all` for scalar properties and reads the
typed EnvironmentFiles D-Bus property directly, requiring exactly `a(sb) 0`.
This systemd255 host omits that empty array even with --all; missing scalar
properties and unexpected/nonempty D-Bus results still fail closed. `complete-retained-hold` accepts separately hashed current helper
and original f82d58e packages. It verifies the original root before record,
immutable runtime, exact original unit/control and pristine empty state, plus
no prior invocation/PID and empty cgroup, before the dormant rehearsal. It does
not restage, overwrite the unit, or clear prior evidence. Retained observe/stop
use the same old runtime with fresh current-controller source checks. Any
already-started or partially rehearsed installation refuses completion replay.

Read-only inspection subsequently identified exact legacy fingerprint drift.
A retained completion may now prove one narrow equivalence: rebuild only the
reset ExecStart accounting start_time and pid from current ExecMain properties,
then apply the original canonical fingerprint formula to every live service
field and live ENV/pins/route/drop hash. The rebuilt SHA must exactly equal the
original private retained fingerprint. No baseline is replaced, no credential
hash is ignored, and there is no timestamp search. Inspect emits only the
boolean result without writing. Completion retains a create-only private proof
and repeats full live attestation plus the same proof before each start and
receipt write. Any mismatch still refuses and preserves the original evidence.
This proof must succeed on the actual host before a completion is dispatched.

## Owner handoff for subsequent jobs — 2026-09-29

The owner controller accepts version 2 phase payloads with the existing fields
plus `predecessor: {plan_sha256, terminal_sha256, sequence}`. Version 1 remains
sequence zero. The successor uses a distinct accepted Plan and dispatch; its
sequence is exactly predecessor.sequence + 1. This is an owner-reviewed bounded
handoff, not an autonomous task issuer or a production activation.

Before prepare, publish, permit and execute, the controller checks retained
predecessor restore/restart evidence, exact current HOLD invocation, immutable
terminal history, owner acceptances, and fresh original Cloud/DB terminal.
The dormant process keeps its flock; owner inspection reads completed records
without taking that lock. The consumer independently validates its full journal
and acquires the lock when the bounded transient executor starts.

Publication retains a create-only per-sequence feed intent and permit before
atomically replacing the exact previous root cursor under HOLD. Historical
claims, terminals and acceptances remain unchanged. An uncertain publication
is not retried. Before a new feed intent exists, containment can disable the
new intake while preserving the previous history; after an intent exists,
incident reconciliation is required. Existing supervisor bounds, deny/stop
cleanup, terminal verification and DB-control restoration remain mandatory.

Local tests cover successor publication while the dormant lock is held,
missing/corrupt acceptance and history, quarantine, RUN admission, lost permit
acknowledgment, repeated publication, containment, stage drift, and bounded
successor cleanup. These contracts do not establish live multi-job readiness.
Deployment still requires an accepted finite next-task agreement, fresh CI and
independent assurance for its exact controller, a new serial live result and
verified restoration. Permanent operation additionally needs an accepted task
issuer, monitoring and recovery ownership; a completed pilot is insufficient.

### 2026-09-29: contained publication-lease incident

The sequence-1 preparation for plan
`3748d3ae6288b650182d851d8f161871ca5ff302ca12d8de6b2ace214b8315a3`
never reached execution. The 300-second outbox claim expired before the reviewed
permit phase. Containment disabled native admission; it deliberately left the
queue unresolved and `can_repair=false`.

The `recover` owner action is restricted to that exact plan, predecessor,
intake/discovery digests, task, dispatch and work item. It requires unchanged
HOLD/history, no feed/execution or native receipt, expired first claim and the
retained containment record. It preserves the draft publication as evidence.
One locked transaction marks the never-executed outbox, step and task
`FAILED_CLOSED`, records a preexecution event (not a provider result), places
work in `PAUSED / OWNER_HOLD`, and restores controls from the original snapshot.
The deployed task trigger also increments the planner decision count and records
its BLOCKED decision; those four planner fields are captured and checked. Its
NOTIFY is delivered only after the work is PAUSED at commit. All other captured
row fields and graph counts must remain unchanged. No retry, native
receipt, provider terminal, new grant or migration is authorized.

Root recovery intent and before/expected-after rows precede COMMIT. A lost ACK
permits exact read-only reconciliation, never SQL replay. If an intent exists
without an expected-after record, or rows differ, recovery refuses: preserve all
records and obtain a newly reviewed incident-specific recovery after proving
what committed. Do not remove intent or rerun a failed workflow. Successful
recovery does not create a completed serial sequence or authorize production.

Validation uses current deployed constraints/trigger definitions, independent
I2 review and fault tests, not a stale test-branch schema or a production rollback
rehearsal. The task summary deliberately lacks `status=BLOCKED`, which the live
terminal trigger would otherwise interpret as potential repair authority.
The lease-lifetime design for future tasks remains a separate unresolved item.
