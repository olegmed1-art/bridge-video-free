# Bound offline journal checkpoints

2026-09-26 — ASSURED / I2. Callable recovery component, no live CLI or deployment.

The operation journal and pause journal must belong to the same operation, not
merely to the same workflow plan. MaintenanceExecutor now includes the digest of
its complete BOUND scope (including original run identity) in the pause journal's
first PLAN record. A different run with the same plan cannot supply that journal.
Standalone WorkflowPause callers remain compatible when no operation binding is
provided. Legacy unbound executor journals are deliberately refused, never
rewritten. No production operation journals have been created by this rollout;
if a legacy journal is later discovered, preserve it for separate reconciliation.

`capture(operation_path, pause_path)` opens both real Journals and holds both
exclusive locks. An active executor prevents capture. Each journal is limited to
4 MiB and 1,024 records before full loading; each record is at most 256 KiB; the
canonical archive is at most 16 MiB. Both complete chains and exact canonical
record bytes are checked against disk twice, with scope/plan/source binding.
Trusted privileged storage writers still require coordination: advisory locks do
not exclude an administrator. No inconsistent tail is repaired or discarded.

`restore(data, expected_digest, expected_scope, parent)` requires independently
accepted digest and scope. It validates the whole archive before creating a new
private digest-named directory under an existing private parent. Exact records,
files and directories are fsynced; both real Journals are reopened and compared.
A RECOVERY_ONLY marker is written after verification. Existing output, partial
output and uncertain failures are retained and cannot be overwritten by retry.
There is no cleanup, publication, journal replacement or service activation.

The checkpoint contains confidential HOLD identity. It must remain in approved
private storage and must never become a public Actions artifact, log or PR body.
The digest proves bytes, not authenticity, freshness or completeness relative to
later external actions. A previous snapshot must never justify replay or enable.
The separately reviewed recovery runtime must establish latest accepted evidence,
remote/DB outcomes, exact source/manifests, fresh run and independent reconciliation.
The real executor still replays semantic history and refuses a repeated session.

This closes offline pair format/reconstruction only. It does NOT provide the
synchronous off-VM durability barrier needed before GitHub/SQL mutations, export
actual Light journals, archive the required source/manifests, or implement private
retention and latest-checkpoint selection. The synthetic OCI probe remains a
separate channel proof. Production maintenance remains dormant.

Verification includes lost-session restoration without SQL replay, cross-operation
pair rejection despite identical plans, active locks, corruption, bounds, private
paths, legacy binding refusal and interrupted restore retention. GitHub/host/DB
coordination in unit tests is explicitly simulated. Database CI also runs the
existing PG18 executor rehearsal from the verified source bundle.

Rollback is source revert before any live maintenance; preserve all archive and
journal evidence. Reverting cannot undo or reconcile a started operation.
