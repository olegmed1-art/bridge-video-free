# One-job private route: activation preflight draft, 2026-10-15

**Prepared, not executed.** This is a read-only inventory and a conditional,
unapplied ACL proposal. The receipt/lock revision has passed local synthetic
tests but awaits independent re-review and disposable PostgreSQL ACK-loss
evidence.
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
`approval_signal_path`. Both paths are absolute. A 0600 receipt journal is
reserved before any claim SQL. The complete lease token and attempt are
published atomically as `PRECOMMIT_UNCONFIRMED` before COMMIT, then changed to
`COMMIT_ACKNOWLEDGED` only after its ACK. A missing ACK requires separate
authorized read-only reconciliation; the launcher never blindly reclaims.
Immediately before staging, the worker
writes a `<approval_signal_path>.ready` notice with the same binding. The
approver waits for that notice, uses its own login to call 0061 for the exact
live claim, then writes a restricted JSON signal with exactly `admission_id`, `job_id`,
`lease_token`, `worker_key`, `attempt_count`, `source_fingerprint`, and the
new `lease_approval_id`. The worker checks that signal just before staging;
an absent signal times out without a private write. No approver credential or
automatic approval lives in the resident. The one-shot path holds the workload
lock exclusively against ordinary shared-lock polling and uses the existing
processor, heartbeat, collector and 0062
finish. It commits a claim only if 0060 returns the expected job, batch,
source and attempt; mismatch rolls the claim transaction back. Pre-existing
claim, ready or approval-signal files block a new launch.
Before a real launch, verify that ordinary resident queue polling is
quiesced for the target batch and cannot claim a sibling when this one-shot
lock is released. The exclusive lock prevents overlap only while held; it does
not disable later polling. Reconcile the actual service state and restore its
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

## Deployed resident and database readback (still pending)

This repository identifies two *possible* service wrappers, not which one is
running. The native unit runs as `universal-video` from
`/opt/bridge-school/universal-video-src` using
`/opt/bridge-school/universal-video/.venv/bin/python -m universal_video.spool_worker`.
The container image uses `tini -> universal-video-container-entrypoint ->
universal_video.container_runtime -> python -m universal_video.spool_worker`.
The normal command enters the polling loop. The proposed one-shot command is
`python -m universal_video.spool_worker --private-one-job <binding.json>`;
the installed unit does **not** invoke that command. No deployed `v6` release
manifest, `approvedonejob` launcher, service revision, or image digest is
present in this checkout. Those names must be tied to an actual deployed
artifact before compatibility is asserted.

On the authorized resident host, an operator can record the following
**read-only, no-secret** facts. Capture only the named properties; do not
collect unit `Environment`, `systemctl status` logs, `/proc/*/environ`, DSN
files, or a whole `docker inspect` result. Redact unexpected arguments before
sharing the output.

```sh
systemctl show universal-video.service \
  -p LoadState -p ActiveState -p SubState -p FragmentPath \
  -p ExecStart -p User -p Group -p WorkingDirectory -p MainPID \
  -p ExecMainStartTimestamp -p NRestarts
# Only if this is the actual source checkout; no fetch or pull:
git -C /opt/bridge-school/universal-video-src rev-parse HEAD
sha256sum /opt/bridge-school/universal-video-src/universal_video/spool_worker.py \
          /opt/bridge-school/universal-video-src/universal_video/neon_worker.py \
          /opt/bridge-school/universal-video-src/universal_video/private_one_job_operator.py \
          /opt/bridge-school/universal-video-src/universal_video/private_one_job_entrypoint.py
```

The four local source SHA-256 values for this pending revision are, respectively,
`add5a84c4dca00112eaedaf5ded41caec08d1246adb04063777b12daffe5c911`,
`90214675b46e6a48bb7192f82726965fe62d1db9eaf9bfa87f989e2239b26f27`,
`edf4816c4df99ccda8906c6bc2ec8c48300317a51d2adf18c2c86ebd7fc28cbe`,
and `915a1d34f00694fdc3dac11ba9b307412253467b9f798485e2bd97fe91cb4fd5`.
The reviewed source SHA-256 values of proposal files 0061 and 0062 are
`b218f42dde413cf2ad4765de417d0585773c4d93a8dca7e537d46e625cc2d56e`
and `58f2fa3dc4d83cb49b6afd35b3cac00c162b22456d114da43fa3d9a65f153a7a`.

If containerized, collect only container name, image ID, state, read-only
mount destinations and the image's `org.opencontainers.image.revision` label
through a field-scoped inspection; do not export the full inspect JSON, which
contains environment values. Compare the image ID and revision with the
reviewed image receipt and compare all four module hashes with this candidate.
Absence of `private_one_job_operator.py` or an unexpected source hash is a
hard hold. Confirm the normal resident and proposed one-shot would use the
same physical spool root and lock inode; matching path text across different
mount namespaces is insufficient. Record the existing service state and
polling configuration as names/presence only, then plan how target-batch
polling will be quiesced without changing other workload.

The target database must be selected from a fresh operator-owned deployment
record, not the historical endpoint above. A restricted read-only session on
that exact endpoint should first record server host/port from the connection
metadata, database, `session_user`, `current_user`, server version and time.
Run the SQL inventory above separately under the **existing** queue,
collector and approver logins, with transaction read-only and a short
statement timeout. Compare the 0059/0060 `schema_migration` rows and their
stored checksums with the approved migration receipt; 0061/0062 are proposal
files, so inspect their actual tables, functions, owners, grants and v2 check
constraint rather than assuming migration rows exist. For each installed
function, compare `pg_get_functiondef(oid)` with the reviewed 0061/0062 SQL
after normalizing formatting, and preserve a signed or hashed readback. A
mere matching function name is insufficient. The inventory is catalog-only;
do not call claim, approval, stage, finish or revoke functions to test them.

If a live readback would require new access, a secret transfer, a billable
database wake-up, or an unverified endpoint, stop at this prepared command
set. The smallest next check is an authorized operator returning redacted
service/image identifiers and the read-only catalog result for the pinned
target and three existing restricted logins. Without it, current deployment,
schema parity, ACL and rollback readiness remain unknown.

## One-shot coordination contract for an existing launcher

The launcher must supply the exact eleven-key protected binding described
above and must invoke the explicit one-shot entry point; changing only an
environment toggle or restarting the normal unit will not select it. Before
the exact 0060 claim, the launcher reserves the receipt path in a protected
directory. It atomically publishes the token before COMMIT. The separate
approver acts only on `COMMIT_ACKNOWLEDGED`; a `PRECOMMIT_UNCONFIRMED` record
could represent either a committed or rolled-back claim and must be matched
against the actual job/lease state using an authorized read-only DB session.
For that recovery, preserve the journal and run the following bounded query
under an **existing separate verifier** with SELECT rights on this one job.
The queue worker must not be given table SELECT or an owner credential.

```sql
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '5s';
SELECT job_id, batch_id, source_file_id, status, attempt_count,
       lease_owner, lease_token, lease_expires_at
  FROM video_queue.job
 WHERE job_id = :expected_job_id AND batch_id = :expected_batch_id;
ROLLBACK;
```

Compare every returned identity and the lease token with the saved journal.
If a matching lease committed and remains active, an operator can plan a
separately reviewed resume using the **same** lease and approval boundary;
do not claim again. If it did not commit, or has expired or changed, preserve
the file as incident evidence and reconcile job events before clearing the
attempt. A failed final receipt update also leaves an unconfirmed file and
stops processing. No automatic exactly-once guarantee is claimed for a lost
COMMIT acknowledgement; the synthetic ACK-loss test only proves this local
fail-closed behavior.
After processing and live terminal readback the worker
writes `<approval_signal_path>.ready`, also exclusively, then waits up to
1800 seconds. A separate approver must verify the current lease, use its own
restricted connection to call the 0061 approval function for that precise
attempt, and write a 0600 signal containing the exact seven fields listed
above. The worker checks the signal and 0062 rechecks the SQL approval during
staging. Signals never confer authority. A stale file blocks a new launch;
do not delete it until the queue and private result state have been reconciled.
For the native unit, binding and signal paths must be accessible under its
existing sandbox: the unit's writable paths are
`/opt/bridge-school/universal-video` and `/run/bridge-school`. The launcher
and separate approver need a pre-existing protected handoff directory and
compatible file ownership while retaining different database principals;
do not widen filesystem permissions merely to pass the signal.

No implementation or versioned manifest of an existing `v6` launcher was
found here, so this is a proposed interface for its owner to compare against
the actual deployed code. In particular, verify its readiness wait, separate
approver principal, exact lease expiration, 0600 file ownership, and handling
of interrupted claims. Any mismatch is a hold, not an invitation to run a
compatibility probe against production. No IBM process is needed for this
comparison.

The local candidate now includes a manual, separate
`python -m universal_video.private_one_job_approver <approval-binding.json>`
entrypoint. Run it once **after** the exact `.ready` notice appears. Its
protected JSON must have exactly: `expected_database`, `expected_host`,
`expected_port`, `expected_approver_login`, `expected_worker_login`,
`expected_batch_id`, `expected_job_id`, `expected_source_file_id`,
`expected_attempt_count`, `admission_id`, `source_fingerprint`, `worker_key`,
`claim_receipt_path`, `approval_signal_path`, `approver_dsn_file`, and
`approval_reference`. The DSN file contains an **existing** restricted
approver credential, is absolute and mode 0600 on Linux; no queue/worker DSN
may be present in that process. The entrypoint checks the acknowledged claim,
exact ready notice, readiness age (at most 60 seconds), target, separate
principal, effective roles/ACL and server time; it asks 0061 for a 30-second
approval. Only after the SQL COMMIT does it atomically create the seven-field
signal. It has no retry or automatic approval. A missing signal after an
uncertain COMMIT needs catalog readback, not a second approval call.

`tests/manual_one_job_0061_0062_pg18.py` is a runnable local rehearsal; it
requires an already available PG18 `bin` directory and no downloaded
dependencies. It creates a fresh loopback cluster beneath this checkout,
installs unchanged migrations 0001–0060 and the exact proposed 0061/0062
files, uses synthetic restricted logins and invented source metadata, then
stops and removes its cluster. It exercises claim, prework observation,
approval, private stage/read, postwork observation, finish and final owner
readback, plus role/token/expiry/replay and lost claim-ACK cases. This proves
the candidate against a disposable PostgreSQL, not Drive, media processing,
production ownership/ACL deployment, or a currently deployed resident.

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
