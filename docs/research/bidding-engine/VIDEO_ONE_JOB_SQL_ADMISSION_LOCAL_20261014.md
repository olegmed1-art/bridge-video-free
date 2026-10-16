# One-job staging admission v1 — local candidate, 2026-10-14

This is an unapplied engineering candidate atop combined queue commit `83f59759`.
It is not a production admission, a migration ready for installation, or a
claim that the existing `bridge_school_worker` principal is restricted to one
job. No role, grant, credential, resident entrypoint, or production state was
changed.

The draft SQL is `database/proposals/0061_one_job_staging_admission_v1.sql`,
outside the migration runner. The adapter
`database/video_one_job_staging_admission.py` is opt-in and unused by the
resident. It calls SQL admission before candidate inserts, holds the live job
row lock across the staging transaction, and attests the replay receipt before
commit. An expired/revoked lease, different attempt, source
fingerprint, job, batch, subset contract, or manifest fails closed. The
attestation stores the immutable receipt identity separately from each exact
lease and attempt. After a crash between staging commit and finish, a new
attempt may rebind the same deterministic receipt only after a dedicated
approver authorizes the new live lease and the current source revision passes
again. The API uses 0060's approver capability, rejects worker membership,
and binds the exact job, lease token, worker key, attempt, source fingerprint
and expiry. An absent, replayed, expired or revoked approval fails closed.
A different job, admission, run, source,
revision, or payload hash is rejected. The finish function requires the
current attempt attestation and the 0060 post-work observation.

The batch and subset manifest continue to say `SHADOW_REVIEW_ONLY` and
`database_persistence_allowed=false`. A separate one-job admission record is
the explicit exception. Only its versioned finish function can replace that
job's output with `REPLAY_CANDIDATE_STAGING_ONLY`, `true` for technical database
persistence, `false` for canon promotion, and `NOT_PUBLISHED`. The proposal
replaces `video_job_output_shadow_check` with a versioned constraint that
accepts the old shadow form and the narrow staging form only on `REVIEW_READY`.
The new finish function makes its own atomic transition and uses the existing
0060 post-work observation function; it never presents a false shadow output
to the old finish function. The original 0056/0060 finish contracts are
unchanged. This one-job finish never releases pending sibling jobs;
`released_jobs=0` even for an admitted canary.

After independent PostgreSQL review found that a revision-only fake terminal
manifest could pass, the finish path now calls
`assert_one_job_terminal_evidence_v1` before consuming the post-work observation
or updating the job. Its validation block matches the complete 0058 terminal
gate: canonical manifest and receipt SHA-256, distinct Drive IDs, source and
folder identity, both artifact locators/MIME types/positive sizes/hashes, PASS
readback flags, and hash-bound revision metadata. The terminal artifact
manifest and receipt retain their original shadow flags; the separately
admitted job output records the staging exception.

Activation blockers and minimum access changes for review:

1. The 0060 objects are owned by `video_subset_owner` with private EXECUTE and
   table ACLs. Installation must use an authorized owner context; the local
   draft obtains no membership. The owner of `video_queue.job` must replace
   `video_job_output_shadow_check` with `video_job_output_mode_v1_check` after
   checking existing rows. Transfer ownership of the four new tables and
   seven new functions to `video_subset_owner`; revoke PUBLIC defaults. Give
   `EXECUTE` on `assert_one_job_staging_v1(uuid,uuid,uuid,text,integer,text)`
   and `attest_one_job_staging_receipt_v1(uuid,uuid,uuid,text,integer,text,uuid,uuid,uuid,text)`
   **only** to `bridge_school_worker_principal`, and `EXECUTE` on
   `finish_one_job_staging_v1(uuid,uuid,uuid,text,integer,text,uuid,uuid,jsonb)`
   **only** to `video_subset_worker_v2`. The internal
   `check_one_job_staging_scope_v1` gets no runtime EXECUTE grant. Existing
   `video_queue` schema USAGE is inherited by the writer from
   `bridge_school_worker`; no queue-worker membership is granted to it.
   The complete review-only owner and EXECUTE delta, including the approver
   and revoker, is in `VIDEO_ONE_JOB_ACTION_TIME_ACL_DELTA_20261015.md`.
   The proposed additional function-owner reads are:

   ```sql
   GRANT SELECT (analysis_candidate_id,analysis_run_id,source_id,candidate_type,status,
       promotion_status,method_version,payload_hash,stable_key,payload)
       ON public.analysis_candidate TO video_subset_owner;
   GRANT SELECT (analysis_run_id,parameters_snapshot)
       ON public.analysis_run TO video_subset_owner;
   GRANT SELECT (source_id,canonical_locator)
       ON public.source TO video_subset_owner;
   GRANT SELECT (analysis_run_id,output_entity_id,output_entity_type,output_role,
       status,artifact_version_id,publication_id)
       ON public.analysis_run_output TO video_subset_owner;
   GRANT EXECUTE ON FUNCTION video_queue.canonical_json_text(jsonb)
       TO video_subset_owner;
   ```

   These are review-only statements, not applied changes. Before installation,
   confirm whether any columns already have effective grants and omit redundant
    grants. The approver-only API is a local proposal; its EXECUTE is
    reserved for `video_subset_approver` in the separate ACL delta.
2. Current `bridge_school_worker` has direct DML on result and candidate
   tables. Therefore this adapter is a cooperative transaction check, not a
   database-enforced one-job writer boundary. For a strict boundary, the
   production worker's broad INSERT/UPDATE privileges on the involved public
   tables must be revoked (including column grants), and persistence must move
   through a narrow guarded `SECURITY DEFINER` writer API or a separate writer
   principal with equivalent table triggers that check immutable admission and
   current lease in each write transaction. Any such change requires an exact
   ACL inventory and separate rollout; the draft makes none of those changes.
3. `persist_completed_drive_job` currently commits the result, source identity,
   checkpoint, and candidate staging in several connections. Integrating the
   draft requires explicit transaction ownership for the candidate insert and
   receipt attestation, a fresh committed readback afterward, and an explicit
   versioned finish call. Do not use the existing Python staging capability as
   approval. The result writes made before candidate staging also need the
   strict writer boundary above before this is production safe.
 4. The previous SQL candidate received an independent disposable PostgreSQL
    review, but this approver addition has not been executed by PostgreSQL. A
   disposable PG 18 database with migrations through 0060, synthetic sources,
   and actual writer/queue/approver login tests is required. Test wrong job, batch,
   manifest, revision, lease, attempt, expiry, revocation, receipt hash/link,
   same-receipt/new-lease retry, conflicting reuse, post-work observation,
   staging constraint, and transaction rollback. Compare
   `pg_catalog` effective ACLs before considering any migration.

Ten local stdlib tests check adapter ordering, lost lease, invalid scope,
receipt identity/retry behavior under a fake SQL boundary, and the presence
of the separate SQL role, approver and finish paths. They also compare the terminal
validation block byte-for-byte with 0058 and require explicit negative checks
for missing IDs, hashes, receipts, and source binding. The parameterized
`tests/sql/one_job_terminal_evidence_negative.sql` validates a complete
synthetic fixture, then requires rejection of five incomplete variants,
including a rehashed revision-only fake. The new
`tests/sql/one_job_approver_negative.sql` adds exact-lease, replay, and
revocation negatives under a dedicated approver login. No run of the new
approver SQL is claimed here. A local PG 18.6 runtime was
found, but loopback client connection was denied by the host with Windows
error 10013; the temporary server process exited and was not used for tests.
