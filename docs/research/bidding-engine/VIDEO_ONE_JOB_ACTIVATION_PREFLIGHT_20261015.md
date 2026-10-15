# One-job private route: activation preflight draft, 2026-10-15

**Prepared, not executed.** This is a read-only inventory and a conditional,
unapplied ACL proposal for the independently tested `cbf613d4` candidate.
The successful disposable PostgreSQL review is not evidence of production
schema, roles, backup, service image or live queue state.

## Target and exact control path

- The documented compute environment is the Oracle universal-video sidecar.
  Its native unit is `deploy/oracle-universal-video/universal-video.service`,
  running `universal_video.spool_worker`; a Dockerfile also exists. The
  *actual* host, service type, installed image/commit and runtime state have
  not been read. The resident package now exposes an explicit one-shot
  `spool_worker --private-one-job <binding.json>` path; ordinary polling does
  not select it. Do not enable `UNIVERSAL_VIDEO_SUBSET_SYNC` as a substitute.
- The last documented Neon target is project `misty-poetry-18012774`, branch
  `br-aged-mud-b1i64914`, endpoint `ep-noisy-pine-b1pe30sf`, database
  `neondb`. This is historical, not a verified current target. Its
  `neondb_owner` connector identity is an installer, not a worker login.
  The intended queue capability is `video_subset_worker_v2`; the separate
  approver capability is `video_subset_approver`; observations use the
  separate `video_subset_collector` login. Actual login names are unknown.
- The fixed-subset contract requires an existing finalized batch and live
  subset approval. A reviewed one-job selector must call
  `video_queue.subset_claim_job(batch_id, worker_key, lease_seconds,
  processing_profile, algorithm_revision)` exactly once under the queue
  login. This **mutates** the queue and returns the job, lease and attempt.
  No read-only query can manufacture or reserve that claim.
- A separate approver login must call
  `approve_one_job_staging_attempt_v1(admission_id, lease_approval_id,
  job_id, lease_token, worker_key, attempt_count, source_fingerprint,
  expires_at, approval_reference)` for that exact current lease. The
  function creates the one-job admission on first approval and binds the
  source fingerprint from the finalized subset/reservation. Expiry cannot
  exceed the current lease or subset approval. Approval near private staging
  is coordinated with the long processor by an exact local signal after the
  approver's separate SQL call. That signal is only a wake-up hint: 0062
  verifies the actual approval before staging. A retry needs a fresh approval.

The one-shot launcher uses the existing restricted queue DSN setting and
collector binding. Its protected binding JSON has exactly these fields:
`expected_database`, `expected_login`, `expected_batch_id`, `expected_job_id`,
`expected_source_file_id`, `expected_attempt_count`, `admission_id`,
`source_fingerprint`, `worker_key`, `claim_receipt_path`, and
`approval_signal_path`. Both paths are absolute. The claim receipt is written
once with mode 0600 after the exact claim commits; it contains the lease token
and attempt for the separate approver. Immediately before staging, the worker
writes a `<approval_signal_path>.ready` notice with the same binding. The
approver waits for that notice, uses its own login to call 0061 for the exact
live claim, then writes a restricted JSON signal with exactly `admission_id`, `job_id`,
`lease_token`, `worker_key`, `attempt_count`, `source_fingerprint`, and the
new `lease_approval_id`. The worker checks that signal just before staging;
an absent signal times out without a private write. No approver credential or
automatic approval lives in the resident. The one-shot path holds the shared
workload lock and uses the existing processor, heartbeat, collector and 0062
finish. It commits a claim only if 0060 returns the expected job, batch,
source and attempt; mismatch rolls the claim transaction back. Pre-existing
claim, ready or approval-signal files block a new launch.
Before a real launch, verify that ordinary resident queue polling is
quiesced for the target batch and cannot claim a sibling when this one-shot
lock is released. The shared lock serializes concurrent work but does not
disable later polling. Reconcile the actual service state and restore its
previous behavior only after the one-job result is inspected.

## Minimal read-only SQL inventory

Run this only after an authorized operator supplies the independently
verified target and separate **existing** restricted queue, collector and
approver connections. Run it once per login in a read-only transaction. Do not use an
owner or broad-writer DSN to stand in for either login. Save the result with
the target endpoint, database, session login, timestamp and source commit;
compare the two sessions. The query makes no claim, approval or ACL change.

```sql
BEGIN TRANSACTION READ ONLY;
SELECT clock_timestamp() AS observed_at, current_database() AS database_name,
       session_user AS login, current_user AS active_role,
       current_setting('server_version_num') AS server_version_num,
       has_schema_privilege(session_user,'video_queue','USAGE') AS queue_usage;

SELECT r.rolname, r.rolsuper, r.rolcreatedb, r.rolcreaterole,
       r.rolreplication, r.rolbypassrls,
       pg_has_role(session_user,r.rolname,'MEMBER') AS member
  FROM pg_catalog.pg_roles AS r
 WHERE r.rolname IN ('video_subset_owner','video_subset_worker_v2',
       'video_subset_approver','video_subset_collector',
       'video_subset_admitter','bridge_school_worker','bridge_school_app')
 ORDER BY r.rolname;

SELECT migration_key, checksum, applied_at
  FROM public.schema_migration
 WHERE migration_key IN ('0059_universal_video_terminal_size_guard',
                         '0060_fixed_subset_admission')
 ORDER BY migration_key;
SELECT to_regclass('video_queue.one_job_staging_admission_v1') AS admission_table,
       to_regclass('video_queue.one_job_staging_lease_approval_v1') AS lease_approval_table,
       to_regclass('video_queue.one_job_private_result_v1') AS private_result_table,
       to_regclass('video_queue.one_job_private_result_attempt_v1') AS private_attempt_table;
SELECT conname, convalidated, pg_get_constraintdef(oid) AS definition
  FROM pg_catalog.pg_constraint
 WHERE conrelid=to_regclass('video_queue.job')
   AND conname IN ('video_job_output_shadow_check',
                   'video_job_output_mode_v1_check',
                   'video_job_output_mode_v2_check')
 ORDER BY conname;

WITH expected(signature) AS (VALUES
 ('video_queue.approve_one_job_staging_attempt_v1(uuid,uuid,uuid,uuid,text,integer,text,timestamptz,text)'),
 ('video_queue.revoke_one_job_staging_v1(uuid)'),
 ('video_queue.stage_one_job_private_result_v1(uuid,uuid,uuid,text,integer,text,uuid,text,text,text)'),
 ('video_queue.read_one_job_private_result_v1(uuid,uuid,uuid,text,integer,text,uuid)'),
 ('video_queue.finish_one_job_private_result_v1(uuid,uuid,uuid,text,integer,text,uuid,uuid,jsonb)'),
 ('video_queue.assert_one_job_staging_v1(uuid,uuid,uuid,text,integer,text)'),
 ('video_queue.attest_one_job_staging_receipt_v1(uuid,uuid,uuid,text,integer,text,uuid,uuid,uuid,text)'),
 ('video_queue.finish_one_job_staging_v1(uuid,uuid,uuid,text,integer,text,uuid,uuid,jsonb)'),
 ('video_queue.check_one_job_staging_scope_v1(uuid,uuid,uuid,text,integer,text,text)')
)
SELECT e.signature, p.oid IS NOT NULL AS installed,
       pg_get_userbyid(p.proowner) AS owner,
       CASE WHEN p.oid IS NULL THEN NULL
            ELSE has_function_privilege(session_user,p.oid,'EXECUTE')
       END AS effective_execute,
       EXISTS (SELECT 1
                 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) AS a
                WHERE a.grantee=0 AND a.privilege_type='EXECUTE') AS public_execute
  FROM expected AS e
  LEFT JOIN pg_catalog.pg_proc AS p ON p.oid=to_regprocedure(e.signature)
 ORDER BY e.signature;

SELECT n.nspname, c.relname,
       has_table_privilege(session_user,c.oid,'INSERT') AS table_insert,
       has_table_privilege(session_user,c.oid,'UPDATE') AS table_update,
       has_table_privilege(session_user,c.oid,'DELETE') AS table_delete,
       has_any_column_privilege(session_user,c.oid,'INSERT') AS column_insert,
       has_any_column_privilege(session_user,c.oid,'UPDATE') AS column_update
  FROM pg_catalog.pg_class AS c
  JOIN pg_catalog.pg_namespace AS n ON n.oid=c.relnamespace
 WHERE n.nspname IN ('public','video_queue') AND c.relkind IN ('r','p')
   AND (has_table_privilege(session_user,c.oid,'INSERT')
     OR has_table_privilege(session_user,c.oid,'UPDATE')
     OR has_table_privilege(session_user,c.oid,'DELETE')
     OR has_any_column_privilege(session_user,c.oid,'INSERT')
     OR has_any_column_privilege(session_user,c.oid,'UPDATE'))
 ORDER BY n.nspname,c.relname;
ROLLBACK;
```

For the queue session, require its expected login and database, active role
equal to login, only worker-v2 among the listed capabilities, no elevated
flags, schema USAGE and the three private EXECUTEs. The old assertion,
attestation and finish EXECUTEs must be false. No public pipeline or private
result table/column DML may appear; investigate any queue DML row too. For
the approver session, require the approver capability, no queue-worker or
broad-worker membership, no elevated flags, the two approval/revocation
EXECUTEs and no private worker EXECUTE. All nine listed functions must exist,
be owned by the intended `video_subset_owner`, and have no PUBLIC EXECUTE.
For the collector session, require only the collector capability among the
listed subset roles, no broad-worker/app membership, no elevated flags and
no public/private pipeline DML. The one-shot path repeats that identity and
ACL check on each collector observation connection.
Missing migration rows, functions, roles, or unexpected grants are holds.
The v2 output constraint must be present and validated, with its older
replaced constraints absent.
The operator must also verify the exact deployed 0061/0062 definitions and
job constraint against reviewed SQL; presence alone does not prove parity.

## Conditional ACL delta for separate review — do not execute now

This is the narrow runtime grant delta **after** authorized installation of
0061 and 0062, owner transfer to `video_subset_owner`, schema/owner checks,
backup and restoration proof. It creates no role or login. The proposals
already revoke PUBLIC defaults on their new functions and tables; repeat
those revokes only if the catalog inventory requires it. Never grant old
public-candidate functions to the private queue login. If an inherited grant
still makes an old function or table DML effective, stop and review the actual
grant source; a direct `REVOKE` from the login will not cancel inheritance.

```sql
-- UNAPPLIED REVIEW DELTA; authorized installer only, after 0061/0062 install.
REVOKE EXECUTE ON FUNCTION
  video_queue.finish_one_job_staging_v1(uuid,uuid,uuid,text,integer,text,uuid,uuid,jsonb)
  FROM video_subset_worker_v2;
REVOKE EXECUTE ON FUNCTION
  video_queue.assert_one_job_staging_v1(uuid,uuid,uuid,text,integer,text),
  video_queue.attest_one_job_staging_receipt_v1(uuid,uuid,uuid,text,integer,text,uuid,uuid,uuid,text)
  FROM bridge_school_worker_principal;
GRANT EXECUTE ON FUNCTION
  video_queue.approve_one_job_staging_attempt_v1(uuid,uuid,uuid,uuid,text,integer,text,timestamptz,text),
  video_queue.revoke_one_job_staging_v1(uuid)
  TO video_subset_approver;
GRANT EXECUTE ON FUNCTION
  video_queue.stage_one_job_private_result_v1(uuid,uuid,uuid,text,integer,text,uuid,text,text,text),
  video_queue.read_one_job_private_result_v1(uuid,uuid,uuid,text,integer,text,uuid),
  video_queue.finish_one_job_private_result_v1(uuid,uuid,uuid,text,integer,text,uuid,uuid,jsonb)
  TO video_subset_worker_v2;
```

Only the two approver and three private worker EXECUTEs are new runtime
grants. The full installation ownership and PUBLIC revokes remain specified
in `VIDEO_ONE_JOB_ACTION_TIME_ACL_DELTA_20261015.md` and
`VIDEO_ONE_JOB_PRIVATE_RESULT_GATE_20261015.md`; reconcile them with the
fresh catalog before constructing an actual migration. Do not combine the
former document's old public-writer grants with this strict route.

**Stop/rollback:** before a private finish, disable further exact approvals
through the authorized approver and revoke the five runtime EXECUTEs above;
verify effective rights again, including inheritance. Do not restore PUBLIC
or old-path grants. After any private receipt or finished job exists, retain
0061/0062 tables, immutable rows and the v2 output constraint for recovery;
disable future approval/grants and reconcile the job before using the
unchanged shadow resident for other work. Dropping objects or resetting the
constraint is not a safe rollback.

## Missing action-time facts

Fresh target identity and migration checksums; actual queue, approver and
collector login names and inherited privileges; exact resident image/commit
and whether the native unit or container runs; verified backup/restore; one
finalized batch, approved subset and chosen job/source fingerprint; a
separate timely approval operator and reviewed deployment of this new
one-shot launcher; and the bounded run/recovery receipt are all still absent.
No real job should be claimed until these are resolved and separately reviewed.
