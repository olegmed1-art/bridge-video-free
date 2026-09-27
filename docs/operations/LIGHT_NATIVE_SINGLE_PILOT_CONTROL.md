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
launch, observe and restore. It does not create approvals or an execution
workflow. Cleanup is permitted after window expiry and never resubmits work.
The live effective systemd properties and whole-path recovery must still be
verified before production activation. Permission-stage production binding is
not enabled by this change.

Local verification includes isolated stdlib imports, immutable request/package
binding, nonrenewing agreements, queue-zero baseline handoff, no-effect launch
gating, lost acknowledgements, configuration/process drift and restoration
readback. CI additionally exercises root-owned filesystem metadata and the
existing disposable PG18 native delivery regression.
