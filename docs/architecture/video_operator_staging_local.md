# Existing operator authority for completed-video staging

2026-10-13. ASSURED; local candidate, no installed caller or live SQL evidence.
Code base: PR2152 `040518f40b1298a3b84dff2b271a11690c52f927` plus local handoff.
Source/caller audit: main `d49feb4cb333b041928a5d37efb56a9f97dd1822`.

## Decision and evidence

The new principal-to-school-to-generation permission model was an extra design
constraint, not an original requirement for the operator-controlled staging
path. It is withdrawn as a universal blocker. The previous tenant/CAS mock is
retained as a historical optional design; it neither authorizes writes nor
defines a production guarantee.

`VIDEO_CANON_STAGING_REPLAY_V1.md` explicitly distinguishes STAGING_READY from
source authorization and allows EVIDENCE_ONLY candidates. An assertion claiming
APPROVED needs independent source-policy evidence. Recording incomplete staging
under an existing trusted operator does not imply that assertion is approved.
Existing `bridge_neon_persistence`, Diana and backfill callers use the worker
credential without member impersonation or per-generation grants.

Migration0005 grants background ingestion/analysis writes to
`bridge_school_worker`;0016 assigns it to `bridge_school_worker_principal`;
0010 adds media/analysis lineage;0049 grants candidate recording. This is source
evidence of the existing contract, not proof of current live credentials/grants.
"Owner-only" here means repository/operator-controlled execution, not connecting
as a PostgreSQL owner and not a proven `github.actor == owner` workflow gate.

## Local adapter

Trusted Python code can explicitly construct `OperatorStagingTransport` with
one `CompletedGeneration`, retained PDF bytes and the existing worker DSN, and
pass it to `handoff_completed_video(transport=...)`. No existing CLI, workflow,
HTTP handler, environment toggle or queue consumer installs this transport.
Construction and loading perform no database or network operations.

The transport verifies PDF SHA256, exactly one `master_analysis.json` attachment,
its exact bytes and raw SHA256. The existing handoff then verifies completion,
job/revision/source/report pins and source selection, and invokes the existing
evidence reader and quality builder. Pins must come from trusted operator
selection outside generated artifacts. Hashes show integrity, never permission.
Missing original media, roles or identity remain UNKNOWN/BLOCKED; this adapter
does not manufacture independent teacher or semantic policy evidence.

The existing DSN validator constrains the worker URI. Authentication is checked
on the actual exclusively owned connection inside the same outer transaction:
session_user and current_user must both be the fixed worker principal; its
capability must be available through `pg_has_role(...,'usage')`; school SELECT
must exist; forbidden policy UPDATE, person DELETE and schema CREATE must not.
The fixed active school must resolve uniquely. The actual SQL statements still
require their native PostgreSQL privileges; a validation boolean grants none.

Before and after the existing result, identity and candidate writers, source
and media/report asset IDs are checked for foreign-school collisions. Existing
rows are held FOR SHARE using their already-granted UPDATE capability. No school
or policy row lock, grant, role, migration or new permanent right is introduced.
The postcheck verifies actual returned school/source/media IDs and stored core
entities; the existing candidate writer retains its independent exact
run/artifact/source lineage validation. All failures abort the same outer tx.

Successful commit plus connection close returns technical COMMITTED_STAGING,
which may retain replay_status=BLOCKED. The receipt always denies canon write
and teacher access. Outbox recording is inherited from the writer; no outbox
publication, checkpoint, ASR, DDS, correction resolver or canon writer is added.

## Revocation, rollback and limits

Repeated privilege checks reject an observed revoke; SQL permission failures
abort. They do not serialize administrator revocation with commit or cancel SQL
already executed. This adapter does not promise instantaneous cancellation.
The existing operator model uses existing role/credential/session administration
for revocation; changing those rights or terminating sessions is outside this
local stage. A future linearizable cancellation requirement needs a separate
reviewed mechanism and SQL race tests, not an invented boolean permission.

Failures after opening a writer return BLOCKED/UNKNOWN without usable IDs,
including lost commit/close acknowledgement. Reconcile the exact generation
before retrying. Idempotent replay uses existing stable writer IDs. Removing
this uninstalled adapter/caller wiring is the local rollback; no database
rollback or permission change has occurred in this stage.

Tests use actual PDF attachments, hashes, replay/evidence preparation and
candidate serialization with explicit in-memory connection/writer doubles.
They prohibit network/DB connections, cover foreign schools, source/PDF/master
tampering, missing frames/roles, incomplete processing, repeats, actual-session
rejection, observed revocation, write/commit/close failures and deny-promotion.
These tests do not prove PostgreSQL grant/race/commit behavior. The prior retained
real-master test lacked original PDF bytes; it is not a real test of this new PDF
transport. No synthetic fixture is presented as video e2e evidence.

Next safe step: review this exact patch and prepare an isolated SQL rehearsal
against the existing migrations/worker grants with no new rights. Only after
separate execution authorization should it verify nested transactions,
idempotency, collision and permission-error rollback. A later bounded real
generation pilot must use independently retained PDF/master/source evidence
and the existing authorized internal reader. Personal teacher delivery still
requires its separate real authentication/disclosure evidence. Production
readiness is not established by this local result.

## One isolated SQL rehearsal candidate

The separately authorized `video-operator-sql-rehearsal-20261013.yml` is limited
to creation of one named non-production branch, run_number=1 and run_attempt=1,
one GitHub-hosted Ubuntu job and a 20-minute timeout. It starts PostgreSQL18 in
an ephemeral service container exposed only on runner loopback55434. No repository
secret, existing database, production credential, merge, deployment or paid
cloud resource is used. It is not a runtime/production caller of the adapter.

`tests/test_video_operator_postgres.py` applies no production migration. The
workflow first applies the repository's unchanged migrations only to that empty
container. The harness enables temporary LOGIN/password on the two existing
worker/app principals, retaining their existing capabilities; no new capability
grant is added. One negative test temporarily revokes and restores the existing
worker membership inside the disposable service. Teardown disables these logins
and clears passwords; the workflow always removes its own container and volumes.

The harness replaces only the hard-coded production endpoint validator with a
test-only exact loopback validator. It uses genuine psycopg connections, actual
session_user/current_user, unchanged guards and SQL writers, real nested
transactions, constraints and a new authenticated internal-reader connection.
This does not test production TLS, credentials, endpoint availability or a
teacher's personal authorization. SQL failure rolls back the complete result,
source identity, candidate and technical outbox graph; duplicates must produce
the same IDs without new rows. Foreign-school collisions, wrong session roles,
revoked membership, PDF/master/source mismatch and canon DML denial are exercised.

Source SHA/tree/parent, migration hashes, container digest, SQL/test logs, exact
diff and cleanup receipt are retained for7days in a synthetic CI artifact. A
failed run is evidence of failure; this one-shot workflow does not authorize a
rerun. The acceptance result must be recorded against its actual commit/run,
not inferred from collection or local mock PASS.
