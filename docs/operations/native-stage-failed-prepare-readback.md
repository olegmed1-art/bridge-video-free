# Failed native prepare: observational readback

On 2026-09-27 prepare run 36310581069, attempt 1, job 108595637618
failed with RPC_TIMEOUT on source 4bb6d2e082773d4119a1003b71e31e60d1277613.
No execute stage or Cloud pilot was dispatched. A timeout does not establish
whether the private prepare claim, unit receipt, or checkpoint was retained.
The consumed request must not be retried and no checkpoint may be inferred.

The rehearsal workflow's explicit `inspect` action accepts a canonical,
independently hashed diagnostic request binding current reviewed main, the old
source, old accepted request digest, and failed run/attempt/job. It verifies the
ended run and successful contract job, then reads the existing root-owned
prepare scope and private OCI checkpoints twice. Inspection requires the Light
service/config fingerprint to remain unchanged, but opens no database connection
and does not claim database maintenance admission.

The diagnostic input itself may be imported create-only into the private request
namespace and short-lived supervised diagnostic processes are started. Existing
claims, stage-unit receipts, journals, locks and OCI objects are never created,
repaired, deleted or overwritten. Missing locks or malformed private paths fail
closed. Inspection does not pause/enable workflows, grant permissions, or resume
any stage. Journal event labels describe historical records, not current workflow
state. Public output contains bounded metadata and digests, never private bytes.

Relations `no_head`, `exact_pair`, `local_uncheckpointed_suffix` and
`mismatch_or_unknown` are observations only; `resume_authorized` is always false.
Any recovery action needs separate review of the actual evidence and current
source. An expired coordination window is never renewed by inspection. A new
source invalidates the old execution handoff even if the observed pair is exact.

Validation: focused unit tests, independent I2 review and required CI before
merging. Rollback is reverting this diagnostic-only change; it cannot reverse
or reset an old stage and must not be used for that purpose.
