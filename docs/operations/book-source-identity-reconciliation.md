# Reviewed book source identity reconciliation

Preparation only; no production write or target admission follows from this code.

The registry's observed drive:<file-ID> form is compared to the public HTTPS
Drive view URL. Other legacy schemes, mismatched IDs, query strings, fragments
and credentials are rejected. Public bundle/teacher citations stay HTTPS.
Existing source UUIDs and locators are never updated.

## Missing source row: one bounded identity transaction (local code only)

The existing operator function
`database.book_source_reconciliation.reconcile_book_source_asset` accepts
`register_missing_source=True` for a
separately admitted source-review and source-identity receipt. Default `False`
preserves the previous requirement for an existing source. The optional path
uses the same operator-provided connection and one transaction; it does not
create a new registry, credential, grant, endpoint, Canon record or book
knowledge version. `new_book_source_id(school_id, public_locator)` computes the
UUID for a genuinely new row. If a single compatible row already exists, its
actual UUID takes precedence and must be bound in the identity receipt.

Before inserting, it locks `public.source`, verifies the target
`public.school` is active, checks both the exact HTTPS locator and supported
`drive:<file-ID>` alias within that school, and rejects ambiguous or conflicting
registry rows. It checks unknown enabled INSERT user triggers and INSERT
rewrite rules on `public.source`; the existing checks on `public.asset` and
`public.source_asset` remain. For a new row, the identity receipt must name the
deterministic source UUID and literal HTTPS locator. The insert sets only
`source_id`, `school_id`, `source_type='book'`, the reviewed title,
`canonical_locator`, and `status='active'`; owner/date/trust/rights columns are
not inferred. The existing asset path then inserts or reuses the immutable PDF
row with exact SHA-256/size/MIME and adds one `embodies` link. The function
returns three separate row counts. Exact replay must return `0/0/0`; any
conflict or injected interruption rolls back all three rows.

An observed absence of source and asset is not authorization to run this
function. A final identity
receipt must bind the active school UUID, exact locator, deterministic source
UUID, reviewed PDF byte size/page count and deterministic CREATE asset UUID.
Recheck source/asset absence, school scope, trigger/rule state and operator
INSERT privileges immediately before a live action. A stale absence result
must never be used as a second registration decision.

The new-source path adds at most **three metadata rows** in this one identity
transaction: one each in `public.source`, `public.asset`, and
`public.source_asset`. It uses existing compute and no new branch or service.
Runtime cost is one short existing-compute transaction plus possible compute
resume and three tiny rows of storage; a monetary amount cannot be calculated
without the then-current plan, compute state and measured duration. Local
synthetic checks do not meter production usage. `lock_timeout=5s` and
`statement_timeout=15s` bound individual waits/statements, not total elapsed
time or cost.

Before commit, any failure or `dry_run=True` rolls back every identity row.
After commit, source/asset/link are provenance and are retained on WORLD
retirement; `retire_book_world` archives only the selected WORLD version.
If source registration itself must be reversed before any knowledge uses it,
separately review all inbound references and use a controlled status retirement
procedure. Do not cascade-delete or repurpose the source UUID.

Once source binding and all review/delivery gates are met, the existing
`persist_book_world` stages one `external` knowledge version through
`public.knowledge_item`, `public.knowledge_version`,
`public.knowledge_version_source`, changeset/event/outbox. The separately
authorized `publish_book_world` step then makes that reviewed version visible
through protected `GET /v1/knowledge/query?lane=WORLD_EXTERNAL` and
`GET /v1/knowledge/teacher/book?stable_key=<object-id>`. Neither operation
inserts into `public.canon_activation` or changes `ai.system_rule`. These later
operations are outside this source-binding step.

A drive: alias additionally requires a source-linked, same-school immutable PDF
asset with the reviewed SHA-256 and byte size. The explicit operator reconciliation
uses an existing admitted connection and a fully bound source-review receipt.
It inserts at most one asset and one source link, preserves a compatible existing
asset ID, and rejects conflicting metadata. Unknown enabled user triggers or
rewrite rules on identity tables stop before inserts; do not disable them.
Dry-run executes the same path with transaction force_rollback. Interruption
rolls back all identity rows. Exact replay creates no new identity rows.

## Row accounting and admission

For one new atom the direct INSERT budget is two identity rows, six staging
rows, and three publication audit rows: eleven new rows total. It is not a
total-write budget: staging also updates one changeset; publication updates
one version and one changeset, giving fourteen direct affected-row operations.
Current staging/publication replay may update statuses even with zero new IDs.
For N atoms, first-run direct inserts are 3*N+8 and direct affected rows 4*N+10.

Foreign-key checks and locks are separate from writes. Identity-table unknown
user triggers/rules are rejected in code. Knowledge/audit triggers and the
existing outbox allocator, delivery workers and catalog synchronization may
have additional writes; their definitions, qualified role, readback counters
and separately approved bounds must be established before live admission.
Do not claim eleven as the end-to-end total or emulate fixture outbox UPDATE
in production. Dry-run calls on production are also not authorized by this PR.

Keep selected-key/source snapshots, exact content/review/anchor hashes,
recoverability evidence and an independently qualified writer. Exact-key
absence does not prove semantic dedup. Final source review, internal rights,
hash/asset reconciliation, delivery proof and parent serialization are required.
No new credentials or grants, source replacement, canon activation or blanket
book publication are permitted.

After successful publication, visibility rollback archives only the selected
WORLD version through retire_book_world, preserving source/asset/links and
audit evidence. It does not delete shared identity records, undo deliveries,
or restore retired versions. Restoration and delivery reconciliation require
their own verified operator procedure.

## Independent identity review and concurrent publication

Claim review alone does not bind registry identity. The separate trusted
book-source-identity-review-v1 receipt binds the complete source descriptor hash,
school/source UUIDs, literal stored locator, public citation URL, reviewed PDF
hash/size/page count, exact asset ID and CREATE/REUSE decision. Never derive it
from source text or accept it from an unauthenticated caller. It becomes part of
the publication/version hashes and stored provenance; legacy staging/rendering
require it. Old claim-review receipts do not implicitly authorize reconciliation.

Publication locks source -> asset/link -> exact derived_from citation -> selected
versions and rechecks exact
content/provenance state before writes. All identity locks remain through commit;
source-UUID link mismatch, changed evidence or missing assets fail closed.
Catalog reads and lock/hash functions use pg_catalog qualification, so an idle
session's TEMP relations cannot hide unknown identity triggers/rules.

Forced rollback covers transactional rows, not sequence advancement or external
effects of functions/triggers. A fixture outbox identity sequence can advance on
dry-run; production defaults and all trigger/delivery effects require their own
admission. Dry-run is never a read-only production preflight.
