# Fixed-subset connector installation and capability recovery

ASSURED/I2 candidate. Production execution requires a separate action-time approval.
No CLI here connects to a database. The single synthetic CI has its own fixed
localhost fixture; never give it a production DSN.

## Executor and transaction contract

Use the existing Neon `run_sql_transaction` connector, bound to the explicitly
approved project/branch/database and observed `neondb_owner`. Its documented
multi-statement single-transaction contract permits this route independently of
the protected workflow's unknown secret identity. The provider transport and
permissions still require action-time reconciliation; isolated PostgreSQL tests
do not prove Neon API transport or durable acknowledgement handling.

The builder requires private local `target.json` (project_id, branch_id,
endpoint_id, database_name), `catalog-baseline.json`,
`semantic-catalog-baseline.json`, `claim-baseline.json`, `restore_claim_job.sql`,
and the hash-pinned source migrations under `source/database/migrations` and041
under `source/database/tests`. No implicit destination or catalog defaults.
`build_recovery.py` renders the fixed repair; `connector_packet.py` emits argument
objects for review. A recovery/verifier payload requires the exact receipt of a
positively acknowledged successful installation transaction. UNKNOWN is HOLD;
never repeat an unknown install or assume a pre-COMMIT SELECT receipt committed.

0059 +0060 + both checksum writes + installation assertions share ONE transaction.
The only migration transformations are removal of the fixed psql ON_ERROR_STOP
directive and outer BEGIN/COMMIT, with lexical splitting of unchanged SQL bodies.
Original file SHA256 and emitted envelope SHA256 are distinct evidence.
Each envelope sets lock5s, statement60s, transaction5min, idle-in-transaction30s,
UTC/ISO output, trusted search path and the existing two-key advisory xact lock.
Keep a continuous external worker fence/drain and a maximum15min action window
across calls; no automatic retries. This package does not stop/resume workers.

## Authority to enumerate at action time

0060 creates five persistent NOLOGIN roles: `video_subset_owner`,
`video_subset_approver`, `video_subset_collector`, `video_subset_admitter`,
`video_subset_worker_v2`. PostgreSQL retains bootstrap ADMIN-only memberships for
the creator. Four new tables and 25 functions including replaced `claim_job` and
`subset_claim_job` move to the definer owner. Capability roles receive exactly
12 API EXECUTE grants and schema USAGE; no LOGIN memberships are provisioned.
The definer gains legacy table SELECT/INSERT/UPDATE, sequence USAGE/SELECT and
four legacy function EXECUTE grants. Five legacy API grants are cut off; six
guards are added to old batch/job.
These are material persistent installer changes, not a read-only operation.

The installer temporarily grants definer membership with INHERIT/SET and schema
CREATE, then revokes both. Verifier/recovery temporarily grant only definer
membership with ADMIN=false, INHERIT=true, SET=false, and revoke the exact
installer-issued grant before COMMIT. This supplies substantial inherited owner
authority, but adds no persistent verifier/reader role or ACL. Failures abort the
whole transaction. Existing independent bootstrap grants are preserved.

## Recovery state, not migration removal

`repair-0060-legacy-recovery-v1.sql` is an explicit guarded recovery artifact,
outside the pending migration inventory. Its exact checksum is recognized in
`database/external_migration_history.tsv`; ordinary migrate.sh validates an
applied marker and never auto-applies the repair.0059/0060 markers are retained.

Recovery requires exact installation catalog/data receipts, an unchanged full
registry, no approvals/observations/admissions, no runtime-role provisioning,
drained jobs and the complete original source-to-historical-job map. It restores
the captured old claim definition/owner and exact observed legacy ACLs; removes
only six added guards on old batch/job; removes added old-table/sequence/schema
grants; and revokes all subset capability EXECUTE. It preserves queue rows,
events, sequence, every reservation including duplicate historical source IDs,
0059 size constraint, four new tables, their guards, and five new NOLOGIN roles.
Its ledger write, data/catalog postconditions and temporary-grant cleanup are
atomic. Any mismatch aborts rather than widening permissions or deleting history.

041–043 explicitly recognize the checksum-bound recovery state, assert the
restored/disabled capability matrices and run legacy queue/canary/size behavior.
043's unrelated0059 rollback/reapply is excluded only in the recovered state,
where0059 must remain present. Missing/forged repair markers and reenabled subset
EXECUTE fail before behavior fixtures. This is not full pre0059 schema equality.

After a separately authorized legacy restart, new legacy work is not mirrored
automatically into retained reservations. Subset reactivation therefore requires
fresh full-history reconciliation and a reviewed forward transition. Do not use
the old empty-only0060 rollback or delete existing ledger entries.

## Evidence scope

The one Ubuntu PG18 fixture creates only0056–0058 synthetic baseline, then executes
the exact generated install/verifier/recovery lists as the nonsuperuser owner.
It emulates Neon target GUCs explicitly, injects failures after GRANT/owner changes
and before install COMMIT, tests unknown default-function grantees, records raw
synthetic before/after data/ACL/ownership and runs necessary041–043 regressions.
It verifies ordinary migrator skip/history recognition with only the five affected
migrations. It is not a repeat of the unrelated full database preflight.

The semantic drift digest is scoped: it includes functions/triggers/constraints,
selected relation/column attributes, creator default ACLs and selected role graph;
it is not a full backup and omits e.g. column-default expressions/index definitions
and provider-role internals. Exact old ACL/owner/function and full new-function
ACL assertions are separate. A mismatch is HOLD, not permission to refresh a
baseline silently. Fresh action-time evidence and a recoverable backup remain
production approval inputs, not reasons to require a different executor's secret.

All obsolete local drafts (including0061_restore_legacy_capabilities_hold.sql)
are excluded from the final manifest and retained locally as historical evidence.
