# Bounded book WORLD rollout (not executed)

The runtime path is the existing PostgreSQL knowledge graph, existing protected
WORLD query, and `/v1/knowledge/teacher/book?stable_key=...`. SQLite is a local
test adapter only. The teacher response carries the persisted knowledge version
ID, an original short paraphrase, conditions, exceptions, and a book citation with
edition, rendition hash, zero-based PDF index, printed label, bbox and segment hash.
It does not call an LLM or DDS and does not activate Canon.

## Source-review handoff contract

Provide a private `book-material-staging-v1` bundle (see schema and staging guide)
and a separate `book-source-review-v1` receipt. Review is a trusted operator input,
not a publicly accepted self-attestation. The producer's M0 does not become M2
merely because a field is set. The source-review team must verify the exact bytes,
actual page/crop and statement before creating a PASS receipt.

Receipt fields:

- `schema`: `book-source-review-v1`.
- `review_id`, `reviewer`, `independence_group`: durable evidence identifiers.
- `assurance`: `I2`, `I3`, or `I4`, with evidence supporting that independence.
- `rendition_sha256`, `source_size_bytes`, `source_page_count`: actual source proof.
- `claims`: one to three objects, each containing `object_id`, `content_sha256`,
  `anchor_sha256`, `verification` (`M1` or `M2`), `checks`,
  `positive_example_ref`, `counterexample_ref`, `review_evidence_ref`.
- `checks` must contain exactly `source_anchor`, `semantic`, `conditions`,
  `exceptions`, `positive_example`, `counterexample`, `errata`, `internal_rights`, `semantic_dedup`;
  each must be `PASS` based on evidence. Unknown is not PASS.

`content_sha256` and `anchor_sha256` use UTF-8 canonical JSON with sorted keys,
no insignificant whitespace, no ASCII escaping and no NaN. Compute them using
`bridge_contracts.book_material.digest`. Bbox coordinates use explicit
`PDF_POINTS_TOP_LEFT` or `NORMALIZED_TOP_LEFT` units relative to the full page,
not crop coordinates. Explicitly convert legacy one-based indexes and pixel bboxes
using page dimensions. Unknown printed labels stay null; never copy a PDF index
into a field claiming a visible printed numeral. Use the exact 1-based PDF page
for the user-facing citation when no printed label is visible.
Record the crop/hash procedure in `review_evidence_ref`. A segment hash can bind
the exact crop bytes or a documented normalized text segment; never mix methods.

Each published atom needs nonempty conditions/exceptions, short original wording,
internal-use rights, no unresolved attached erratum, a verified positive example
and a negative/boundary example. Keep full text, crops, private evidence and real
corpus fixtures outside public Git. A review can exclude defective positions;
it must not invent missing cards or silently repair original diagrams.

## Migration and authorization

No schema migration, new service, new key, network change or GRANT is needed by
this candidate. `database.book_world_persistence.persist_book_world` receives an
already provisioned operator connection and explicit school/source UUIDs. It
never obtains credentials. Preflight must verify existing grants and source
binding; if they are insufficient, stop instead of changing privileges.

The source row must already exist in the correct school, active, with the same
canonical locator as the reviewed bundle. Source creation/reconciliation is a
separate reviewed operation. The adapter uses an advisory transaction lock for
that school/source and bounded statement/lock timeouts. No existing knowledge
content is overwritten. Stable-key/ID conflicts, changed run replays, retired
versions and version replacements all require explicit reconciliation.

First batch: at most three atoms, one source, one school. Maximum new staging rows:
3 knowledge items + 3 versions + 3 source links + 1 changeset + 1 domain event +
1 outbox = **12 rows**. The publication audit adds one changeset, one event and one
outbox row, for a maximum of **15 new rows** across both steps. No source/asset/Canon
rows. Same-run replay adds zero rows.
A new model/run with identical content/review preserves item/version IDs and
creates only the three audit/outbox rows for that run.

Knowledge versions remain `staged`: the existing WORLD query cannot retrieve them.
Knowledge, source links, event, outbox and committed changeset are written in one
transaction. Failure before commit exposes none of them. External delivery/catalog
reconciliation is a separately coordinated action. `publish_book_world` then
requires the original outbox to be published, the original changeset committed,
an exact publication-hash-bound catalog receipt, and all original version/source
bindings. Only its second atomic transaction changes the entire batch to `active`.
It records a separate publication audit; rerun is idempotent. The delivery receipt
is a trusted operator input with schema `book-world-delivery-v1`, fields
`publication_hash` and `catalog_receipt_ref`; never manufacture it from pending work.

## Serial execution plan

### Resolve the deployed connection before selecting a database branch

Historical branch names in canary manifests are not runtime evidence. The existing
`incident_db_probe` validates effective resident DSN parameters and can check the
session principal/database, but does not attest the server's Neon branch. Existing
`/healthz` returns only availability. A Light-host attestation concerns that host's
connection, not the Vercel WORLD reader.

The small `GET /v1/knowledge/validation/runtime-identity` route runs under the same
existing API-token dependency and no-store response middleware as WORLD retrieval.
It accepts no target, SQL, credential or publication parameters. It first reuses
the incident probe's effective-configuration checks, then opens the same connector
as WORLD retrieval and performs two SELECTs in a read-only transaction with server
statement/lock timeouts and explicit rollback. These timeouts are not a hard HTTP
request deadline; connection retries, transport and rollback have separate limits.
No school, student, knowledge or source rows are
read. Missing or inconsistent identity metadata returns a generic 503; raw database
errors, DSNs and environment values are never serialized.

The allowlisted result contains observed timestamp, optional validated deployment
SHA, actual checked host/port/database/principal and exactly the three Neon identity
tags. Tags must have their expected server contexts, configuration-file provenance,
matching reset values and no pending restart. The branch is returned as an
observation, with `OBSERVED_NOT_ADMITTED`; no historical branch is automatically
selected and no rollout gate is advanced.

Project and branch require `postmaster` context. Endpoint accepts `postmaster`
(the [current upstream Neon definition](https://github.com/neondatabase/neon/blob/main/pgxn/neon/libpagestore.c))
and the privileged `superuser` context used by the existing project attestor.
Neither context substitutes for the configuration-file/reset/pending checks.
Catalog functions are schema-qualified. Configured hostname, TLS in use and required
channel binding do not independently prove certificate hostname verification or
protect against a compromised server/control plane; fresh external corroboration
remains mandatory.

Minimal execution after parent coordinates a code-only merge/deploy:

1. Resolve the production alias to its actual deployment and Git SHA through the
   existing Vercel metadata read. Use the existing authorized operator HTTP client
   and resident API token to call the validation route once; never copy that token
   into prompts, files or logs. Anonymous requests must remain 401/403.
2. Bind the no-store response to that deployment. A missing revision needs separate
   deployment corroboration; a mismatching revision or failed identity is a stop.
   Compare the observed endpoint/project/branch with fresh control-plane evidence
   before selecting a branch for the existing read-only source/schema/ACL preflight.
3. Only after actual target, source/key dedup, source-review and recovery gates are
   satisfied may parent coordinate the bounded staging/publication window below.

This route grants no new role/key/permission and does not call a writer. Its
synthetic tests and disposable PostgreSQL rehearsal are not a live runtime receipt.

1. Parent confirms current main, deployment revision, affected schema/grants,
   source binding, reviewed source pack, I2 regression and exact selected IDs.
2. Preserve a private preflight snapshot of selected source and existing keys,
   counts and hashes. Verify restore/rollback in a disposable database first.
3. Parent coordinates merge/deploy and checks unauthorized teacher requests fail.
4. Execute bounded staging on the existing operator connection. Record changeset
   ID, version IDs and row delta. Rerun and require zero new versions/IDs. Verify
   WORLD/teacher return no staged records. Coordinate original outbox/catalog
   delivery and readback, then execute the separate bounded publication step.
5. Existing protected WORLD query must return those exact persisted IDs. Protected
   teacher route must return the same version and authentic book/page citation.
   Prove no source/candidate/Canon fallback, no BIDDING crossover, no public caching.
6. Reconcile publication audit outbox and catalog status/readback. No automatic workflow
   dispatch, background book worker or mass processing is part of this pilot.

## Rollback

`retire_book_world` receives the exact receipt version IDs (maximum three) and
school UUID. It verifies external/book ownership and absence of any Canon link,
then changes only those versions to `archived` in a transaction. WORLD and teacher
retrieval immediately stop returning them. All content, source links and audit
history remain. Capture the rollback operator receipt and reconcile any delivered
external projection; archiving does not unsend outbox events or alter catalog rows.
Replaying an archived version fails closed; reopening needs a new reviewed change.

## Proof limits

Unit/API tests with mocked database rows are not actual Neon evidence. The local
SQL rehearsal is not production publication. JSON schema validation and PostgreSQL
constraints are useful independent checks but do not prove bridge correctness.
Production completion requires the full persisted knowledge → protected retrieval
→ cited teacher response and separately reconciled outbox/catalog evidence.
