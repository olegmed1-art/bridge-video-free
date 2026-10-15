# One-job private result gate - local candidate, 2026-10-15

**Status: unapplied, unconnected to the resident, PostgreSQL review pending.**
This candidate follows the reviewed 0061 approver gate. It does not claim
that the current public result/candidate writer is least-privileged.

The existing `persist_completed_drive_job` performs writes in separate
transactions: `video_result_persistence.py` inserts or updates result,
analysis, evidence, source, event and outbox rows across 22 public tables;
`source_identity_persistence.py`, `run_checkpoint_persistence.py`, and
`video_candidate_persistence.py` add source identity, checkpoint, candidate
and replay-output rows. `bridge_school_worker_principal` inherits broad DML
from `bridge_school_worker` and `bridge_school_app`. A preflight assertion
around candidate insertion cannot fence the earlier public writes.

The proposed one-job route deliberately skips **all** those public writers.
After verified Drive download of the same AI_DONE and embedded master JSON,
`database/video_one_job_private_result.py` sends both original JSON texts and
the AI_DONE Drive ID to `stage_one_job_private_result_v1` on the existing
`video_subset_worker_v2` queue connection. The SQL checks 0061's exact
approved job, lease, owner, attempt, source fingerprint, active subset and
consumed source observation. It also checks embedded master SHA-256 against
AI_DONE, job and revision identity, source file/name/MIME/size/folder, and
the approved source fingerprint. One immutable private row and one exact
attempt receipt are committed atomically. A separate connection confirms the
committed hash. The finish gate also matches the staged AI_DONE ID/hash and
its PDF ID/hash to the independently verified terminal artifact evidence.
Same-bundle retry needs fresh attempt approval; changed
bundle, changed receipt, same-attempt/new-token, wrong job, expiry or
revocation fails closed. The proposal never writes public result, candidate,
source identity, checkpoint, outbox, student or canon tables.

`PrivateResultStagingHandoff` can be passed explicitly to `process_claim`;
its processor still runs in the existing shadow environment with the broad
writer DSN removed, and the opt-in route rejects any ambient broad writer
DSN before processing. The 0060 subset adapter then uses its separate
`finish_private_job` call. `process_one_neon` never constructs this handoff.
`finish_one_job_private_result_v1` is a separate, versioned queue transition.
It requires the committed private bundle, current attempt, full 0058
terminal evidence and 0060 post-work source observation. Its output mode is
`PRIVATE_RESULT_STAGING_ONLY`; it never claims public analysis candidates
were persisted, never authorizes publication or canon promotion, and never
releases siblings. The old `REPLAY_CANDIDATE_STAGING_ONLY` path remains in
0061 for comparison but must receive **no** runtime EXECUTE grant for a strict
one-job cutover. The existing resident is unchanged and cannot invoke this
route automatically.

Review-only ACL delta after installing 0061 and 0062 in an isolated database:

```sql
ALTER TABLE video_queue.one_job_private_result_v1 OWNER TO video_subset_owner;
ALTER TABLE video_queue.one_job_private_result_attempt_v1 OWNER TO video_subset_owner;
ALTER FUNCTION video_queue.stage_one_job_private_result_v1(
    uuid,uuid,uuid,text,integer,text,uuid,text,text,text) OWNER TO video_subset_owner;
ALTER FUNCTION video_queue.read_one_job_private_result_v1(
    uuid,uuid,uuid,text,integer,text,uuid) OWNER TO video_subset_owner;
ALTER FUNCTION video_queue.finish_one_job_private_result_v1(
    uuid,uuid,uuid,text,integer,text,uuid,uuid,jsonb) OWNER TO video_subset_owner;
GRANT EXECUTE ON FUNCTION video_queue.canonical_json_text(jsonb)
    TO video_subset_owner;
REVOKE ALL ON video_queue.one_job_private_result_v1,
    video_queue.one_job_private_result_attempt_v1 FROM PUBLIC;
REVOKE ALL ON FUNCTION video_queue.stage_one_job_private_result_v1(
    uuid,uuid,uuid,text,integer,text,uuid,text,text,text),
    video_queue.read_one_job_private_result_v1(
    uuid,uuid,uuid,text,integer,text,uuid),
    video_queue.finish_one_job_private_result_v1(
    uuid,uuid,uuid,text,integer,text,uuid,uuid,jsonb) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION video_queue.assert_one_job_staging_v1(
    uuid,uuid,uuid,text,integer,text),
    video_queue.attest_one_job_staging_receipt_v1(
    uuid,uuid,uuid,text,integer,text,uuid,uuid,uuid,text)
    FROM bridge_school_worker_principal;
REVOKE EXECUTE ON FUNCTION video_queue.finish_one_job_staging_v1(
    uuid,uuid,uuid,text,integer,text,uuid,uuid,jsonb)
    FROM video_subset_worker_v2;
GRANT EXECUTE ON FUNCTION video_queue.stage_one_job_private_result_v1(
    uuid,uuid,uuid,text,integer,text,uuid,text,text,text),
    video_queue.read_one_job_private_result_v1(
    uuid,uuid,uuid,text,integer,text,uuid),
    video_queue.finish_one_job_private_result_v1(
    uuid,uuid,uuid,text,integer,text,uuid,uuid,jsonb)
    TO video_subset_worker_v2;
```

The SQL above is **not applied**. It is not sufficient by itself: inspect
effective inherited table/column/function ACLs, `pg_auth_members`, and
`session_user` in the target environment. The queue login must have no
`bridge_school_worker`, `bridge_school_app`, or approver membership and no
direct public result DML. Do not pass the old broad writer DSN to this path.
The `video_subset_owner` must have its existing 0060 owner privileges and
EXECUTE on `canonical_json_text`; compare those grants in `pg_catalog`.

Compatibility and recovery gate:

- The private row stores both full raw JSON texts and their Drive-bound
  SHA-256 evidence; the original Drive artifacts remain independently
  available. The
  one-job path does not materialize public `analysis_candidate` rows, so it is
  a **technical result canary**, not a completed teacher/canon staging run.
- The separate opt-in callable `run_private_one_job` constructs this handoff
  from an exact already-claimed job and checks queue identity/ACL before and
  during staging. The resident constructs none of these and uses its old
  shadow finish. See `VIDEO_ONE_JOB_PRIVATE_CUTOVER_20261015.md` for the
  proposed action-time sequence and rollback; no production claim is made.
- The 0061 approver expiry cannot outlive the **current** lease at approval
  time. A long processor can heartbeat beyond that expiry, so the separate
  approver must authorize the exact live attempt shortly before private
  staging. No automatic approval or lease-renewal authority is added here;
  missing or expired approval causes retry, never a partial result write.
- Before any cutover, run `tests/sql/one_job_private_result_negative.sql` with
  a full synthetic valid fixture, then the closed-scope script separately for
  revoked admission, expired attempt approval and expired lease. Test actual
  queue-only login, lack of public DML, cross-job, replay and same-bundle
  retry, terminal evidence, and no sibling release. 0062 has not been parsed
  or executed by PostgreSQL on this host.
- Rollback before first private output: revoke the three new EXECUTEs and
  leave 0062 tables in place. After any private output exists, keep its rows
  and v2 output constraint for read/recovery; disable new approvals/grants
  and return the resident to the unchanged shadow path. Never drop private
  data or restore v1 constraint until all v2 rows have been safely reconciled.

This remains `ASSURED` work: independent I2 PostgreSQL and architecture
review is required before an action-time ACL or queue integration decision.
