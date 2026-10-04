# Bounded book WORLD admission plan

Status: inactive proposal. No workflow dispatch, production dry-run, write,
reconciliation, delivery or publication is authorized by this document.
The current protected owner channel is qualified for its reviewed read-only
scope; that qualification does not authorize a different workload.

## Existing evidence and remaining privilege gap

The successful owner inventory at main f513afc4a1a4cae325b7baeeef5f4848dd29a7a5
(run 37235562804, attempt 1, job 111533883837) confirms the existing protected
runner's TLS policy, owner identity, server binding and read-only rollback.
Its report explicitly has write_admission=false and production_mutations=false.
Reuse this evidence and its existing credential path. Do not repeat runtime
qualification merely to obtain a book-specific label.

The installed canon probe checks SELECT/INSERT on 12 plan relations, separately
SELECT on school, and a fixed set of column UPDATE privileges. A book writer
has the following additional requirements; owner identity alone is insufficient.

| Relation | Existing probe coverage | Book operations to establish |
| --- | --- | --- |
| public.school | SELECT | SELECT; no write |
| public.source | SELECT/INSERT | SELECT and UPDATE on any column for FOR SHARE; no source mutation |
| public.asset | Not checked | SELECT/INSERT, UPDATE on any column for FOR SHARE; ROW EXCLUSIVE table-lock eligibility |
| public.source_asset | Not checked | SELECT/INSERT, UPDATE on any column for FOR SHARE; ROW EXCLUSIVE table-lock eligibility |
| public.knowledge_item | SELECT/INSERT | SELECT/INSERT |
| public.knowledge_version | SELECT/INSERT, UPDATE authority_class/review_status | SELECT/INSERT, UPDATE status for publication/retirement |
| public.knowledge_version_source | SELECT/INSERT | SELECT/INSERT and UPDATE on any column for FOR SHARE; no citation mutation |
| public.changeset | Not checked | SELECT/INSERT, UPDATE status/committed_at |
| public.domain_event | Not checked | SELECT/INSERT |
| public.outbox_message | Not checked | SELECT/INSERT; existing dispatcher owns delivery |
| public.canon_activation | SELECT/INSERT and selected UPDATE columns | SELECT only; no activation or write |

Check these with fixed pg_catalog.has_table_privilege,
has_column_privilege and has_any_column_privilege calls. SELECT alone does not
authorize the writer's row-locking SELECTs ([PostgreSQL SELECT](https://www.postgresql.org/docs/18/sql-select.html)); INSERT permits ROW EXCLUSIVE table locks ([PostgreSQL LOCK](https://www.postgresql.org/docs/18/sql-lock.html)). Check public schema USAGE and the
actual advisory/hash function EXECUTE privileges. Inventory actual defaults,
owned sequences/allocators, RLS policies, enabled user triggers, rewrite rules,
constraints and trigger-function definitions/privileges. Hash approved effect
definitions privately; observe any additional effect tables and their access.
Metadata privileges do not prove that a mutation will be admitted by RLS or
triggers. Do not execute INSERT/UPDATE/LOCK/FOR SHARE merely to test permissions.

The production WORLD application reader separately needs existing SELECT on
asset/source_asset for legacy citation verification. The owner's capabilities
do not establish those of the application principal. If any required existing
privilege is absent, stop; do not grant or substitute another credential.

## Read-only channel extension to review first

The current entry point requires OWNER_PROBE_SCOPE=canon-readonly. Do not pass
book data through that scope or expose caller SQL. Propose a separate fixed
book-readonly capability/effect inventory in the same protected main-only
environment, with repository/actor/workflow/exact-main checks before credential
access and live-main checks before/after. Reuse the existing strict credential
parser and previously qualified binding. Keep transaction READ ONLY with forced
rollback and fixed timeouts. Return allowlisted booleans, relation names and
approved definition hashes; never return DSNs, source rows, book text or private
bindings. Do not install or dispatch the extension during another main slot.

An eventual write launcher is a separate reviewed change and admission. It must
accept only a sealed private pack and exact approved phase, verify its hash and
budget before touching the existing credential, forbid arbitrary SQL/URLs,
serialize with parent, and emit a sanitized receipt. No public writer route or
default-enabled schedule is part of this proposal.

## Private evidence required before any write

Start with exactly one selected CARD_PLAY atom, one existing source and school.
The book's other pilot atoms and unresolved errata are not admitted by inference.
Keep BIDDING and Canon out of this plan.

1. **Semantics and source anchor:** inspect the actual PDF page/crop and bind exact
   rendition, edition, page index/printed label, bbox units and segment hash.
   Verify the short original statement, every condition/exception, positive
   example and boundary counterexample. Keep author solution, DDS and
   single-dummy conclusions distinct. Missing author cards cannot be repaired
   by invention or treated as strict PBN; structural full-deal checks alone
   cannot prove a bridge result. Record independently reviewed evidence for
   each of the nine book-source-review-v1 checks. Unresolved selected-atom
   errata, absent examples or PENDING semantic_dedup stop publication.
2. **Rights:** bind an explicit internal-use rights decision to that exact
   rendition and selected paraphrase; rights_notes_present alone is not
   authorization. Keep full text/crops and personal evidence outside public Git.
3. **Semantic dedup:** read the selected school's existing WORLD/SOURCE/Canon
   candidates and relevant fact/projection stores using approved bounded
   selection. Compare meaning, conditions and system profile, not just stable
   keys. Record candidates, decisions and snapshot/time privately. Exact-key
   absence proves only exact-key absence.
4. **Final identity review:** produce a separate trusted
   book-source-identity-review-v1 receipt binding complete source descriptor
   digest, actual school/source UUIDs, literal stored locator, public citation,
   PDF hash/size/pages, exact asset UUID and CREATE/REUSE decision. Decide reuse
   from fresh asset/link metadata. The claim receipt cannot substitute for this
   identity review; do not manufacture PASS from a draft or code-review report.
   Validate both receipts against the unchanged selected private bundle.
5. **Recovery:** capture a private pre-write snapshot of exact selected records,
   IDs, hashes, statuses, effect definitions and counters. Verify recovery in
   the disposable database using the actual reviewed effects: interrupted
   reconciliation/staging/publication, exact replay and visibility retirement.
   Existing synthetic PostgreSQL proof is useful but does not prove all live
   trigger/delivery effects. Restore rehearsal must cover the selected scope.
   Archive retirement preserves identity/audit rows; it does not reverse an
   external delivery. Define projection recovery and reopening separately.
6. **Delivery:** identify the existing dispatcher and catalog projection route,
   its admitted role, event compatibility and idempotency key before staging.
   After staging require actual committed changeset + published original
   outbox + catalog readback tied to the exact publication hash/version IDs.
   Never emulate fixture outbox UPDATE or create a receipt from pending work.
   The publication audit outbox needs its own post-publication reconciliation.

The current owner inventory closes only the transport/runtime part. Final
receipts, rights, semantic comparison, actual effect bounds, recovery and
delivery evidence must be supplied by their responsible reviewers/operators.
If supported source download is denied, stop that path and report the denial.

## Separately coordinated execution, once all gates are evidenced

Parent admits exact main/deployment, private pack hash, reviewed writer wrapper,
qualified existing role, effect definitions, selected IDs, rollback procedure
and a finite end-to-end affected-row budget. The known one-atom direct budget is
11 INSERTs and 14 affected-row operations across identity + staging + publication;
it excludes trigger/default/allocator/delivery/projection effects. Add and
approve their observed bounds rather than calling 11 an end-to-end cap.

1. Reconcile at most one immutable asset and one embodies link. Retain the
   source UUID/locator; read back identity and record actual deltas. Unknown
   enabled identity-table triggers/rules stop before inserts. No delete.
2. Stage exactly one atom. Record changeset/version IDs and transactional row
   deltas. Verify staged records are invisible to WORLD/teacher. Any deliberate
   replay is separately bounded: zero new IDs does not imply zero UPDATEs.
3. Use the existing dispatcher and obtain the hash-bound catalog delivery
   receipt from real readback.
4. Publish that exact version with identity/citation locks retained through
   commit. Read back active version, immutable asset, citation edge and audit.
5. The protected WORLD/teacher path must return the same persisted version and
   authentic book/page/bbox/hash citation, with conditions/exceptions and
   WORLD_EXTERNAL authority. Verify no SOURCE/Canon fallback, no BIDDING
   crossover and no public caching. Use the resident token without exporting it.
6. Reconcile publication audit delivery/catalog state. If visibility must be
   retired, archive only the admitted version and prove it disappears from
   WORLD/teacher; retain identity/evidence and reconcile delivered projections.

A future production dry-run executes attempted writes and may advance sequences
or invoke external effects despite transaction rollback. It requires explicit
write admission; it is never the read-only inventory above.
