# Internal video staging persistence and read rehearsal

Status: staging implementation and disposable PostgreSQL contract rehearsal. No
production import, canon activation, student publication, deployment or paid
compute is authorized by this document.

## Existing school service boundary

`GET /v1/knowledge/internal/video-staging/{candidate_id}` belongs to the existing
knowledge router, mounted with `Depends(require_api_token)`. The existing token
authorizes an internal service; it is not a student's identity. The database
connection is supplied by the existing `bridge_school_api.db.connect`. This work
does not change that module, `main.py`, credentials, login, roles or grants.

The route uses one `REPEATABLE READ, READ ONLY` transaction. It resolves the sole
active school by the existing server constant `Школа спортивного бриджа`.
Candidate, source, media asset, analysis run, artifact and replay receipt must
all bind to that school. No request school, teacher label, instructor role or
per-video disclosure receipt is needed for this internal common-school lane.
The older personal teacher reader retains its separate disclosure gate.

The response is a bounded source excerpt with exact offsets and a source URL,
raw/canonical parent hashes, candidate and replay hashes. It preserves replay
BLOCKED/PARTIAL status and machine-readable gaps. Speaker identity and media
review remain unverified, accepted canon remains empty, and student delivery and
canon writes remain false. This route cannot serve arbitrary candidate types,
hands, student metadata, full receipts or diagnostics containing arbitrary text.

## Exact persistence handoff

Pass the **actual return value** of `persist_video_result` separately to
`persist_quality_candidates(..., persisted_result=result)`. The IDs are selectors,
not proof. The writer checks the existing database facts:

- fixed active school; active lesson-video source;
- exact source, source asset, analysis run and artifact version;
- source identity with `drive_source_native_key`, hence `google-drive:<id>`;
- exact `source_asset(..., relation_type='embodies')`;
- existing asset-form primary `analysis_run_input` and its `metadata.source_id`;
- registered run job/source IDs, source locator and immutable media checksum;
- the run's artifact/output relationship and embedded master raw hash.

There is no latest-run or latest-source fallback. Missing handoff is explicitly
`BLOCKED / EXACT_PERSISTED_RESULT_REQUIRED`; generated payload JSON cannot supply
the handoff. Diana v4.2 exposes `main(persisted_result=...)` as a server-only seam.
Its ordinary standalone invocation remains blocked for persistence until the
authorized orchestrator supplies that exact preceding worker result. This
change does not claim a deployed end-to-end orchestration chain.

The existing production DSN checks are preserved. The `_on_connection` variants
run the same SQL on an authorized caller-owned connection, allowing real local
PostgreSQL tests without mocking SQL or weakening production DSN validation.
The caller must explicitly begin an outer transaction to combine helpers
atomically. On an idle connection each helper commits its own transaction;
inside an existing transaction it uses a savepoint. A returned pending result
alone does not prove that an outer transaction was committed.

## Receipt and retries

Each record retains its own `method_version`. A record without one may use the
legacy quality version; supported replay records already have explicit methods.
Only staging/review-queue records are accepted; no authoritative table is a
candidate-writer target.

The full unchanged replay is stored in an additional `analysis_candidate` of
type `video_staging_replay_snapshot`, schema `video-staging-replay-snapshot-v1`.
Its envelope records exact source/run/asset/artifact/school bindings and the
database IDs and hashes of replay members. It is **outside** the replay member
list, avoiding a self-referential receipt hash. An `analysis_run_output` points
to that snapshot. No migration or new receipt table is required.

Candidate identity includes the exact lineage and replay hash. An exact retry
returns the same IDs; an existing conflicting payload, metadata, lineage or
output link raises and rolls back this staging transaction. Duplicate members,
invalid hashes, promotion commands and unsupported replay statuses are rejected.

Persistence describes the lineage observed when staging was written. It does
not grant a lasting authorization lease or promise commit-linearized revocation.
The independent read validates current relationships in its single consistent
snapshot. Later source/link revocation therefore yields BLOCKED. No new grants
or locking privileges are added to existing immutable tables.

## Reproducing the actual local test

Use a disposable PostgreSQL **18** database and apply the unchanged current Git
migrations with their normal checksums. The default source fixture is one invented
segment and frame byte string, built in the test. It is not a real video, media
verification, teacher identity or useful accepted knowledge. Do not alter immutable migrations.
On Windows ensure Git archive uses `-c core.autocrlf=false` so checksum guards see
canonical repository bytes. The isolated runner and logs accompany the delivery.

The integration test deliberately accepts only this client connection:

```
BRIDGE_VIDEO_REHEARSAL_DSN=host=127.0.0.1 port=55433 dbname=video_rehearsal user=rehearsal_owner
```

Clear all production DSN variables before running:

```
python -m pytest -q tests/test_video_staging_postgres.py
```

It applies the existing worker/app roles locally, writes the full quality output
from the synthetic fixture, closes the write connection, and reads
the committed candidate via another read-only connection and the protected HTTP
route. Adversarial cases use the same disposable database and restore/roll back
their mutations. Without the explicit disposable database these tests skip.
The Linux CI job provisions an isolated PostgreSQL 18 container and maps client
port 55433 to container port 5432; it uses no repository secrets or cloud DSNs.
The normal migration script runs only against that fixed service. PostgreSQL
contracts and synthetic-source success are never described as real video e2e.

An optional local `BRIDGE_VIDEO_REAL_FIXTURE_DIR` can supply an independently
retained artifact bundle with a pinned `MANIFEST.json`, master, selection and
frames. This opt-in is never set by public CI. Do not commit its inputs, excerpts,
checksums identifying private artifacts, output rows or local test reports.
Neither a generated manifest nor speaker labels become identity/canon authority.
Full original media inspection, semantic audio review and production teacher
request require separate evidence.

## Schema requirements for internal source-observation ingestion

No new DDL, role, grant or canon capability is introduced. Apply and verify the
complete unchanged numbered migration history from the pinned release, using
`database/scripts/migrate.sh` only on the approved target. Do not assemble a
partial production schema from the following inventory. Foundation migrations
`0001`, `0002`, `0004`, role/principal migrations `0005`/`0016`, media/provenance
`0010`/`0045`, candidate staging `0049`, and later guards including `0329` all
participate through the normal ordered chain. Required extensions are `pgcrypto`,
`btree_gist`, and `pg_stat_statements`; CI installs the existing schema as its
disposable owner, then performs runtime operations as existing worker/app roles.

The added persistence/read path directly requires these `public` relations:

| Relation | Required columns and invariant |
| --- | --- |
| `school` | `school_id`, `stable_name`, `status`; exactly one active server-selected school |
| `source` | `source_id`, `school_id`, `source_type`, `status`, `canonical_locator`; active lesson video with exact `gdrive:file:<id>` locator |
| `source_identity` | `source_identity_id`, `source_id`, `source_native_key`, `display_name`, `attributes`, `first_seen_at`, `last_seen_at`; unique `(source_id,source_native_key)`, native key `google-drive:<id>` |
| `source_asset` | `source_id`, `asset_id`, `relation_type`; exact `embodies` association |
| `asset` | `asset_id`, `school_id`, `checksum_algorithm`, `checksum_value`, `immutable_flag`; same-school immutable SHA256 asset |
| `analysis_run` | `analysis_run_id`, `school_id`, `parameters_snapshot`, `run_status`, `technical_record_status`, `publication_authorization_status`; parameters carry exact `job_id` and `source_drive_id`; technical status recorded |
| `analysis_run_input` | `analysis_run_id`, `asset_id`, `input_role`, `metadata`; primary ASSET row and exact source ID in metadata |
| `artifact` | `artifact_id`, `school_id`, `status`; same school, active/staging |
| `artifact_version` | `artifact_version_id`, `artifact_id`, `generated_by_analysis_run_id`, `provenance`, `status`; candidate version, exact run and original embedded master raw hash |
| `analysis_candidate` | `analysis_candidate_id`, `school_id`, `analysis_run_id`, `source_id`, `candidate_type`, `stable_key`, `input_fingerprint`, `quality_status`, `promotion_status`, `payload`, `payload_hash`, `evidence_refs`, `rejection_reasons`, `method_version`, `status`; unique `(school_id,candidate_type,stable_key,method_version,input_fingerprint)` and canonical JSONB readback digest |
| `analysis_run_output` | `analysis_run_output_id`, `analysis_run_id`, `output_entity_id`, `output_entity_type`, `artifact_version_id`, `publication_id`, `output_role`, `status`; artifact linkage plus derived staging receipt linkage with null artifact/publication IDs |

The preceding unchanged result writer also requires registered `algorithm` and
`algorithm_version` for the exact revision; `changeset`, `ingestion_run`,
`ingestion_item`, `learning_interaction`, `episode`, `decision`, `media_asset`,
`asset_location`, `transcript`, `transcript_segment`, `evidence`, `evidence_link`,
`domain_event` and `outbox_message`, with their current triggers and constraints.
It emits a technical recorded event/outbox row with quality pending and publication
blocked. This is an existing write side effect and must be included in the approved
ingestion target's outbox-consumer audit. It is not a launch/deploy instruction.

Use the existing `bridge_school_worker_principal` and `bridge_school_app_principal`
capabilities. Verify their effective existing SELECT/INSERT/UPDATE grants and
guard behavior through the runtime preflights and bounded rehearsal; never add
grants to make a failing canary pass. The app reader needs SELECT on all lineage,
candidate and receipt relations above and an actual read-only repeatable-read
transaction. The worker needs the existing writer capabilities; canon promotion
roles are unnecessary and must not be activated by this path.

## Rollout sequence and acceptance boundary

1. Reconcile fresh main, schema checksums, existing app/worker target and active
   school/revision registry. Confirm owner-approved service auth/DSN, file ownership,
   current outbox consumers, and rollback. Missing facts stop ingestion.
2. Publish/review the isolated draft and require the exact source SHA's contract
   and PostgreSQL CI. This PR has no live workflow dispatch, deployment, migration
   or cloud compute. The existing private v4.2 field job requires explicit manual
   opt-in and cannot run on PR creation.
3. After separate release authorization, deploy code with replay opt-in unset.
   Verify unauthenticated request denial and no automatic ingestion. Existing
   historical candidates lacking full lineage/receipt remain BLOCKED.
4. For one approved already-processed source, retain original master bytes and
   selection/index pins in private storage. Open one authorized worker transaction;
   call result persistence, ensure the exact provider source identity, then pass
   that actual result separately into staging persistence. Roll back the whole
   transaction on failed bindings/hash checks. Do not rebuild IDs from artifact claims.
5. Commit, close the worker connection, then use a separate authenticated internal
   HTTP request to read the exact returned candidate. Require exact source URL,
   offsets, source/run/asset/artifact IDs, payload/replay hashes and current gap
   codes. Retry the same input and require the same IDs with no new candidates.
6. Record distinct outcomes: ingestion attempted, staging persisted, source excerpt
   readable, independent evidence complete, knowledge accepted, student delivery
   authorized. `PERSISTED` and `READABLE_STAGING` only satisfy the first technical
   stages. A BLOCKED source observation contributes **zero accepted knowledge**;
   it cannot satisfy curriculum growth, semantic quality or production-readiness
   metrics. Even `STAGING_READY` is not canon acceptance.
7. Resolve identity/role, original-media, speech/frame, source-policy and semantic
   gaps using independently verified evidence before proposing knowledge acceptance
   through the existing separate canon process. Do not batch more sources merely
   because one blocked excerpt is readable. Require a useful reviewed outcome and
   approved bounded batch plan before expanding ingestion.

The standalone v4.2 seam does not yet install step 4 in a deployed orchestrator.
A full production ingestion and positive internal teacher request remain NOT_RUN
until that separate rollout is executed and evidenced. Real fixtures and receipts
stay private; public CI proves synthetic contracts and actual SQL behavior only.

## Rollback and next boundary

Production has not changed. Revert the local patch to remove the endpoint and
new persistence behavior. Stop the disposable local PostgreSQL after verification.
Before publication, the parent task must reconcile fresh main, file ownership,
review and the shared USD20 budget. Production import and a positive production
teacher request need their own explicit authorization and evidence.
