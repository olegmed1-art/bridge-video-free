# One-job action-time ACL proposal, 2026-10-15

**Review-only; no SQL below has been applied.** The 0061 SQL is still outside
the migration runner. This delta assumes an authorized installer with ownership
of the new objects and `video_queue.job`. It creates no login, credential,
membership, secret, or production session.

The new approver API uses the existing 0060 `video_subset_approver` capability.
Its caller must not have either `video_subset_worker_v2` or
`bridge_school_worker` membership. The caller approves the exact live job,
lease token, worker key, attempt number, source fingerprint, and an expiry no
later than both the lease and subset approval. Every retry needs a fresh
approval. Revocation is terminal for that admission. The staging assertion,
receipt attestation, and finish paths all use the exact attempt approval.

If installation is ever authorized, review these object-owner and EXECUTE
changes together with the 0061 proposal and actual `pg_catalog` privileges:

```sql
ALTER TABLE video_queue.one_job_staging_admission_v1 OWNER TO video_subset_owner;
ALTER TABLE video_queue.one_job_staging_lease_approval_v1 OWNER TO video_subset_owner;
ALTER TABLE video_queue.one_job_staging_receipt_v1 OWNER TO video_subset_owner;
ALTER TABLE video_queue.one_job_staging_receipt_attempt_v1 OWNER TO video_subset_owner;
ALTER FUNCTION video_queue.approve_one_job_staging_attempt_v1(
    uuid,uuid,uuid,uuid,text,integer,text,timestamptz,text) OWNER TO video_subset_owner;
ALTER FUNCTION video_queue.revoke_one_job_staging_v1(uuid) OWNER TO video_subset_owner;
ALTER FUNCTION video_queue.check_one_job_staging_scope_v1(
    uuid,uuid,uuid,text,integer,text,text) OWNER TO video_subset_owner;
ALTER FUNCTION video_queue.assert_one_job_staging_v1(
    uuid,uuid,uuid,text,integer,text) OWNER TO video_subset_owner;
ALTER FUNCTION video_queue.attest_one_job_staging_receipt_v1(
    uuid,uuid,uuid,text,integer,text,uuid,uuid,uuid,text) OWNER TO video_subset_owner;
ALTER FUNCTION video_queue.assert_one_job_terminal_evidence_v1(
    uuid,jsonb) OWNER TO video_subset_owner;
ALTER FUNCTION video_queue.finish_one_job_staging_v1(
    uuid,uuid,uuid,text,integer,text,uuid,uuid,jsonb) OWNER TO video_subset_owner;

REVOKE ALL ON FUNCTION video_queue.approve_one_job_staging_attempt_v1(
    uuid,uuid,uuid,uuid,text,integer,text,timestamptz,text),
    video_queue.revoke_one_job_staging_v1(uuid),
    video_queue.check_one_job_staging_scope_v1(uuid,uuid,uuid,text,integer,text,text),
    video_queue.assert_one_job_staging_v1(uuid,uuid,uuid,text,integer,text),
    video_queue.attest_one_job_staging_receipt_v1(
        uuid,uuid,uuid,text,integer,text,uuid,uuid,uuid,text),
    video_queue.assert_one_job_terminal_evidence_v1(uuid,jsonb),
    video_queue.finish_one_job_staging_v1(
        uuid,uuid,uuid,text,integer,text,uuid,uuid,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION video_queue.approve_one_job_staging_attempt_v1(
    uuid,uuid,uuid,uuid,text,integer,text,timestamptz,text),
    video_queue.revoke_one_job_staging_v1(uuid) TO video_subset_approver;
GRANT EXECUTE ON FUNCTION video_queue.assert_one_job_staging_v1(
    uuid,uuid,uuid,text,integer,text),
    video_queue.attest_one_job_staging_receipt_v1(
        uuid,uuid,uuid,text,integer,text,uuid,uuid,uuid,text)
    TO bridge_school_worker_principal;
GRANT EXECUTE ON FUNCTION video_queue.finish_one_job_staging_v1(
    uuid,uuid,uuid,text,integer,text,uuid,uuid,jsonb)
    TO video_subset_worker_v2;
```

The 0061 proposal itself revokes PUBLIC table privileges for all four new
tables. No runtime role gets direct DML on them. Internal scope and terminal
gate functions get no runtime EXECUTE grant. Before any installation, verify
no PUBLIC or inherited grant defeats these boundaries, that the approver login
has neither worker membership, and that the writer login has no approver or
queue-worker membership. The 0060 schema USAGE and internal function grants
must also be verified against the effective ACLs. Existing owner read grants
and `canonical_json_text` EXECUTE needed by receipt attestation are listed in
`VIDEO_ONE_JOB_SQL_ADMISSION_LOCAL_20261014.md`.

**Unresolved strict writer boundary.** `bridge_school_worker_principal`
inherits `bridge_school_worker`, which in turn inherits `bridge_school_app`
and `bridge_school_reader` under migration 0005. It has broad direct
`INSERT`/`UPDATE` on public result, ingestion, evidence, source, student and
event tables; migration 0049 adds `analysis_candidate` DML. Revoking only
from the principal would not remove inherited capability. The current
one-job Python adapter is a cooperative transaction check, not an enforceable
one-job write fence. The required future delta is a dedicated writer login
without `bridge_school_worker` or app membership, replacement of its direct
result/candidate writes with narrow guarded `SECURITY DEFINER` persistence
functions (or equivalent database triggers), and a change to the writer
identity check in 0061. Those functions must bind **each** write transaction
to the same admission, live lease, source revision and receipt. No such
principal or functions exist in this candidate, so no direct-DML revoke or
production role switch is proposed as executable SQL now.

Action-time preflight must inventory `pg_roles`, `pg_auth_members`, schema,
function, table and column ACLs from `pg_catalog`; include inherited rights
and existing writer calls in `video_result_persistence.py`, source identity,
checkpoint and candidate paths. Then test a dedicated approver, wrong/no
role, worker-role conflict, exact lease, expiry, replay and revocation on a
disposable database. `tests/sql/one_job_approver_negative.sql` covers the
synthetic exact-lease and terminal-revocation cases. Only after that and
independent I2 review should a migration or worker cutover be considered.
