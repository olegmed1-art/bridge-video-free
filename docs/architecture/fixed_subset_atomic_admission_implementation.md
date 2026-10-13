# Fixed-subset admission: unapplied server candidate

Date: 2026-10-12. Governance: ASSURED. State: LOCAL / SQL NOT RUN / RUNTIME HOLD.

This stage implements the database boundary described in
`fixed_subset_atomic_admission_design.md`; it does not activate it. Baseline main
is `d49feb4cb333b041928a5d37efb56a9f97dd1822`, with the exact PR #2158 tree at
`717a4d92bc51bfc76f3e6455911e8b9900d83beb`. Neither public ref is modified.

## What the previous six files contain

`universal_video/admission_binding.py` is pure validation, not server logic.
Its unit test and `test_universal_video_admission_sql_plan.py` are offline checks.
The other three files are a design document, a declarative SQL matrix, and a
synthetic terminal fixture. They remain unchanged and are reused by this stage.
Their previous PASS does not establish that the new migration works.

## New executable boundary

| File | Responsibility |
| --- | --- |
| `database/migrations/0060_fixed_subset_admission.sql` | Actual PostgreSQL tables, privileges, permanent guards and admission/recovery/attempt functions. Unapplied. |
| `universal_video/admission_client.py` | Explicit-DSN caller owning one connection and transaction, one server call, receipt validation before commit and return only after commit. No environment credential lookup or mutation retries. |
| `tests/test_universal_video_admission_client.py` | Transport spies covering transaction ownership, receipt mismatch rollback and lost COMMIT response; these are not SQL concurrency tests. |
| `tests/test_universal_video_admission_runner.py` | Offline collection and execution-gate tests with driver/socket spies; no database connection. |
| `database/rollback/0060_fixed_subset_admission.sql` | Empty-state deactivation which retains evidence structures and guards. Unapplied. |
| `database/tests/fixed_subset_admission/` | Executable synthetic PostgreSQL scenarios, separate real connections and test-only fault/barrier helpers. Not executed. |
| `.github/workflows/fixed-subset-admission-postgres.yml` | One-job Linux/PostgreSQL test workflow for the first push creating one exact candidate branch. Not published or run. |

Migration 0060 depends on 0056, 0057, both 0058 parts and 0059. It is one DDL
transaction with bounded locks, a prepared-transaction check, historical source
backfill and persistent guards. It is not safe to apply to an active legacy
workload: the precondition remains an authorized and drained maintenance window.

The new authority tables are `subset_approval`, `source_observation`,
`subset_admission` and `source_reservation`. Queue jobs still live in the existing
`video_queue.batch`, `job` and `job_event` tables. Admission calls the existing
`enqueue_drive_batch` inside the same transaction; Python `enqueue_manifest` is
not called. Actual generated queue UUIDs are persisted in the immutable binding.

Server entrypoints include:

- `approve_subset(uuid,text,jsonb,timestamptz)` for trusted exact contract bytes;
- `observe_subset(...)` for trusted native source observations;
- `admit_approved_subset(uuid,text,text,text,uuid)` for one fixed subset;
- `recover_subset_admission(text,text,text)` for its immutable receipt;
- `authorize_subset_attempt`, `heartbeat_subset_job`, `retry_subset_job` and
  `finish_subset_job` for the fenced worker path.

Separate NOLOGIN capability roles receive only their intended function grants.
No existing runtime principal receives these memberships. `session_user`
membership is checked inside SECURITY DEFINER functions; caller-set settings do
not establish authority. Approval provenance and source reads must come from the
trusted approver/collector integration, which this candidate does not deploy.

New approvals have `runtime_enabled=false`. The existing `claim_job` signature
is preserved, while its SQL selector is restricted to eligible finalized managed
admissions, with a hard three-attempt bound. Legacy workers cannot process the
new rows. Native observations are bound to the approved fingerprint, actual job,
lease token and attempt; same-size changes with null checksums remain detectable.
Canary completion keeps the existing review HOLD and does not release the rest.
The resident Python worker is not upgraded or deployed by this change.

Recovery precedes fresh-source checks and reservation acquisition. Mutable
RUNNING/CANARY_REVIEW status, token rotation, later source changes and approval
expiry/revocation do not rewrite the fact of admission. A missing or inconsistent
binding is an error; a legacy batch is never silently adopted.

Reservation inserts cover absent source keys before the final locked freshness
check. Freshness is anchored to that check, not universally to COMMIT time.
The `SET CONSTRAINTS ... IMMEDIATE` limitation from the design still applies.
An SQL function result is tentative until its outer transaction commits.

## Bounded codec and evidence limits

The server canonical codec currently accepts null, strings, booleans and integer
numbers. Non-integer duration/cost annotations fail explicitly with
`SUBSET_CANONICAL_INTEGER_DOMAIN`; they are never rounded or discarded. This is
a deliberate restriction compared with the existing Python planner, which can
retain fractional cost annotations. Missing duration/cost remain null. Unicode,
canonical bytes and digests require the real PostgreSQL cross-vector tests before
this boundary can be promoted.

No SQL in this stage has been sent to PostgreSQL. Offline Python tests,
Python/YAML syntax parsing and independent code review are useful evidence but
cannot demonstrate locking, grants, trigger timing, transaction rollback or SQL acceptance. The
declarative matrix remains historical design input; the new runner is the
executable implementation of its 29 scenario IDs. Test-only helpers are never
included in the production migration.

## Rollback and containment

A failed migration transaction rolls back its DDL and backfill. After COMMIT,
the rollback file first locks the affected tables and refuses if any approval,
observation, admission or reservation exists. This includes historical tombstones.
On an empty database it revokes the new entrypoint grants, retaining tables,
guards, role definitions and migration history. It does not drop the schema or
restore unsafe legacy grants.

For nonempty state, use a reviewed forward correction and deactivation: stop new
admissions and affected workers, preserve bindings/reservations and read recovery
access, and investigate the captured evidence. A failed canary never frees a
source identity. Production rollout additionally requires tested restoration and
historical cutover evidence; neither is claimed here.

## One next approval, before production or publication

The next bounded approval can cover one run of this exact frozen candidate on
an explicitly identified disposable Linux/PostgreSQL instance: one job, one
PostgreSQL 16 service, at most eight simultaneous test connections, no matrix
fanout, and a 20-minute job limit. It covers only synthetic fixtures, temporary
DDL/data, all 29 scenarios, empty rollback/nonempty refusal checks and saved test
evidence. No production credentials, Drive, IBM, media processing, canonical
publication or production migration are included. It does not assume new paid
infrastructure. Standard Ubuntu jobs and ordinary artifacts for this public
repository are free; this workflow does not save GitHub Actions caches.

The prepared GitHub workflow uses read-only repository permissions, pinned Action
commits, PostgreSQL `16.15-bookworm`, a fixed synthetic password, a loopback-only
port and clean environments. Its only event is `push` to
`codex-atomic-subset-postgres-20261012`, with the workflow path changed. The job
requires the repository owner, initial branch creation, no force/deletion and
run attempt 1. A normal authenticated owner `git push` must create the branch
directly at the reviewed final commit; creating an empty branch first is not
equivalent. Checkout is pinned to the event SHA. No PR or main merge is needed
to register a previously unpublished manual workflow. Publication is therefore
also execution and requires approval of both together; neither is authorized now.

That one job first runs exactly the two Windows-skipped POSIX metadata-inventory
checks, requires two passed results and zero skips, then executes the 29 SQL
scenarios. Existing workflow files and their filters remain unchanged. A separate
PR to main would match 15 existing workflows with up to 18 jobs and three extra
PostgreSQL services, including a real synthetic media smoke test. Opening that PR
is outside the one-job approval. Deleting/recreating the branch, updating code,
rerunning jobs or expanding scope requires a separate decision.

Successful collection is not execution. Acceptance requires a result for every
scenario ID, no skipped/placeholder PASS, pinned source hashes and server version,
actual SQLSTATE/constraint evidence for negatives, real blocked-session evidence
for concurrency, and zero unintended canary release. Any failure keeps this
candidate in HOLD and does not authorize another run or production enablement.

The flat candidate branch matches the existing Vercel deploymentEnabled wildcard
`*`: false, with no matching true rule. The previous slash-containing branch did
not match that minimatch pattern. Vercel configuration and existing workflows
remain unchanged. Unknown internals of unrelated integrations and private-repo
artifact quotas are not blockers for this prepared public-repository job.
