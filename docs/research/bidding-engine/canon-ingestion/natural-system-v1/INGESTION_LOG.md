# Canon ingestion log: Natural bidding system v1

Status: **ACTIVE / APPEND-ONLY WORK LOG**  
Program: issue #609 - School Canonical Bidding Engine  
Working branch: `bidding/canon-natural-system-ingestion`

## Purpose

This file records, in chronological order, every material action used to turn the approved two-page PDF into machine-readable SCHOOL CANON.

The log is evidence of the process, not a substitute for the source PDF. Entries are appended; earlier entries are not silently rewritten. Corrections must be recorded as later entries that identify what changed and why.

## Standing boundaries

- The authoritative source is the approved PDF identified in `SOURCE_MANIFEST.json`.
- Canonical scope covers every opening, response, continuation and rebid explicitly shown in the PDF.
- The PDF is authoritative; automated text extraction is only an aid.
- Missing or ambiguous source content is recorded as a gap or ambiguity and is not completed from general bridge knowledge.
- BEN, BBA, Pons, books and other world sources may be used for comparison or testing, never for silent correction of SCHOOL CANON.
- No rule is activated before provenance, source location, positive/negative/boundary tests and conflict checks are complete.
- Runtime decisions may use only the acting hand and public auction information.
- Production changes must be reported separately from candidate preparation.

## Entry format

Each entry records:

- timestamp/date;
- role/function performing the action;
- action;
- input/source;
- output/artifact;
- verification;
- database effect;
- unresolved questions;
- next permitted step.

---

## 2026-08-27 / LOG-0001 - Source located

**Role:** Coordinator / Curator  
**Action:** Located the two-page PDF in the School Google Drive and downloaded an exact working copy.  
**Source:** Google Drive file `1HkVff4iH2e3HT5kwblvd3mY8TUQPR6jf`, title `1569510427630_натуральная система торговли - color(1).pdf`.  
**Output:** Local working copy for inspection.  
**Verification:** PDF opens successfully; two landscape pages; embedded text and fonts are present.  
**Database effect:** None.  
**Unresolved:** None at source-identification level.  
**Next:** Establish byte identity and canonical authority.

## 2026-08-27 / LOG-0002 - Byte identity verified

**Role:** Observatory  
**Action:** Compared the downloaded Drive file with the user-facing copy.  
**Output:** SHA-256 `dc9678da5ab19a897c3f1fbd785cc6f7b0ddd9d70d90d895743db615a2fdd3d6`.  
**Verification:** Both local files have the same SHA-256; file size is 113399 bytes; page count is 2.  
**Database effect:** None.  
**Unresolved:** None.  
**Next:** Record director approval and scope.

## 2026-08-27 / LOG-0003 - Director approval and scope fixed

**Role:** Director / Canon owner  
**Action:** The Director explicitly approved the PDF as the school system and clarified that approval covers **all openings, all responses, all continuations and all rebids shown in the document**, not only openings and first responses.  
**Output:** Canonical source classification recorded in `SOURCE_MANIFEST.json`.  
**Verification:** Scope was clarified by a direct correction in the project conversation.  
**Database effect:** None. Approval of a source does not itself create or activate database rules.  
**Unresolved:** The source may still contain local ambiguities; these will be logged per block.  
**Next:** Render and inventory every auction block.

## 2026-08-27 / LOG-0004 - PDF rendered and inspected

**Role:** Curator / Observatory  
**Action:** Rendered both pages at 220 DPI and visually inspected the full grid. Also extracted embedded text for comparison.  
**Output:** Confirmed a dense, two-page auction chart with 34 distinct source blocks.  
**Verification:** Visual render is treated as authoritative for layout and suit symbols; machine text extraction showed column-order and symbol errors and therefore cannot be imported directly.  
**Database effect:** None.  
**Unresolved:** Exact line-level transcription and semantic normalization remain to be performed block by block.  
**Next:** Create the block inventory.

## 2026-08-27 / LOG-0005 - Actual implementation state reconciled

**Role:** Observatory / Red Team  
**Action:** Rechecked GitHub and production Neon instead of relying on prior chat statements.  
**Findings:** PR #630 is still an open draft; production Neon has no `bidding` schema, no `0105_bidding_knowledge_v0` or `0106_bidding_resolver_v0` migration, and no bidding knowledge items.  
**Output:** The ingestion plan was reset to the real state.  
**Verification:** Read-only GitHub PR inspection and read-only Neon query.  
**Database effect:** None.  
**Unresolved:** Infrastructure PR #630 must be corrected, verified and promoted before executable canon can be activated.  
**Next:** Prepare source artifacts independently of production activation.

## 2026-08-27 / LOG-0006 - Controlled ingestion branch created

**Role:** Coordinator  
**Action:** Created branch `bidding/canon-natural-system-ingestion` from current `main`.  
**Output:** Durable workspace for source manifest, inventory, transcriptions, candidate rules, tests and this log.  
**Verification:** Branch creation returned success.  
**Database effect:** None.  
**Unresolved:** None.  
**Next:** Add source manifest and complete block inventory.

## 2026-08-27 / LOG-0007 - Source manifest committed

**Role:** Curator  
**Action:** Created a machine-readable source manifest containing the exact Drive identity, SHA-256, document metadata, director approval, canonical scope and authority boundaries.  
**Output:** `SOURCE_MANIFEST.json`.  
**Verification:** Manifest is valid JSON and points to this log and the block inventory.  
**Database effect:** None.  
**Unresolved:** None at manifest level.  
**Next:** Inventory every source block.

## 2026-08-27 / LOG-0008 - All auction blocks inventoried

**Role:** Curator / Observatory  
**Action:** Mapped the rendered two-page grid into stable source-block identifiers.  
**Output:** `BLOCK_INVENTORY.json` with 34 blocks, including openings, first responses, continuations and rebids.  
**Verification:** Heading-level inventory was checked against both rendered pages; each grid cell has page, location, normalized public auction path and actor role.  
**Database effect:** None.  
**Unresolved:** Line-level transcription remains pending for 33 blocks after the openings block.  
**Next:** Transcribe the openings block exactly.

## 2026-08-27 / LOG-0009 - Opening block transcribed

**Role:** Curator  
**Action:** Produced a visual-verification transcription of `NSV1-P1-R1-C1` at 300 DPI.  
**Output:** `transcriptions/NSV1-P1-R1-C1_OPENINGS.md`.  
**Verification:** Every source line was checked against the rendered crop; machine extraction was used only as a secondary comparison.  
**Database effect:** None.  
**Unresolved:** Eight semantic-operator questions were identified and preserved rather than guessed.  
**Next:** Decompose the shared source lines into atomic candidate actions.

## 2026-08-27 / LOG-0010 - Opening candidates decomposed

**Role:** Knowledge compiler  
**Action:** Split the opening block into 19 atomic call candidates while preserving source text and unresolved predicates.  
**Output:** `candidates/NSV1-P1-R1-C1_OPENINGS.candidates.json`.  
**Verification:** Candidate count matches the distinct opening actions shown in the source: 1♣, 1♦, 1♥, 1♠, 1NT, 2♣, 2♦, 2♥, 2♠, 2NT, four three-level suit openings, 3NT and four four-level suit openings.  
**Database effect:** None; all candidates are non-activated and currently in Git only.  
**Unresolved:** 1♣/1♦ partition logic, length exactness for preempts, strong-suit and playing-trick definitions, preemptive-strength boundary, semi-preemptive meaning and stopper evaluation.  
**Next:** Design source-faithful tests for the unambiguous candidates and prepare a batch ambiguity register for the Director.

## 2026-08-27 / LOG-0011 - Semantic operator register created

**Role:** Curator / Knowledge architect  
**Action:** Separated exact source wording from the machine operators required to execute it.  
**Output:** `SEMANTIC_OPERATOR_REGISTER.md` with 33 operators classified as mechanical, source-implied, requiring other approved canon, or requiring a Director decision.  
**Verification:** Every registered operator points to terminology actually present in the PDF; no external system definition was inserted.  
**Database effect:** None.  
**Unresolved:** Operator definitions remain open by class; affected candidates remain `not_eligible`.  
**Next:** Continue exact block transcription while batching true Director decisions.

## 2026-08-27 / LOG-0012 - First responses to 1♣ transcribed

**Role:** Curator  
**Action:** Rendered page 1 at 300 DPI, cropped block `NSV1-P1-R1-C2` and transcribed all first responses to 1♣.  
**Output:** `transcriptions/NSV1-P1-R1-C2_1C_FIRST_RESPONSES.md`.  
**Verification:** Visual source block was checked line by line; grouped suit calls and the trailing source punctuation were preserved.  
**Database effect:** None.  
**Unresolved:** Point method, forcing-state glossary, balanced-shape domain, major tie-break completeness and the meaning of `блок`.  
**Next:** Decompose grouped calls into atomic candidates.

## 2026-08-27 / LOG-0013 - First-response candidates decomposed

**Role:** Knowledge compiler  
**Action:** Split `NSV1-P1-R1-C2` into 14 atomic response candidates and recorded explicit source conditions separately from unresolved priority rules.  
**Output:** `candidates/NSV1-P1-R1-C2_1C_FIRST_RESPONSES.candidates.json`.  
**Verification:** Candidate count matches all distinct calls printed in the block. No candidate was marked active.  
**Database effect:** None; database rows created: 0.  
**Unresolved:** The candidate file lists each missing operator and overlap risk.  
**Next:** Build abstract positive/negative/boundary/conflict test specifications.

## 2026-08-27 / LOG-0014 - Replacement infrastructure branch created

**Role:** Coordinator / Technical owner  
**Action:** Created `bidding/knowledge-v0-mainline` from current `main` because draft PR #630 uses a non-active migration path and cannot be treated as deployed.  
**Output:** Clean mainline-compatible infrastructure workspace.  
**Verification:** GitHub branch creation returned success.  
**Database effect:** None.  
**Unresolved:** Migration and regression tests are not yet committed to this branch.  
**Next:** Validate the hardened migration against an isolated database branch.

## 2026-08-27 / LOG-0015 - Temporary Neon validation branch created

**Role:** Observatory / Technical owner  
**Action:** Created isolated Neon branch `bidding-knowledge-v0-mainline-validation-20260827`.  
**Branch ID:** `br-muddy-resonance-b1pf1tze`.  
**Parent:** production branch `br-wispy-lab-b1rq54of`.  
**Purpose:** Syntax, permission, activation-gate, audit-log and rollback validation only.  
**Verification:** Neon branch creation returned success.  
**Database effect:** Production none; temporary branch only.  
**Unresolved:** Migration not yet applied.  
**Next:** Execute the migration as one controlled transaction.

## 2026-08-27 / LOG-0016 - Multi-command execution probe rejected safely

**Role:** Technical owner  
**Action:** Tested whether the single-statement Neon interface accepts two SQL commands.  
**Input:** Read-only probe `SELECT 1; SELECT 2;` on temporary branch `br-muddy-resonance-b1pf1tze`.  
**Result:** Interface rejected multiple commands in one prepared statement.  
**Verification:** No schema or data changes were made.  
**Database effect:** Production none; temporary branch unchanged.  
**Unresolved:** Full migration must be submitted through the transaction interface as individually parsed statements or through repository CI.  
**Next:** Parse and validate the migration transaction without bypassing the safety boundary.

## 2026-08-27 / LOG-0017 - Infrastructure migration hardened locally

**Role:** Technical owner / Red Team  
**Action:** Revised the draft migration before execution to add cross-school evidence checks, activation-overlap prevention, active-rule immutability, append-only test runs and decision traces, strict ingestion-run/event auditing, sequential event numbering, server-controlled audit timestamps and least-privilege column grants.  
**Output:** Local uncommitted draft `0105_bidding_knowledge_v0.sql`; 120 transactional SQL statements after parsing.  
**Verification:** Static parser completed with balanced strings and dollar-quoted function bodies. Runtime PostgreSQL validation is still pending.  
**Database effect:** None.  
**Unresolved:** PostgreSQL execution and regression tests.  
**Next:** Apply the exact parsed statements to the temporary Neon branch, then commit only the verified migration.

## 2026-08-27 / LOG-0018 - Migration applied to the temporary branch

**Role:** Technical owner / Observatory  
**Action:** Applied the hardened infrastructure migration to Neon branch `br-muddy-resonance-b1pf1tze` in eight transactional batches because the connector accepts one prepared command per statement.  
**Output:** Temporary schema containing 9 base tables, 3 views, 20 functions and 16 non-internal triggers; migration key `0105_bidding_knowledge_v0` registered on the temporary branch.  
**Verification:** Every transaction batch completed successfully. Structural count query matched the intended object inventory.  
**Database effect:** Production none; temporary branch only.  
**Unresolved:** Behavioral and permission smoke tests still required.  
**Next:** Run fail-closed activation, authority-lane, immutability, audit and ACL tests.

## 2026-08-27 / LOG-0019 - Pre-execution trigger defect found and corrected

**Role:** Red Team / Technical owner  
**Action:** Static review found that an overly broad local replacement would have attached an active-rule test-definition guard to `rule_test_run`, unintentionally rejecting new append-only test results.  
**Correction:** Restored the intended separation: test definitions cannot change after rule activation; test-result rows allow INSERT but reject UPDATE and DELETE.  
**Verification:** Correction was made before the affected trigger batch was applied to the temporary database.  
**Database effect:** Production none; defective trigger never entered the temporary schema.  
**Unresolved:** None for this defect.  
**Next:** Execute the full smoke test.

## 2026-08-27 / LOG-0020 - First smoke attempt rolled back on a test-only error

**Role:** Observatory / Red Team  
**Action:** Ran the complete transactional smoke fixture.  
**Result:** The final privilege assertion used `PUBLIC` as a role name in `has_function_privilege`; PostgreSQL interpreted it as a missing named role and aborted the DO block.  
**Verification:** The fixture transaction rolled back automatically; no synthetic source, knowledge, rule, activation, gap, trace or ingestion event remained from the attempt. The migration schema itself was unchanged.  
**Database effect:** Production none; temporary branch retained only migration objects.  
**Unresolved:** Test assertion syntax only, not a schema defect.  
**Next:** Remove the invalid named-role check and rerun the same behavioral assertions.

## 2026-08-27 / LOG-0021 - Corrected full smoke test passed

**Role:** Observatory / Red Team  
**Action:** Re-ran the full transactional smoke fixture after correcting the privilege assertion.  
**Verified boundaries:** activation without tests blocked; required latest PASS results enabled activation; cross-school evidence rejected; canonical rule visible only in canonical catalog; WORLD rule excluded from SCHOOL catalog and available to research catalog; active rule and test definitions immutable; overlapping activation blocked; hidden-hand payload rejected; decision traces append-only; ingestion events sequential and append-only; terminal ingestion run immutable; cross-school rule creation rejected; worker unable to activate or pre-resolve conflicts.  
**Result:** PASS.  
**Database effect:** Production none. Synthetic fixtures exist only on the disposable temporary branch and will disappear when that branch is deleted.  
**Unresolved:** Repository CI packaging and independent I2 review remain.  
**Next:** Verify effective principal ACLs and persist the exact tested migration in GitHub.

## 2026-08-27 / LOG-0022 - Effective runtime ACLs verified

**Role:** Observatory / Security review  
**Action:** Checked function ACLs and effective privileges of `bridge_school_app_principal` and `bridge_school_worker_principal`; also executed the canonical catalog under `bridge_school_reader`.  
**Results:** Reader can execute the SCHOOL catalog but not the research catalog or WORLD views; app principal can insert only the approved decision-trace columns and cannot set server audit time; worker principal can append test results and ingestion events but cannot set audit timestamps, activate rules or pre-resolve conflicts; worker can execute the research catalog.  
**Verification:** Direct PostgreSQL ACL and column-privilege queries on the temporary branch.  
**Database effect:** Production none.  
**Unresolved:** A CI database test must reproduce these assertions from a clean database.  
**Next:** Commit migration and regression test to the active `database/` migration path.

## 2026-08-27 / LOG-0023 - Wave 01 test plan and decision pack created

**Role:** Curator / Red Team  
**Action:** Converted the first two source blocks into a source-faithful test plan and a batched ambiguity decision pack.  
**Output:** `tests/TEST_PLAN_WAVE_01.md` and `DIRECTOR_DECISION_PACK_01.md`.  
**Verification:** The plan explicitly tests known overlaps such as 1NT versus a five-card major, 1NT versus 2♣ after 1♣, 2NT versus 3♣, and incomplete 1♣/1♦ partitioning. No row-order fallback is permitted.  
**Database effect:** None.  
**Unresolved:** Decision pack remains internal until the remaining 32 blocks are searched for explicit answers.  
**Next:** Continue transcription and compile only unambiguous, testable knowledge.

## 2026-08-27 / LOG-0024 - Draft ingestion review opened

**Role:** Coordinator  
**Action:** Opened draft PR #662, `SCHOOL CANON ingestion: approved natural bidding system v1`.  
**Output:** Independent review surface for the source manifest, 34-block inventory, exact transcriptions, candidates, operator register, test plan, decision pack and this log.  
**Verification:** PR reports 10 changed files, 1225 additions and zero deletions at creation.  
**Database effect:** None. No rule was created or activated in production.  
**Unresolved:** PR is intentionally draft and not merge-ready while transcription and review continue.  
**Next:** Persist the independently validated infrastructure migration and transcribe the next auction block.


## 2026-08-28 / LOG-0025 - Infrastructure PR #630 status corrected

**Role:** Observatory / Coordinator  
**Action:** Rechecked the GitHub state of PR #630 instead of carrying forward the stale draft status recorded in LOG-0005.  
**Finding:** PR #630, `Bidding knowledge v0: authority-separated runtime gates and traces`, is merged at commit `0f80f102b640d9b4f69749ccb3afb12f8293461a`.  
**Verification:** Read-only GitHub PR and commit inspection.  
**Database effect:** None. A merged code change is not evidence that its schema is present in production.  
**Correction:** References in earlier log entries to PR #630 being an open draft are historical observations and no longer describe the current GitHub state.  
**Next:** Reconcile the mainline migration and production migration registry independently.

## 2026-08-28 / LOG-0026 - Mainline bidding migration status reconciled

**Role:** Observatory / Technical owner  
**Action:** Rechecked PR #668, its head checks, and the active database migration path.  
**Finding:** PR #668, `Bidding knowledge v0: mainline schema, checksums and DB contract`, is merged at commit `7da87bb2e58a517d961624dc8d539dfbac3d84d2`. Its head commit `d651bbe578b34d1a44bea3d72c4c219d29097244` had successful Database CI, Secret gate, META Evidence Gate, and Migration Namespace Guard checks. The mainline migration key is `0200_bidding_knowledge_v0`.  
**Verification:** Read-only GitHub PR, file, commit-status and workflow inspection.  
**Database effect:** None. Production registration and objects were checked separately.  
**Correction:** Earlier entries that refer to a local `0105_bidding_knowledge_v0.sql` describe an intermediate validation draft, not the current mainline migration identity.  
**Next:** Prove `0200_bidding_knowledge_v0` from a fresh production-derived branch before any promotion decision.

## 2026-08-28 / LOG-0027 - Canon ingestion PR #662 status corrected

**Role:** Observatory / Curator  
**Action:** Rechecked PR #662.  
**Finding:** PR #662, `SCHOOL CANON ingestion: approved natural bidding system v1`, is merged at commit `24d47df1535b693afcfa073aa8db0ce88cca841c`. The approved source remains Google Drive file `1HkVff4iH2e3HT5kwblvd3mY8TUQPR6jf`, SHA-256 `dc9678da5ab19a897c3f1fbd785cc6f7b0ddd9d70d90d895743db615a2fdd3d6`.  
**Verification:** Read-only GitHub inspection and comparison with `SOURCE_MANIFEST.json`.  
**Database effect:** None. The 34-block inventory, two exact transcriptions, 33 candidate records and related plans remain Git artifacts; no rule is activated by the merge.  
**Correction:** LOG-0024 is preserved as the state at PR creation; it is no longer the current PR status.  
**Next:** Continue source work only under the fail-closed eligibility rules.

## 2026-08-28 / LOG-0028 - Production Neon state reconciled

**Role:** Observatory / Database custodian  
**Action:** Queried the protected production branch read-only for the migration registry and bidding objects.  
**Finding:** Production contains 62 registered migrations. Migrations `0020` through `0037` are already registered; `0200_bidding_knowledge_v0` is not registered. Production has no `bidding` schema, no `bidding.rule` table and no active bidding Canon runtime rules.  
**Verification:** Read-only Neon metadata and SQL queries against production project `misty-poetry-18012774`, branch `br-wispy-lab-b1rq54of`, database `neondb`.  
**Database effect:** None.  
**Correction:** The earlier project handoff statement that only migrations `0001–0019` were present is superseded by this live observation.  
**Next:** Keep production unchanged and create clean, disposable preflight evidence.

## 2026-08-28 / LOG-0029 - Existing validation branch rejected as promotion evidence

**Role:** Red Team / Observatory  
**Action:** Re-inspected the earlier validation branch `br-muddy-resonance-b1pf1tze`.  
**Finding:** The branch contains bidding objects, two rules, two active activations, one decision trace and one ingestion run, while `0200_bidding_knowledge_v0` is not registered.  
**Verification:** Read-only Neon SQL on the isolated branch.  
**Database effect:** Production none; no cleanup or mutation was performed.  
**Assessment:** This branch is useful historical evidence but is not a clean preflight baseline and must not support production promotion.  
**Next:** Use a fresh branch from current production and require zero residual fixture rows.

## 2026-08-28 / LOG-0030 - Automated migration helper failed closed

**Role:** Technical owner / Observatory  
**Action:** Submitted the exact mainline composite migration to the Neon migration-preparation helper. The computed composite SHA-256 was `024b348c0bf01dc7666caef5bcb7e0613463d4f223ee0bc3e9171baa1af091fd`.  
**Result:** The helper returned `INVALID_ARGUMENT` and produced no migration identifier or validated migration branch.  
**Verification:** The failure response was checked; production still has no `0200_bidding_knowledge_v0` registry row or bidding schema.  
**Database effect:** None.  
**Assessment:** The failed helper flow is closed and will not be represented as successful evidence.  
**Next:** Execute the repository's exact `database/scripts/migrate.sh` on a fresh disposable production-derived branch; require registry checksum match, smoke-test rollback, zero residual rows and an idempotent rerun before I2 review.
