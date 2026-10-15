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
Before any asset or link insert, it inspects every PDF/`embodies` link already
attached to that source. A different SHA-256, byte size, MIME, school or asset
UUID is a conflict, even if a matching PDF is also linked. Identity-table locks
serialize competing inserts until commit; the check uses a fresh READ COMMITTED
snapshot. A reused Drive alias with another PDF must be reviewed as a separate
identity decision, not rebound by this operation.
The asset and source-asset table locks also serialize unrelated identity writes
for this short transaction; the five-second lock timeout bounds contention.

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
asset ID, and rejects conflicting metadata. Enabled user triggers and rewrite
rules on asset/link tables stop before inserts. For optional source creation,
the gate checks enabled INSERT triggers and INSERT rewrite rules; UPDATE-only
source guards are permitted because this operation never updates source rows.
Do not disable guards.
Dry-run executes the same path with transaction force_rollback. Interruption
rolls back all identity rows. Exact replay creates no new identity rows.

## Row accounting and admission

Identity registration and later knowledge staging/publication have separate
transactions and authorization. For an already provisioned active school, an
identity transaction inserts at most one asset and one link when the source
already exists, or **three rows** when source creation is also admitted:
one source, one asset and one link. Exact identity replay returns `0/0/0`.
No source UPDATE or school INSERT is part of this operation.

For N new atoms from that one source, staging inserts 3*N+3 rows and publication
inserts three audit/outbox rows. First-run direct budgets are:

| Identity state | Identity INSERTs | Total direct INSERTs | Direct affected-row operations |
| --- | ---: | ---: | ---: |
| Existing source, missing asset/link | 2 | 3*N+8 | 4*N+10 |
| Missing source, asset and link | 3 | 3*N+9 | 4*N+11 |

For one atom this is **11 inserts / 14 direct affected-row operations** with an
existing source, or **12 inserts / 15 operations** with a newly created source.
The extra operations are one staging changeset UPDATE plus N publication version
UPDATEs and one publication changeset UPDATE. Compatible existing identity rows
reduce new-row counts; they do not permit additional rows elsewhere.
Current staging/publication replay may update statuses even with zero new IDs.
These are direct logical row budgets, not physical storage, total database writes,
sequence effects, trigger effects or external delivery/catalog work.

Foreign-key checks and locks are separate from writes. Unknown enabled user
triggers/rules on asset/link tables and unknown source INSERT effects are
rejected in code. Knowledge/audit triggers and the
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

## Contention, writer compatibility and release preflight

The source-registration path takes `SHARE ROW EXCLUSIVE` on `public.source`
before alias lookup; both modes then take that lock mode on `public.asset` and
`public.source_asset` before checking PDF bindings. These are table-wide locks:
different schools and different books still contend. The source lock also blocks
unrelated INSERT/UPDATE/DELETE writers. Identity locks block conflicting identity
DML, and PostgreSQL holds them through commit or rollback. Plan a short serialized
metadata window; do not infer parallel registration throughput from the unit tests.
The five-second `lock_timeout` can reject an otherwise valid registration.
The operation rolls back on contention and does not automatically retry.
The fifteen-second `statement_timeout` applies per statement, not to total
transaction duration, total cost, or an operator's `before_commit` callback.
External writers using a reverse lock order may deadlock; do not classify a
lock/deadlock exception as an identity-contract rejection.

Static repository examples of other shared-table writers include
`database/tournament_facts_importer.py` (source insertion) and
`database/video_result_persistence.py` (asset, source and link insertion,
including asset-before-source paths). They need inclusion in the contention and
lock-order assessment. A source-code inventory is not proof of which versions
or processes are running.

These guarantees apply to the updated reconciler, including its
`register_missing_source=False` mode. They do **not** make the old reconciler
safe: its wanted-SHA-only query can still discard an existing X binding when
checking Y after a table-lock wait. Table serialization alone does not repair that
old validation. There is no database uniqueness constraint that permanently
enforces one canonical alias/PDF binding against arbitrary writers. Old scripts,
jobs, imported modules and workers must be excluded before admitting a write.
More stringent `require_source_asset` validation also rejects extra PDF links
and PDF links with a non-`embodies` relation; existing data needs review.

The following preflight is mandatory before any separately authorized live
identity action; it has not been performed by this local rehearsal:

1. Pin fresh main, deployment and **every writer's actual running code**, inventory
   operator scripts/jobs/workers and drain or stop incompatible old writers under
   the appropriate operational authorization. A new API deployment alone is
   insufficient. If writer compatibility cannot be established, stop.
2. Corroborate the actual target project/branch/database/principal with fresh runtime
   and control-plane evidence. Verify existing privileges without adding grants,
   credentials, roles or services.
3. Admit the exact school/source/asset IDs and trusted source/identity receipts,
   PDF SHA/size/page count, internal rights, source descriptor and both HTTPS/Drive
   aliases. Recheck active school, fresh source/asset state and **all** relevant
   PDF links immediately before the transaction; mismatches require reconciliation.
4. Inspect real defaults, enabled triggers, rewrite rules, constraints, partitioning
   and other schema effects on all three identity tables. Resolve unknown effects
   through review rather than disabling guards. The fixture is not this evidence.
5. Check current long/idle transactions and lock contention, choose an acceptable
   serialized window and bound retries explicitly. On lock/deadlock failure retain
   zero-commit evidence and repeat fresh identity checks before any admitted retry;
   never blindly retry a changed registry decision.
6. Freeze the three-row identity budget (or the smaller reuse case), immutable
   before-state and recoverability plan. Record actual `1/1/1` or reuse counts,
   committed state and independent readback. Dry-run is a write transaction with
   rollback and is not authorized merely by a read-only preflight permission.
7. Establish safe retention/retirement and inbound-reference checks for a committed
   source binding; do not cascade-delete provenance. Staging, delivery, publication
   and Canon remain separate gates, with their own row/effect budgets and evidence.

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


## Shared writer protocol (local candidate, 2026-10-15)

`database/source_registration.py` gives book reconciliation (both modes), video persistence, tournament import and standalone Drive source-identity metadata persistence one source-table barrier before their own advisory locks, row locks or DML. The barrier is held until the entire transaction ends; asset/source_asset writes follow it. All participating transactions require READ COMMITTED. A caller-owned video transaction may start with read-only work, or explicitly call `lock_source_registration` before its first application write/row/advisory lock. A transaction already holding application write/row or advisory locks without the barrier is rejected; RR/SERIALIZABLE are rejected rather than silently downgraded.

Registration checks both the globally unique expected UUID and every recognized Drive alias in the requested school, including inactive rows. HTTPS Drive-view, `drive:` and `gdrive:file:` are equivalent for conflict detection. The exact `source_identity` keys `google-drive:<id>` and `google-drive:file:<id>` also identify the file; other provider/participant keys and arbitrary JSON attributes do not. One UUID with both key forms is deduplicated. Participating Drive writers reject non-string, colon/URL, whitespace or empty IDs before SQL instead of stripping or coercing them; this prevents collisions between the two native-key namespaces. Standalone metadata persistence validates the same source UUID/canonical file identity before its existing metadata UPSERT. Multiple rows, a different UUID/school/type/status or an unrelated locator cause a contract error and transaction rollback. No source is adopted under another UUID, merged, renamed, or repaired. Video/tournament replay may retain any recognized alias for the same UUID/file/type and preserves the stored title. Book creation retains its stricter receipt-bound locator/title/type policy; existing-only book behavior and `book_source_matches` authorization are unchanged. In particular `gdrive:file:` is not newly authorized as a book receipt alias.

The shared writer protocol needs no migration or grant change. The existing book create path still requires UPDATE privilege on at least one school column for its school FOR SHARE lock; unchanged migrations do not grant this to the worker role. The operator checks effective privileges and refuses this mode if unavailable, without granting access or weakening the lock. Existing-source mode does not require that school UPDATE privilege. The protocol serializes complete video/tournament transactions, so contention is global across schools. It does not enforce a database uniqueness constraint against old or external SQL clients. Exclude those writers through coordinated admission/drain, preserve pending jobs and receipts, and keep the exclusion on rollback. An old mixed-version writer can still violate the invariant after the barrier is released. Do not infer live readiness from a package upgrade or a successful synthetic test.

Use the separate `database.book_source_operator` one-shot CLI for a reviewed, bounded book identity operation. It defaults to dry-run and never publishes WORLD/canon or teacher output. Its target/principal checks do not establish an actual operator connection or fresh endpoint-to-branch mapping: those remain readback obligations through existing access. `autopilot_light_worker_login` is not a book writer. See `book-source-operator.md` for exact inputs, modes, recovery and target checks.
