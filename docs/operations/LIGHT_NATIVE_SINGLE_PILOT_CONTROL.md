# Light native single-pilot control

Status: dormant controller, not production activation. The existing enabled
Light service retains its original HOLD unit, drop-in and environment files.

The accepted source package includes the immutable runtime and every supervisor
helper. Staging checks runtime DB identity and service-profile Codex login,
then performs a read-only listing of the exact Cloud environment. Only a
private output fingerprint is retained; no task is submitted by staging.
The Cloud environment's repository mapping is separately verified.

The operational sequence is deliberately split around real task creation:

1. Review the exact source, package, permission manifest, task target/head and
   rollback. Obtain one finite owner no-write window covering direct owner SQL,
   host administration, workflow administration/reruns and main pushes.
2. Before creating a task, retain a full HOLD/empty-queue baseline under that
   accepted window. Baseline creation is one-time; an existing or partial
   namespace requires reconciliation, not automatic deletion or reuse.
3. Use the real shared admission pipeline for one READ_ONLY REPOSITORY_AUDIT,
   with no repair, mutation, paid action, merge or deploy. Retain the original
   AUTOPILOT role and native configuration before any scoped changes. Publish
   the actual discovery PR and independently accept the resulting owner
   preflight and permit. Never substitute a fixture or fabricated publication.
4. Bind the accepted request to the baseline, same window and exact package.
   The queue is now intentionally nonempty, so binding checks service-only
   HOLD identity and protected files plus the accepted task preflight.
5. Dispatch one root transient supervisor with a fixed ExecStopPost restoration
   hook. It arms recovery before stopping the old service, proves process and
   DB backend drain, and starts the pilot behind a root-owned HOLD gate.
6. While the gate blocks all loader/DB/provider effects, verify PID1's actual
   command, both environment files, environment flags, duration and sandbox.
   Recheck the operator window, protected files and current main, then release
   PILOT once. A lost launch acknowledgement is never retried.
7. On terminal marker, quarantine, timeout or supervisor failure, deny admission,
   stop and drain the owned pilot, and restore the unchanged persistent HOLD
   service. A durable service-only receipt supports idempotent restore readback.
8. Independently read DB/provider evidence: exactly one task/receipt, correct
   terminal result, unchanged goal, and no successor or repair task. A journal
   marker and successful service restoration alone are not pilot acceptance.
   Unknown provider/DB outcomes retain the ledger and remain quarantined.

`ops.light_native_pilot_control` generates fixed SSH bootstrap data for baseline,
launch, observe and restore. The fixed manual owner and control workflows
require accepted source/package/payload digests. They do not create approvals.
Private permit/request bytes remain on the host: prepare-retained loads only
the accepted permit digest from the fixed intake directory, and launch-retained
uses the separately accepted request digest. Cleanup is permitted after window expiry and never resubmits work.
The live effective systemd properties and whole-path recovery must still be
verified before production activation. The fixed permission-stage workflow profile is pinned to its reviewed bytes;
that capability alone authorizes no grant without the accepted manifest,
recovery evidence and finite owner window.

Local verification includes isolated stdlib imports, immutable request/package
binding, nonrenewing agreements, queue-zero baseline handoff, no-effect launch
gating, lost acknowledgements, configuration/process drift and restoration
readback. CI additionally exercises root-owned filesystem metadata and the
existing disposable PG18 native delivery regression.

The owner intake performs the existing shared register, probe, materialize,
claim, prepare-dispatch and claim-outbox RPCs in one transaction. Any unexpected
queue selection rolls back the transaction; it does not clear or bypass the
historical queue. Original native configuration and AUTOPILOT repair policy are
retained before the first mutation. Authenticated workflow/run/job checks apply
at effect boundaries and before commit, in addition to the finite owner window.
Broker publication is one attempt with durable intent; uncertain outcomes are
reconciled using actual GitHub evidence, never blindly retried. Public workflow
output contains only digests and selected identifiers, not private records.

After service restoration, a fresh Cloud status/diff must match the terminal
DB evidence. Conditional control restoration verifies the exact terminal,
unchanged goal and absence of successors, then restores only the saved native
configuration and repair policy. UNKNOWN leaves the ledger in place. A known
BLOCKED result is not success: its work item may be scheduled again, so the
persistent service stays HOLD until that specific lane is reconciled before
any later activation. No queue, task, receipt or journal is deleted.

The new disposable intake fixture exercises actual admission/native RPCs and
control restoration on localhost bridge_school_ci only. It is test evidence,
not a production task or permission receipt. Live pilot acceptance and the
live permission-stage execution remain separate outstanding requirements.

### Private grant request candidate assembly

The existing read-only stage-rehearsal workflow has an explicit `candidate`
choice. Its externally accepted input is canonical `grant_request_candidate`
JSON. It binds source, staged runtime digest, four accepted recovery-asset
identifiers, the exact workflow plan, stage, accepted scope/head/prior-unit
references, request ID and Agreement. The fixed root command reads the retained
release `before.json` and `staged.json`, checks the live HOLD, and constructs the
private scope without publishing HoldIdentity or credentials.

With `agreement: null`, only a prepare-scope observation is returned; no grant
request is submitted. This allows scope review before asking for the future
operator no-write interval. After that interval is explicitly accepted, the
operator supplies the Agreement bound to that scope. The command creates a
private request once, verifies local and OCI unit receipts for resumed stages,
and retains the request off-host. It emits only digests and `approved: false`.
A separate authenticated stage dispatch must accept that exact resulting
request digest. Assembly neither executes a grant nor selects an observed
checkpoint head, outcome, Agreement or omitted prior unit for the operator.
A lost acknowledgement requires reconciliation, never blind resubmission.

If read-only rehearsal refuses database drain, it may additionally emit bounded
counts classified as light/owner/other, client/other, transaction state and
coarse age. It emits no query text, address, application name or PID and has no
admission authority. The drain rule and nonrenewing time limit stay enforced.

The fixed stage/rehearsal launcher uses one nonrenewing 100-second authenticated
run binding, constructed before private request import. It covers SSH/OCI
preparation, the bounded host exchange and independent final readback. The host
binding and duplex channel remain 60 seconds; SQL admission retains its existing
shorter bound and PID1 keeps its 100-second cleanup lifetime. A generic caller
cannot supply 100 seconds to the host constructor. Neither successful checks nor
request fetch renew the launcher deadline. Safe refusal categories and total
binding elapsed time distinguish expiry from other transport failures without
printing exception text. The previous 64-second read-only refusal was consistent
with outer expiry but did not establish its cause; live verification is required.


### Controlled executor image restart (G6)

The single pilot now starts only from a fresh local claim and RESERVED DB receipt.
Before the first provider collection, the loader verifies the committed SUBMITTED
ACK against the bound provider journal (same dispatch, request, task ID and prompt).
It fsyncs a create-only private restart intent, closes the DB connection and claim
lock, and execs the exact pinned interpreter and launch gate. No second systemd
unit, provider task, permission window or retry is created.

Reentry must retain the same PID and systemd invocation, a different Python image
nonce, the original permit bytes and monotonic deadline, and the exact DB/journal
binding. A create-only resume record precedes collection. Missing, changed or
unsafe records, a second reentry, expired authority or exec failure quarantine.
The controller's RuntimeMax and final HOLD restoration continue unchanged.

Owner terminal acceptance freshly reads Cloud and DB results and requires the
private startup/intent/resume chain to match the root-retained unit PID/invocation.
A root-retained restart receipt is linked to the terminal digest. Logs alone do
not establish recovery. This tests controlled fresh-memory recovery at a durable
ACK checkpoint; it does not establish arbitrary host, unit or crash recovery.

Validation includes actual os.execv in an isolated child with durable fake DB and
provider ports, proving one creation, one terminal acceptance and the same PID
across fresh Python images. The real Cloud pilot is still required for G6 PASS.
