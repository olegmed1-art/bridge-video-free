# Staged native permission maintenance

2026-09-26 UTC. ASSURED preparation for #1946; no production activation.

The authenticated synthetic relay took 40.119 seconds of its 60-second run
lease. That result covers two checkpoint publications, not workflow exclusion,
database fencing, permission changes and postchecks. Putting all work into the
same lease is unproven. Increasing the timeout has not been authorized by this
change. The executor now offers an explicit staged mode while preserving the
original single-run mode and its existing journals.

## Durable ordering

| Stage | Required state | Durable result | External effect |
| --- | --- | --- | --- |
| `prepare()` | Original authenticated run, new staged scope | `PREPARED`, fully checkpointed journal pair | Pause only the reviewed workflow list |
| `execute_prepared()` | Different authenticated run, independently accepted current remote head, identical locked local pair | `SESSION_BOUND`, then `SESSION_INTENT/UNKNOWN`, each checkpointed before SQL | Exactly one apply or rollback attempt |
| `restore()` | Fresh independent DB/process reconciliation | `RESTORE_INTENT`, then `RESTORED` | Restore only workflow states owned by this operation |

One immutable scope binds source, target, manifest, plan, route, HOLD and origin
run. `execution_mode=staged_v1` prevents reinterpreting a legacy journal as a
staged authorization. Every stage obtains a new, nonrenewed authenticated run
binding and supervised host lifetime. This does not extend an expired lease.

`PREPARED` is not a permission permit. The execute stage rechecks current source,
HOLD, paused workflow states, scoped operator coordination and drain. Existing
route and database fences still surround the SQL operation and its independent
postcheck. The same unexpired direct-admin no-write commitment must cover the
whole operation, including the interval between stages; none is inferred from
the workflow pause or a historical statement that nobody else was working.

`accept_resume()` reads the independently accepted current remote head, verifies
the private archive and requires exact equality with the locked local pair. It
cannot publish a local suffix or accept an inferred head. Subsequent checkpoint
publication still uses the existing append-only/CAS protocol. A stale acceptance,
local-only PREPARED suffix or competing remote advance refuses.

An ambiguous PREPARED acknowledgement fails the current preparation instance.
It is not a permanent ban on this operation: preparation cannot execute SQL.
If the remote PREPARED head was actually committed, a different run may proceed
only after independent acceptance of that current head, exact pair comparison,
fresh paused-workflow/HOLD observations and real operator/drain reconciliation,
including completion of the previous host process and its database backends.
Accepting a hash copied from the failed local process is not independent
acceptance. A digest alone and the local `prepared` state confer no authority.
The production assembly must obtain that acceptance outside the failed run.
If the remote head still precedes PREPARED, the local suffix cannot authorize
dispatch. An ambiguous SESSION_BOUND/SESSION_INTENT is different: it consumes
the opportunity, so a recovered bound/intended session is never dispatched.

Once `SESSION_BOUND` is appended, no entrypoint can rebind or resume the session,
even if the process stopped before `SESSION_INTENT`. Once intent exists, an
unknown return never implies rollback. Separate reconciliation/restore remains
available; neither success nor failure automatically enables workflows.

## Evidence and remaining assembly

Contract tests exercise the complete three-run ordering with real locked journals
and checkpoint serialization over an explicitly simulated remote store. Fault
cases include missing/stale acceptance, local-only preparation, a committed
PREPARED with lost acknowledgement (refused until fresh drain reconciliation),
death after binding, lost intent acknowledgement and lost SQL return. The disposable PG18
fixture exercises both execution modes, actual apply/revoke, route exclusion,
independent postcheck and recovery after a real commit with a lost return.

These tests do not claim a live staged timing result or real operator authority.
The production workflow/runner, actual job/backend drain observations, explicit
operator commitment and separately accepted checkpoint transfer between runs
still need assembly and one complete no-write rehearsal before the grant.
Preparation itself must also fit its own bounded run. A timeout leaves the
operation for reconciliation; it never silently renews the lease.

The registry observation at main `49f814565adcb6f81feb193283610cc0ac5413f2`
refused authentication in run `36270503687`. Keep
`recovery-registry-population.yml` in the scoped pause/drain plan. The separately
verified maintenance owner connection passed with native disabled and zero Light
EXECUTE grants. Do not change the registry credential to obtain an exemption.

Rollback for this code change is a revert before any staged production scope is
created. Once such a scope exists, retain this reader and its journal format for
reconciliation; reverting code is not restoration of workflow or database state.

## Runtime coordination implementation

`native_maintenance_coordination` provides the readers for the staged runtime:

- `Agreement` checks a separately accepted record for the exact operation spec,
  evidence reference, direct-owner/host/workflow-admin/rerun coverage and an
  explicit window no longer than 30 minutes. Wall-clock rollback, monotonic
  expiry or changed bytes latch refusal. The code cannot supply the director's
  missing future commitment by hashing a record it has just invented.
- `WorkflowDrain` requires zero runs in each of GitHub's five nonterminal states
  for every workflow in the accepted pause plan. It does not filter by branch,
  source or creation date, so historical reruns count. A nonzero, malformed or
  incomplete/paginated response refuses; no page traversal is needed to establish
  refusal after any unfinished run is found. API status definitions:
  https://docs.github.com/en/rest/actions/workflow-runs#list-workflow-runs-for-a-workflow
- `OwnedConnections` verifies the target of actual owned connection objects and
  registers their backend PID plus start time. It never accepts caller-provided
  PID exclusions or trusts application names. Foreign/invisible backends and
  prepared transactions refuse.
- `PriorSupervisors` reads independently accepted unique unit/invocation/cgroup
  identities and requires completion/empty cgroups. It never stops a process.
  A cross-run operator cannot omit the original preparation unit evidence.
- `Operator` binds those components to the exact scope, source and authenticated
  run. Any observation failure latches refusal. The executor still supplies the
  route fence, table locks and independent permission postcheck.

A live read-only observation found `pg_read_all_stats=true` for the maintenance
owner and one idle Light worker connection. The latter is deliberately allowed
only for the exact recipient `autopilot_light_worker_login`, at most one client
backend, idle with no transaction or backend xid/xmin, visible identity, unchanged
approved HOLD and freshly verified disabled native configuration, empty queue and
zero receipts. An active/transactional/second worker or any other foreign client
refuses. This is a scoped HOLD exception, not proof that idle connections cannot
write later; route/database fences and operator coordination remain necessary.
Stopping the existing service would invalidate its approved HOLD identity and is
not part of this implementation.

The disposable PG18 executor fixture now uses the actual owned-backend drain for
both execution modes, including a real foreign owner connection that must refuse.
It temporarily supplies the CI owner with statistics visibility and restores the
original role membership afterward. External operator agreement, GitHub and host
authorities remain explicitly simulated in that fixture. Production still needs
the reviewed runner to supply accepted agreement/prior-unit/head evidence and
execute the complete bounded rehearsal before granting rights.

## Consolidated admission observation (opt-in)

Assembly review after #2011 found recursive remote observations: for one planned
workflow and the permitted idle Light backend, a legacy HOLD assertion issues
about 64 GitHub GETs and five HOLD attestations. This is a call-graph count, not a
live measurement. It is not a viable basis for assuming the 60-second run and
30-second route windows will suffice.

`observed_admission=True` is explicit, staged-only, and binds
`admission_mode=observed_v1` into the immutable operation scope. It requires the
actual `Operator` and authenticated `RunBinding` on the same run. The concrete
`ObservedSessionWindow` replaces recursive authority reads with one no-effect
observation chain on **every** HOLD guard call:

1. Check local scope, journal locks, supervisor, agreement and fixed run deadline.
2. Observe the exact paused workflow states, all unfinished workflow runs, prior
   supervisors and registered/foreign database backends.
3. Observe the exact approved HOLD, and observe paused workflow states again to
   catch a re-enable during drain or HOLD inspection.
4. Obtain a fresh complete authenticated run/workflow/main/job observation, then
   recheck local agreement, journal, supervisor and deadline continuity.

The database drain retains its two HOLD checks for the idle worker exception.
There is no cached successful observation, lease renewal, inferred approval or
SQL dispatch in these read methods. The existing engine/session calls remain
unchanged, including checks before GRANT/REVOKE, before COMMIT and around fresh
outcome inspection. Route and database fence checks still follow the external
observation. OCI publication and workflow mutation guards keep their existing
ordering. Legacy callers retain their original guard path; a same-named method
on an arbitrary writer cannot opt into this composite.

Fault tests compose the actual coordinator/run/pause/HOLD classes over explicitly
simulated external infrastructure. They verify fresh reads on every call, a
re-enabled workflow during drain, cancellation/main drift after HOLD, agreement
expiry during final authentication, changed HOLD, foreign backends and subsequent
route/fence loss. One planned workflow now uses 12 GitHub GETs and three HOLD
attestations per guard call. These tests neither grant production authority nor
prove a real timing budget. Complete supervised no-write rehearsal remains a
production gate; the 60/30/100-second limits have not been increased.

## Host stage composition

`native_maintenance_runtime.stage` now composes the actual executor, operator,
owned connections, checkpoint barrier and a separately reconciled workflow
release. `operation_scope` is shared with the executor so the operator's scope
cannot be assembled from a different interpretation of the inputs.

The input is canonical private packet bytes whose digest was independently
accepted by the trusted controller. It binds the stage, immutable operation
scope, workflow plan, manifest baseline, current agreement, all prior unit
records, accepted remote checkpoint head and (for restore) expected DB outcome.
The packet parser proves consistency, **not the provenance of acceptance**. A
future launcher must obtain that acceptance outside the host operation; it must
not promote a newly observed candidate by computing its own digest.

Each stage verifies its PID1 unit/invocation/cgroup and rejects any other active
or populated `bridge-native-ro-*` supervisor, including one from another scope.
Under the pre-existing persistent store lock it preserves the exact manifest and
journal pair. Prior unit files must equal the independently accepted prior set;
missing or omitted uncertain stages require separate reconciliation. It retains
one private, create-only, fsynced unit record per run/attempt, outside BOUND/PLAN,
then requires synchronous off-VM write plus exact-readback acknowledgement before
constructing the executor. A lost acknowledgement leaves the local record intact
and prevents dispatch. It is never retried automatically.

Prepare pauses only; execute consumes the prepared operation once; restore
independently inspects the database, drains prior processes/backends and checks
HOLD before each workflow enable. No `finally` path restores workflow admission.
A successful host return explicitly says `host_exited=false`: only the runner
can observe supervisor exit and independently accept the remote head afterward.

This is a dormant host core, not an installed production entrypoint.
`StageRunBinding` has no installed workflow hash and refuses every real run.
The runtime rejects both the legacy window and the synthetic checkpoint profile.
The tests install an explicitly fake profile locally; they cannot authorize
production. The fixed manual workflow, SSH/driver/credential launcher, honest
private OCI unit-record retention/ACK, independent packet/head acceptance and
complete no-write timing rehearsal are still required before enabling this core.
There is no production CLI or automatic acceptance callback.

Tests exercise the actual composed classes with real private journals and
explicitly simulated GitHub/host/SQL/remote-store authorities. They cover the
three-stage path, lost/wrong unit ACK, omitted uncertain execution unit, missing
local evidence, stale remote head, DB drift, global orphan exclusion and rejection
of diagnostic profiles. These are wiring/failure-order tests, not live timing or
production permission evidence. Revert before use removes a dormant component;
after any future use, preserve its unit records and reader for reconciliation.

### Private off-VM unit acknowledgement

`native_maintenance_stage_unit` supplies the previously missing retention
component. The host `UnitClient` sends one canonical unit record over the existing
bounded authenticated duplex channel. The runner `UnitServer` checks channel and
operation bindings and uses `Retainer` with the actual `OCIJournalStore` adapter.
Records use a distinct `native-journal/stage-units-v1/<scope>/<run>-<attempt>.json`
prefix in the already approved private bucket.

Retention requires private-bucket policy, the existing object budget, create-only
PUT with no automatic retry, exact readback and fresh authority before ACK. An
existing record, even identical, refuses a new retention attempt. A committed PUT
with a lost response or lost pipe ACK remains for separate read-only reconciliation
through `read_accepted` with an independently accepted digest. A bad request or
acknowledgement poisons its channel; retention/readback failure poisons the store.
The record never approves the operation packet or proves that the host has exited.

Eleven fault tests use actual OS pipes and the actual store adapter over an
explicit simulated OCI client. They cover retained data with lost PUT/ACK return,
post-readback authority loss, wrong channel/scope/digest, existing record, public
bucket and independent accepted-digest readback. The future fixed launcher still
must wire both endpoints to the authenticated run/packet, supply dependencies,
verify supervised exit and demonstrate complete live timing. No production
entrypoint or workflow hash is enabled by this component.

## Fixed launcher assembly (2026-09-27)

This section supersedes the earlier missing-launcher inventory. The two fixed
manual workflow files invoke `native_maintenance_stage_launcher.py` with distinct
`stage` and `rehearsal` commands. Rehearsal has `actions:read`; only the stage job
has `actions:write`. Both require owner/manual/current-main context, the exact
workflow hash and both existing Oracle concurrency groups. The read-only profile is pinned to its exact workflow bytes. The production
profile remains uninstalled until live rehearsal establishes the timing budget;
adding the fixed production workflow does not enable it.

### Independent acceptance and prepare's new run identity

The owner/manual dispatch provides `expected_main_sha` and
`accepted_request_digest`. The latter must identify separately reviewed canonical
private bytes; neither submission nor a successful observation accepts a request.
The request format is exactly:

- `version:1`, a 32-hex `request_id`, and the 40-hex `source`;
- `assets`: accepted source-bundle, manifest, baseline and recovery-envelope
  SHA256 values (`source_digest`, `manifest_digest`, `baseline_digest`,
  `envelope_digest`);
- `packet`: the exact stage, scope, plan, baseline, director agreement, prior unit
  records, accepted checkpoint head and expected restore outcome described above.

Only prepare omits `scope.origin_run`, because its authenticated job ID does not
exist before dispatch. `AcceptedRequest` preserves the independently accepted
bytes. `DerivedStagePacket` inserts exactly the authenticated new run/attempt/job
for prepare and changes nothing else. Its calculated hash is transport integrity,
not director approval. The host independently authenticates that job and repeats
the derivation. Execute/restore preserve the already accepted origin, prior unit
records and remote head; the runner verifies them against OCI but cannot fill in
omissions or select a new head. `stage()` rejects a direct self-hashed packet and
all diagnostic profiles.

Private submissions use a separate root-owned 0700 namespace,
`/var/lib/bridge-native-stage-requests/{requests,claims}`. This does not change the
existing journal store's inventory schema. An exact VERSION marker and both
private subdirectories must already exist. `submit_candidate(raw)` creates a
canonical request as `<sha256>.json`, 0600/O_EXCL/fsynced/exact-readback, and never
approves it. Retain its exact bytes independently before dispatch. The runner
reads the fixed digest-selected file through pinned SSH; the supervised host
reads it again. Before any stage effect, `stage()` itself claims that digest with
a durable O_EXCL receipt bound to the run and derived packet. Lost acknowledgement,
cancellation or expiry after this point consumes the request. Never remove or
overwrite the claim to retry; reconcile first and obtain a separately accepted
new request if another stage is appropriate.

When the command service cannot elevate because of its existing NoNewPrivileges
setting, leave that restriction intact. The operator can stage only the reviewed
data as ubuntu in `/home/ubuntu/bridge-native-stage-submissions/<sha256>.json`
(0700 directory, 0600 regular single-link file). The already authorized fixed
GitHub SSH/sudo bootstrap imports this data only if the exact request leaf is
absent and the complete root namespace, VERSION and claims ledger are intact.
It checks owner, modes, bounded size, dirfd/inode continuity, canonical schema,
the externally supplied digest and pinned source, then performs create-only root
submission and readback. Existing-root corruption, permissions errors or a digest
mismatch never cause fallback, overwrite or cleanup. This provides no command
execution field or new privilege to the command service. A staged or root file
still confers no stage authority; authentication and the durable claim remain
mandatory.

The same read-only workflow has a separate explicit `first_install` choice for
one-time namespace provisioning. It is not a rehearsal request or a recovery
action. Its accepted digest binds `first_install_intent(source)`: version, pinned
source, fixed hostname/path and the purpose `first_install_only_not_loss_recovery`.
The authenticated read-only run is checked before and after the fixed SSH command.
Root creation is exclusive; an existing or partial root refuses without repair.
Routine submission/fetch never creates missing directories or replaces a missing
claims ledger. Any loss after first use requires separate offline recovery and
must not be treated as a fresh installation merely because a path is absent.
Before first installation the controller must reconcile prior use and the current
host, not infer history from absence alone. No SQL, workflow-state or pilot
change is part of provisioning.

### Transport, completion and failure

The runner restores the accepted source/manifest recovery envelope and compares
the restored source to the bundle built from the exact reviewed Git commit. It
keeps an exact private create-only request copy under
`native-journal/stage-requests-v1/` with readback before launching the stage. The
shared bootstrap runs byte-verified source under PID1, verifies the existing
private ARM64 driver, and delivers the owner URI and run token only through SSH
stdin. OCI credentials stay on the runner. Unit retention and journal RPC use the
same bounded channel, accepted scope and real private OCI adapter.

Completion requires the terminal frame, supervised SSH exit, a new read-only OCI
readback of the exact head/unit record, and a separate source-pinned read-only
host inspection of that exact unit/invocation/cgroup. A final fresh run/source
check must still fit the original 60-second lease. No lease is renewed. Failure
at any point produces refusal; it does not imply that preceding SQL was rolled
back. No exception handler dispatches another stage, grants rights, enables a
workflow, releases HOLD, restarts Light or starts a pilot.

The read-only request is a different schema: exactly `version:1`,
`mode:"read_only_rehearsal"`, `source` and the reviewed `plan`. It contains no
agreement or permission packet. The separate host module performs real owner
READ ONLY identity/snapshot, HOLD, workflow GET/drain and owned-backend drain,
then retains/restores only synthetic-scope journals through the shared transport.
It never calls the permission executor or workflow pause/restore. Its private
supervisor observation is checked after exit but is not a production unit record.
Timing is explicitly an estimate: workflow PUTs, permission SQL and direct-admin
exclusion are not measured by this rehearsal. A passing rehearsal cannot approve
a baseline or replace the director's scoped future no-write commitment.

### Promotion and rollback

Before production prepare, retain and independently review the final-source
manifest/assets, exact HOLD/route and full workflow plan (including registry
population), all request bytes, and the apply/rollback paths. Complete the live
read-only rehearsal. Only then obtain the bounded direct-owner/host/workflow-admin
no-write window for the concrete operation. Prepare, execute and restore are
separate accepted manual dispatches; no automated chaining is supplied.

Before any stage claim, code rollback is a revert. After a claim or journal
exists, retain the exact source, private request, claims, unit records and OCI
archives. For ambiguous execution, first inspect actual DB BEFORE/AFTER state,
remote/local journal equality and prior host/backend drain. Restore workflows
only via a separately accepted restore request for that observed outcome. If the
six grants must be revoked, use the same retained manifest with an independently
accepted `operation:"rollback"` scope and a new prepare/execute/restore sequence.
Neither code revert nor request deletion is database rollback. HOLD remains in
force throughout this launcher; service activation and the one authorized
read-only pilot require their own reconciled subsequent steps.
