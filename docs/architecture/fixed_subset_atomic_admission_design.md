# Fixed-subset atomic admission: local design candidate

Date: 2026-10-12. Governance: ASSURED. Status: DESIGN / NOT ACTIVATABLE.
No migration, database connection, runtime operation, publication or deployment
is part of this candidate. All new Python outputs remain execution-disabled.

## Baseline and scope

Fresh main is `d49feb4cb333b041928a5d37efb56a9f97dd1822`; PR #2158 is still a draft
at `717a4d92bc51bfc76f3e6455911e8b9900d83beb`, tree
`33472547dec31a228bdda7023c22fec5da45cce2`. The isolated local baseline has that
exact tree; its synthetic local commit is not the public PR commit. PR #2158,
its five files, queue API, worker, adapters, auth, workflows and migrations are
unchanged. `.agents/skills` is absent. AGENTS.md and the canonical
`docs/governance/SCHOOL_GOVERNANCE_SYSTEM_V1.md` govern the work.

Goal: admit one trusted, approved, fixed subset into the existing durable queue
once, keeping actual batch/job UUIDs and source revisions recoverable. No new
planner, scheduler, queue, retry engine or automatic release. Existing 1..1000
batch bounds stay fixed; archive discovery remains separately capped at 500
direct children per capture. Duration/cost remain unknown when absent.

The source candidate adds `universal_video/admission_binding.py`, a pure checker
of immutable binding rows and separately scoped source observations. It does
not implement the server transaction described below. Its tests prove local
consistency rules only. The SQL test plan is a specification for real PostgreSQL
sessions, not an executed test suite or a migration runner.

## Current code contracts and gaps

| Existing source | Actual behavior and required consequence |
| --- | --- |
| `universal_video/video_queue.py`, `enqueue_manifest` | Opens and commits its own psycopg connection. Do not call it inside the new atomic path. Call the existing SQL function on the server's single transaction. |
| `database/migrations/0056_universal_video_queue.sql`, `enqueue_drive_batch(text,text,text,text,text,text,text,text,jsonb)` | Validates bounded manifest rows; locks `hashtextextended(request_key,0)`; inserts batch/jobs/events and `pg_notify`. Existing request replay compares header counts/hash, not every persisted job. New path must compare exact manifest and mappings. |
| 0056 `video_queue.job` | Source/stable-key uniqueness is only `(batch_id,source_file_id)` / `(batch_id,stable_job_key)`. It does not prevent another request or historical batch using the same source. |
| 0056 `claim_job`, `heartbeat_job`, `retry_job` | Reuse job IDs, lease tokens, owners, expiry, SKIP LOCKED, retry delays. SQL retry accepts max 1..10; QUEUED claim lacks a hard attempt-count predicate. The new managed path must enforce 3, not assume existing primitives do so globally. Retry's nullable `<>` comparisons need an explicit non-null/exact fence in the new boundary. |
| `universal_video/neon_worker.py`, `verify_claimed_source` / `process_claim` | Matches queue projection, requires current version/time to be present and compares before/after the current attempt. It does not compare them to an approved capture revision. Same-size, null-checksum changes before attempt one can pass. |
| `universal_video/subset_contract.py`, `_sources` / `_fresh` | Fingerprint comparison is useful, but ledger/lease freshness does not timestamp the source metadata itself. Native observations need independent trusted freshness. |
| `tests/test_universal_video_subset_admission_fake.py`, `admit` | In-memory model compares mutable batch status on recovery and checks current sources before recovery. Neither condition belongs in immutable lost-response recovery. Model is not SQL evidence. |
| 0057 + 0058 `finish_job` | Canary REVIEW_READY produces CANARY_REVIEW with released_jobs=0. Preserve this HOLD gate, including a one-file subset. |

## Smallest server-owned data boundary (future, not created here)

Use the `video_queue` schema and existing batch/job/event tables. Four bounded
support records are proposed; they are bindings and authority evidence, not a
second queue:

1. `subset_approval`: UUID approval ID, exact canonical contract bytes + SHA256,
   exact canonical manifest bytes + SHA256, parsed values, plan/subset hashes,
   approver identity/provenance, server approval time, expiry/revocation fields.
   Immutable approved content; revocation may change authorization state only.
   Store original full approved plan/capture reference in trusted provenance so
   server ingestion can verify the selected subset. A caller's self-consistent
   hash or UUID is not approval.
2. `source_observation`: UUID observation ID, approval/contract hash, purpose,
   server-observed time/expiry, exact source IDs/native fingerprint records and
   nonce. Admission scope binds request key + one-time nonce; attempt scope binds
   request key + actual job ID + lease token + attempt count + source ID. Only the
   trusted collector can create it; request payload timestamps cannot create trust.
   For multiple reads, the envelope starts at the oldest constituent observation,
   not collection completion. Refreshing one row cannot renew the others.
3. `subset_admission`: unique request key and approval ID, immutable exact
   manifest/contract/plan/subset digests, eventual unique batch ID, exact binding
   bytes + SHA256 and server commit receipt. A transient transaction-local draft
   exists only to support job INSERT guards; it must never survive COMMIT.
4. `source_reservation`: primary key `google_drive:<file_id>`, immutable owning
   request/admission and approved fingerprint, eventual actual batch/job mapping.
   Alternatively a `HISTORICAL_HOLD` tombstone with provenance to all preexisting
   jobs. Never free the key when status becomes FAILED/REVIEW_READY/REVIEW.

Use immediate primary/unique keys for exclusion. Deferred constraint triggers
and FKs verify the complete final bijection between exact manifest, jobs,
reservations and binding before COMMIT. Every participating table's mutation
must schedule validation. A checksum string supplied by a caller is not enough.

Digest definitions remain PR #2158's UTF-8 sorted-key compact finite JSON.
Do not hash PostgreSQL `jsonb::text` and assume it equals Python serialization
(whitespace, numeric formatting and Unicode can differ). Approval ingestion
stores verified canonical bytes and their digests; the server verifies bytes
against stored parsed data. The future SQL implementation must cross-test its
canonicalization against shared vectors, including Unicode, null and numeric
annotations, before activation. Existing 0058 canonical JSON serves its own
terminal-artifact contract and is not automatically a new contract codec.

## Atomic admission contract

Proposed signature (not implemented):

`video_queue.admit_approved_subset(approval_id uuid, request_key text,
contract_sha256 text, queue_manifest_sha256 text, observation_id uuid) -> jsonb`

The request names immutable server records; it cannot supply a replacement
manifest, approval, timestamp, source revision or arbitrary batch/job UUID.

One server transaction at READ COMMITTED. The future trusted caller owns the
outer transaction, calls the SQL entrypoint directly, commits once and only then
exposes the binding. The SQL function itself does not COMMIT; its returned value
is tentative until the outer COMMIT acknowledgement (or immutable recovery).

1. Bound statement/lock timeouts. Take the same request advisory transaction
   lock as 0056: `pg_advisory_xact_lock(hashtextextended(request_key,0))`.
2. If an admission already exists, compare immutable authority/content and
   recover it; do not repeat enqueue or require a fresh Drive read. If a legacy
   batch exists with this key and no binding, return HOLD, never adopt it.
3. Lock approval row against revocation. Verify server provenance, current
   authorization, exact contract/manifest bytes/digests, canary, full row shape,
   bounded count and all disabled flags. No widening, implicit filtering, or
   changing the reviewed selection/profile/destinations.
4. Lock and validate an unconsumed trusted ADMISSION observation for this exact
   approval/contract/request nonce. Insert the transient admission draft and
   reserve ALL exact source identities through the immediate primary key, in
   source identity order `COLLATE "C"`. All callers use the same insertion order.
   This covers absent keys too: `SELECT FOR UPDATE` on a missing row alone does
   not. On insertion conflict, inspect the owner in a separate READ COMMITTED
   statement with a fresh snapshot, not a single stale `ON CONFLICT` CTE. Another
   owner or historical tombstone is an explicit conflict; never skip a source.
   All reservation waits precede the final freshness check. A competing writer's
   rollback may allow this insertion to succeed after its observation expired.
5. Recheck approval expiry and source-observation age with the server clock
   after waits: `observed_at <= now < expires_at`, lifetime at most 30 seconds.
   Compare approved native parent/version/modifiedTime/size/MIME/all checksums
   and strict manifest name. Version and time are mandatory even for null checksum.
   A stale observation rolls back and needs a new trusted read, not a retry with
   the same stale blob. External Drive and Postgres cannot be one transaction:
   this closes admission's evidence window, not all subsequent remote races.
6. With all reservations held and freshness revalidated, within this very
   transaction call existing `video_queue.enqueue_drive_batch` with
   the stored exact manifest. Do not call Python `enqueue_manifest`, open a
   second connection, commit early or download anything. If an additional
   potentially blocking prerequisite precedes authorization/enqueue, recheck the
   server clock after that wait too; do not move reservation acquisition here.
7. Read actual `batch_id` and actual `job_id` per source from queue tables.
   Verify exact count/set/order and every immutable header/projection field.
   `build_binding` is the offline reference for this comparison; do not derive
   queue UUIDs from file IDs or trust just an enqueue summary.
8. Persist complete mappings + original contract/manifest digests + immutable
   binding and its digest; finalize reservation references and consume the
   observation. Deferred checks reject drafts, missing or extra jobs, orphan
   reservations or any mismatch. The outer caller commits once, then returns the
   binding. Existing queue notifications become visible at COMMIT.

Freshness is promised at the final locked server authorization check after waits
(step 5), using `clock_timestamp()`, not at COMMIT or response time. An additional
deferred expiry check can reject ordinary delayed commits, but is not a universal
commit-time fence: `SET CONSTRAINTS ALL IMMEDIATE` may execute it while fresh and
then the transaction can idle past expiry before COMMIT. The SQL matrix explicitly
records that limitation. It does not remove the independent fresh observation
required before every attempt. Deferred structural checks still enforce the
final bijection; every subsequent relevant mutation must schedule validation.

Faults after reservation, enqueue, mapping or binding must roll back all new
rows, observation consumption and notifications. Recovery must not consume an
observation again. UUID/sequence allocations can have gaps; gaplessness is
not an invariant. A lost COMMIT acknowledgement is unknown, not failure: recover
by original request key and pinned digests, never generate a new key.

Do not acquire existing batch then job locks opposite worker's job-then-batch
order. Recovery reads immutable fields without locking mutable status rows.
On deadlock/serialization/lock timeout, abort the whole attempt; bounded retry
first checks immutable recovery and refreshes expired source evidence.

## Legacy races, privileges and cutover

A new function's private advisory lock or a one-time table lock cannot fence
legacy enqueue after COMMIT. Permanent enforcement is required before admission:

- Runtime enqueuers receive EXECUTE on the new boundary only; revoke legacy
  `enqueue_drive_batch` EXECUTE from `bridge_school_app` and every other writer
  path. Existing SQL function remains callable by the non-login function owner
  inside the admission transaction. No runtime role inherits table ownership,
  superuser/BYPASSRLS, approver/collector authority, trigger-disable or raw DML.
- SECURITY DEFINER functions use a fixed qualified search_path. Trusted
  approver, collector and enqueuer are distinct capabilities. No caller-set
  GUC/session variable can stand for approval or active admission.
- A persistent job INSERT guard checks actual batch request, the persisted
  in-transaction admission draft and exact reserved manifest row/owner. It must
  permit the trusted draft before actual job UUIDs are known; a deferred check
  later requires finalized binding. Legacy calls, including those which began
  before a permission change, cannot insert jobs without this authority.
- Freeze batch identity, request/source kind/folders/profile/revision/hash/count/
  total/canary/shadow flags/created_at; freeze job ID/batch/sequence/source metadata/
  stable key/canary/created_at. Freeze admission content, bindings and reservation
  ownership. Reject identity UPDATE, DELETE and TRUNCATE; normal lease/status/
  output changes remain available only through fenced worker functions.

Future cutover requires a separately approved maintenance transaction: quiesce
and drain legacy producers/workers and outstanding/prepared transactions; acquire
bounded locks; inventory all existing source IDs in ALL statuses; backfill
HISTORICAL_HOLD reservations; install persistent guards and privileges atomically.
For historical duplicates, retain all rows and record the conflict; do not choose
a winner, erase evidence or release a key. Only an independently reviewed
historical reconciliation may later change the policy. Recheck privileges under
actual runtime roles. If drain, backfill or guard proof is incomplete, stay HOLD.

## Recovery and attempt execution are different gates

Proposed read function (not implemented):

`video_queue.recover_subset_admission(request_key text, contract_sha256 text,
queue_manifest_sha256 text) -> immutable binding / NOT_FOUND / HOLD`

Recover only committed server records and actual immutable queue mapping. A
RUNNING/CANARY_REVIEW status, rotated lease, terminal output, elapsed time,
expired/revoked approval or subsequently changed Drive revision does not erase
the fact of admission. Never reenqueues. Return progress separately, never in
the immutable receipt/digest. Missing legacy binding, partial rows, altered
digests/UUIDs or an incomplete bijection is HOLD, not a repaired success.

Recovery still requires caller read authorization. Approval revocation locks the
same approval row as admission and execution authorization: revocation first
blocks new work; admission first preserves its committed receipt, while later
attempt permits stop. Keep approval content/history after revocation. Use
`project_lease_binding(...)["binding"]` after full immutable revalidation when
calling existing `subset_contract.check_attempt`; it preserves that validator's
strict five-key binding and two-key job schema without weakening it.

Before every attempt, including the first, read a fresh trusted source observation
and compare it with the APPROVED fingerprint. A before/after pair that both
observe a changed source is insufficient. Bind observation to actual job/token/
attempt and recheck lease owner, non-null token, LEASED status and server expiry
immediately before processing and before terminal submission. Re-read source
after processing too. A mismatch or unknown revision blocks work; it cannot
silently update the approved binding. External mutation during reads remains a
fail-closed detection problem; these checks do not create remote snapshot isolation.

Reuse existing claim, heartbeat, retry and finish primitives. A server boundary
fixes retry max=3 and compares token/owner using null-safe equality. The managed
row guard must prohibit a fourth lease and requeue after exhausted attempt 3.
Do not fix this with a second scheduler or just a Python assertion. Existing SQL
alone does not prove those invariants. An upgraded worker source gate and a
persistent boundary excluding old worker credentials from managed jobs are
activation prerequisites: current resident code could otherwise claim the new
QUEUED_CANARY job immediately. No admission is enabled before that boundary is
reviewed and proven with the old worker role in the SQL matrix.

The future worker boundary requires a dedicated authenticated v2 DB principal
which old credentials cannot assume, with legacy/inherited/public EXECUTE paths
revoked. SECURITY DEFINER `current_user`, `p_worker_key` and a caller-set version
GUC are not authenticated worker identities. Claim eligibility must select only
eligible finalized admissions under the controlled rollout epoch. Calling the
generic claim then rolling back ineligible jobs can starve behind old jobs; the
existing claim selector needs a reviewed eligibility predicate, keeping its API
and lease algorithm. Fixed-3 retry is the intended path; also reject attempt
counter resets and enforce monotonic +1 on each lease. Skip or terminalize
exhausted QUEUED rows so a fourth-lease guard does not create a liveness trap.
Historical rows already exceeding three attempts stay HOLD for reconciliation.

Canary finish keeps 0057/0058 `CANARY_REVIEW`, `released_jobs=0`; remainder stays
PENDING_CANARY. No release function or grant is introduced here. Review/release
of the exact remainder is a separate authorized action, with fresh evidence.

## SQL terminal evidence fixture

The new synthetic `tests/fixtures/universal_video_terminal_sql_v2.json` targets
0058/001 and /002 together. Receipt schema is `/v1` even though the finish gate
is called v2. Both manifest and receipt need identity/shadow/publication fields.
Both ordered artifacts need Drive ID, `gdrive:file:` locator, MIME, output parent,
positive numeric size, SHA256 and native `modified_time`/digit-only `version`.
Manifest/receipt hashes must be recomputed after mutations when testing semantic
guards, otherwise a hash mismatch can hide an untested missing-field check.

0058/002 installs `video_job_terminal_revision_check NOT VALID`; it still applies
to new/updated REVIEW_READY rows. The fixture is local test data, not evidence
of a real Drive artifact or a passed SQL transaction.

## Real SQL verification and rollback contract

`database/tests/plans/fixed_subset_admission_v1.json` specifies multi-session
PostgreSQL statements, barriers and expected observations. Required future
server functions/tables/roles are absent. No scenario has been executed. Offline
JSON/fixture/unit checks must never be reported as database concurrency proof.

Run only after separate authorization against a disposable PostgreSQL instance:
baseline 0056/0057/all 0058 pieces, future admission DDL, actual runtime-role
privileges, independent sessions and server lock observations. Include rollback
at each transaction boundary, ambiguous commit recovery, both legacy race
orders, historical duplicates, raw DML/session spoofing, stale evidence after
lock waits, token rotation/null tokens/fourth attempt, and full SQL terminal
fixture with native-revision/size negative cases. Do not replace barriers with
arbitrary sleeps or mocks, and do not give application credentials superuser.

Rollback design precedes any future migration: deactivate admission and quiesce
affected workers, preserve bindings/reservations/events as evidence, and retain
the permanent source guard. Reverting blindly to legacy 0056 admission would
reopen duplicates, so it is NOT a safe rollback. Before first admitted row, an
owner-approved schema reversal may be possible after an empty-data assertion;
after any committed admission use a forward fix or retain read-only support
records. Never drop reservations or reopen legacy permissions merely because a
canary failed. Exercise both empty rollback and nonempty rollback refusal in
the disposable database, plus backup restoration before production changes.

Next bounded step: implement the specified database constraints and server
entrypoint, then prove the multi-session matrix on an explicitly authorized
disposable database. Publication, production migration and runtime enablement
remain separate decisions; the current deliverable is local design evidence.
