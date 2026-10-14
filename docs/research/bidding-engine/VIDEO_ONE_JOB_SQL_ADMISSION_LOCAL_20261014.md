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
row lock across the staging transaction, and attests the newly inserted replay
receipt before commit. An expired/revoked lease, different attempt, source
fingerprint, job, batch, subset contract, or manifest fails closed. The
attestation stores the exact lease and attempt. The finish function requires
that attestation and the 0060 post-work observation; v1 rejects receipt reuse
across attempts, even when the original candidate is otherwise idempotent.

The batch and subset manifest continue to say `SHADOW_REVIEW_ONLY` and
`database_persistence_allowed=false`. A separate one-job admission record is
the explicit exception. Only its versioned finish function can replace that
job's output with `REPLAY_CANDIDATE_STAGING_ONLY`, `true` for technical database
persistence, `false` for canon promotion, and `NOT_PUBLISHED`. It delegates the
ordinary transition and observation checks to 0060 in the same transaction.
The original 0056/0060 finish contracts are unchanged.

Activation blockers and minimum access changes for review:

1. The 0060 objects are owned by `video_subset_owner` with private EXECUTE and
   table ACLs. Installation must be run by an authorized owner able to create
   the new objects with that owner, or use a reviewed temporary owner SET grant
   that is revoked in the installation transaction. The local draft does not
   create or obtain that membership. A future installer must explicitly set
   `OWNER TO video_subset_owner` for both tables and three functions, revoke
   all default/public grants, and grant only the new `assert`, `attest`, and
   `finish` EXECUTE signatures to `video_subset_worker_v2`. An approver-only
   admission creation/enable/revoke API also needs owner implementation and
   EXECUTE only for `video_subset_approver`; worker must have no admission DML.
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
4. The SQL draft has not been parsed or executed by PostgreSQL here. A
   disposable PG 18 database with migrations through 0060, synthetic sources,
   and actual worker/approver role tests is required. Test wrong job, batch,
   manifest, revision, lease, attempt, expiry, revocation, receipt hash/link,
   duplicate receipt, post-work observation, and transaction rollback. Compare
   `pg_catalog` effective ACLs before considering any migration.

Local stdlib tests check adapter ordering and rollback for lost lease, invalid
scope, and old receipt. They do not prove SQL or production behavior.
