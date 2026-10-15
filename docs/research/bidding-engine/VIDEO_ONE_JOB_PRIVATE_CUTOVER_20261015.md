# One-job private route: proposed cutover and rollback, 2026-10-15

**Prepared only. No target database, real ACL, resident, CI or deployment was
changed.** The reviewed 0061/0062 SQL and the explicit callable
`universal_video.private_one_job_entrypoint.run_private_one_job` are the
candidate. The explicit one-shot `spool_worker --private-one-job` launcher
now claims only the selected job and uses the existing restricted queue DSN
setting. Ordinary resident polling remains unchanged. The private processor
does not accept a broad writer credential or choose a fallback.

## Action-time prerequisites, in order

1. Obtain independent review of this exact entrypoint commit, then reconcile
   current `main`, migration/ACL state, one-job approval and the target queue
   against primary sources. Verify a recoverable target backup and tested
   restoration before any migration. No unreviewed source or policy drift can
   authorize execution.
2. In a disposable PostgreSQL rehearsal, apply 0061 then 0062 under the
   authorized owner, test the exact queue-only and separate approver logins,
   run the SQL negatives for cross-job, changed bundle, replay, expiry and
   revocation, plus full terminal evidence. Confirm unchanged public result,
   candidate, source identity, checkpoint, outbox and canon row counts.
3. Prepare the action-time ACL delta in
   `VIDEO_ONE_JOB_PRIVATE_RESULT_GATE_20261015.md`. Give the existing
   `video_subset_worker_v2` login EXECUTE on only the three new private
   functions. Do not grant the old public-candidate assertion/attestation or
   finish path to this route. Verify effective inherited table **and column**
   DML, schema and function privileges using `pg_catalog` and a real session;
   the entrypoint repeats these checks on every private queue connection,
   including before-work, heartbeat, retry and finish.
4. Select exactly one job and batch through the reviewed one-shot claim
   procedure. Pass the already-claimed job, batch, lease token, attempt,
   source file, admission ID and source fingerprint explicitly. This
   entrypoint will not claim a different job if the selected claim is absent.
   The approver remains a separate login and must approve the current attempt
   before staging. The one-shot path writes a protected claim receipt for
   that approver, emits a ready notice immediately before staging, then
   waits for a matching local signal. SQL
   still verifies the actual approval; the signal is not authorization.
   No automatic approval capability is added.
5. Invoke `spool_worker --private-one-job <binding.json>` once through the
   explicitly authorized one-shot launch procedure. It uses the existing
   restricted queue DSN and collector, selected job/batch/source/attempt,
   expected database/login and separate approver signal. Supply no broad
   writer DSN. Its
   preflight must pass before Drive processing; every queue connection repeats
   the identity/ACL check immediately before its SQL operation. Accept only a
   matching private receipt, `REVIEW_READY`, `released_jobs=0`, no publication or canon write,
   and a committed readback. Check terminal Drive IDs/hashes and source
   revision again after the run.

## Failure and rollback

- A wrong claim, login, role, database, inherited DML, old-path EXECUTE,
  ambient broad writer DSN or missing new EXECUTE stops **before processing**.
  A changed identity on any later queue connection stops that operation. There is no
  fallback to `persist_completed_drive_job`, the public-candidate route, or
  `process_one_neon`.
- Failure before bundle commit leaves no private row. Failure after bundle
  commit but before finish keeps the immutable row and attempt history;
  retry requires a new lease and approver decision, then may rebind only the
  identical raw bundle. Do not delete the receipt or manually advance the job.
- Before the first private finish, disable new approvals and revoke the three
  private EXECUTE grants to stop this route. After a private finish, keep
  0062 tables and the v2 job-output constraint for readback/recovery; do not
  drop them or attempt to rewrite the finished output. Restore the unchanged
  shadow resident for future jobs only after state reconciliation. No sibling
  job release is part of the canary.

This is a **technical result** canary. The private bundle retains the full raw
embedded master and AI_DONE JSON but does not materialize public
`analysis_candidate` rows. That later teacher/canon staging decision needs a
separate least-privilege design and assurance; it is not an implicit cutover.
