# PR1546_MINIMAL_V1 — publication security evidence

## Identity and hard boundaries

- Package identity: `PR1546_MINIMAL_V1`.
- Functional base: merged `main` exact head `2189f1014bedaee69c3304b7e90670a3a1785d46`.
- Main anchor used for final reconciliation: `2189f1014bedaee69c3304b7e90670a3a1785d46`.
- Frozen pre-minimization PR #1546 head: `b8a48795fff264220f7df915c119e2936a35256e`.
- Last repository-published minimal predecessor before this reconciliation: `6f12f61cfe38eaa40797c571266a043e136ec898`.
- Merged dependency boundary: migrations 0336 and 0337 are authoritative on `main`; PR #1546 does not duplicate or rewrite them. PR #1600 is not inspected, reviewed, reconstructed, commented on, or modified here.
- Merged PR #1608 supplied migration 0337 and repair-admission/callback semantics now inherited from `main` unchanged by #1546.
- Merged PR #1625 made the inherited role-dispatch lower-chain CI roundtrip upper-chain-aware: when 0338/0339/0340 are present it cleanly rolls them back before 0337→0322 and reapplies them afterward; #1546 inherits that CI fix unchanged.
- PR #1546 owns only 0338 bounded publication, 0339 native CLI receipts, 0340 owner-bound permit issuance, their focused integrations/tests, and strictly necessary evidence.
- Publication remains **DISABLED**. Permit issuance remains **DISCONNECTED FROM RUNTIME/ACTIONS**. No merge, production migration, activation, production permit, canary, server/credential/ruleset/Canon/Drive change is part of this package.

Old `PUBLICATION_V5` aggregate `c54cec548c5ae41205a714bd66be2dc98b10005166ad78916a6881ba8f803c03` is retired and is not reused. The frozen chunk stream stops after chunk 6/69; chunk 7 is not continued.

## Minimal keep classification

Every changed functional path below has an explicit security/runtime reason. “Needed to match main” is not used as a keep reason.

| Path | Classification | Contract that fails without it | Required by |
|---|---|---|---|
| `.github/workflows/autopilot-codex-event-callback.yml` | REQUIRED_FOR_1546_CORE | Only existing callback receiver may get the guarded publisher job; without it no immutable `github.workflow_sha` receiver or job-scoped `contents: write` path exists. | `tests/test_oracle_autopilot_github_codex_callback_workflow.py`; runtime module `github_codex_publication`. |
| `.github/workflows/autopilot-publication-security-ci.yml` | REQUIRED_FOR_1546_CORE | Provides #1546-only exact-head Python/PostgreSQL18/migration-lifecycle/namespace checks without taking ownership of the merged role-dispatch SQL CI. | Hosted PR CI; parsed by callback-workflow test. |
| `database/migrations/0338_autopilot_bounded_publication_permit.sql` | REQUIRED_FOR_1546_CORE | Creates the bounded publication permit ledger and locked authorization RPC consumed by the callback publisher; carries owner-approval binding while keeping issuer grants absent. | SQL test 338; `github_codex_publication.authorize`. |
| `database/migrations/0339_autopilot_native_cli_receipts.sql` | REQUIRED_FOR_1546_CORE | Adds native CLI reservation/receipt fencing and unknown-submission retention required by the requested native receipt contract. | SQL test 339; `codex_cli_queue.py` RPCs and delivery state machine. |
| `database/migrations/0340_autopilot_publication_permit_issuer.sql` | REQUIRED_FOR_1546_CORE | Adds the owner-only, exact-dispatch permit issuer with idempotent replay and reuse conflict fencing; no runtime/callback grant is made. | SQL test 340 and concurrent 340a test; offline permit verifier `issue()`. |
| `database/rollbacks/0338_autopilot_bounded_publication_permit.sql` | REQUIRED_FOR_1546_CORE | Defines fail-closed rollback for publication ledger and refuses destructive rollback when permit evidence exists. | Focused PostgreSQL18 lifecycle CI. |
| `database/rollbacks/0339_autopilot_native_cli_receipts.sql` | REQUIRED_FOR_1546_CORE | Restores pre-native receipt functions/contract, takes `ACCESS EXCLUSIVE` on the receipt ledger before the emptiness check, and refuses rollback when native evidence exists. | Focused PostgreSQL18 lifecycle CI and 340a rollback/write race proof. |
| `database/rollbacks/0340_autopilot_publication_permit_issuer.sql` | REQUIRED_FOR_1546_CORE | Removes only owner issuer on clean ledgers and refuses rollback if retained permit evidence exists. | Focused PostgreSQL18 lifecycle CI and 340a populated-ledger check. |
| `database/tests/338_autopilot_bounded_publication_permit.sql` | REQUIRED_FOR_1546_CORE | Proves exact assignment/provenance/ACL/revocation/deadline checks for callback authorization. | PostgreSQL18 CI after migration 0338. |
| `database/tests/339_autopilot_native_cli_receipts.sql` | REQUIRED_FOR_1546_CORE | Proves disabled-by-default native path, reservation ownership, replay/conflict, unknown outcome retention and ACL isolation. | PostgreSQL18 CI after migration 0339. |
| `database/tests/340_autopilot_publication_permit_issuer.sql` | REQUIRED_FOR_1546_CORE | Proves exact owner-bound evidence fields, wrong task/dispatch/head rejection, replay/reuse/expiry/revocation and no runtime grants. | PostgreSQL18 CI after migration 0340. |
| `database/tests/340a_autopilot_publication_permit_concurrency.sh` | REQUIRED_FOR_1546_CORE | Provides two-session permit conflict proof, populated-ledger rollback retention, and a deterministic native-receipt rollback/write serialization race proof that a single SQL transaction cannot prove. | Focused PostgreSQL18 cloned-database CI job. |
| `oracle_autopilot/codex_cli_bridge.py` | REQUIRED_FOR_1546_CORE | Maintains crash-safe local provider evidence and fail-closed `PUBLICATION_OUTCOME_UNKNOWN`/submission ambiguity handling for native CLI. | CLI bridge and delivery unit tests. |
| `oracle_autopilot/codex_cli_delivery.py` | REQUIRED_FOR_1546_CORE | Orchestrates reserve/begin/ack/finish without treating an unconfirmed mutation as success. | `test_oracle_autopilot_codex_cli_delivery.py` fault injection. |
| `oracle_autopilot/codex_cli_queue.py` | REQUIRED_FOR_1546_CORE | Binds native client state to owner-only SQL receipt RPCs and uses parameters rather than executable receipt values. | `test_oracle_autopilot_codex_cli_queue.py` and migration 0339. |
| `oracle_autopilot/github_codex_publication.py` | REQUIRED_FOR_1546_CORE | Implements bounded allowlist/sensitive-path denial, immutable event binding, expected-head atomic `createCommitOnBranch`, exact blob/readback verification, replay and unknown-outcome semantics. | Publish job runtime path and publication unit tests. |
| `oracle_autopilot/github_codex_publication_permit.py` | REQUIRED_FOR_1546_CORE | Offline verifier re-fetches command/publication/direct-owner approval, rejects copied/edited envelopes, hashes provenance, and calls only the owner-only SQL issuer in explicit `issue` mode; no Actions/runtime wiring. | Permit unit tests plus migration 0340. |
| `tests/test_oracle_autopilot_codex_cli_bridge.py` | REQUIRED_FOR_1546_CORE | Exercises native provider evidence parsing, crash/retry boundaries and ambiguous submission behavior. | Focused Python CI. |
| `tests/test_oracle_autopilot_codex_cli_delivery.py` | REQUIRED_FOR_1546_CORE | Fault-injects native delivery and proves no false terminal success on ambiguous provider/receipt failures. | Focused Python CI. |
| `tests/test_oracle_autopilot_codex_cli_queue.py` | REQUIRED_FOR_1546_CORE | Proves queue values are bound SQL parameters and validates receipt authority calls. | Focused Python CI. |
| `tests/test_oracle_autopilot_github_codex_callback_workflow.py` | REQUIRED_FOR_1546_CORE | Pins workflow permission/flag/immutable-checkout expectations and proves merged role-dispatch SQL CI is inherited rather than duplicated by #1546. | Focused Python CI. |
| `tests/test_oracle_autopilot_github_codex_publication.py` | REQUIRED_FOR_1546_CORE | Covers parser, bounded paths, sensitive denial, base/live head, CAS readback, replay and fail-closed publication outcomes. | Focused Python CI; runtime publisher contract. |
| `tests/test_oracle_autopilot_github_codex_publication_permit.py` | REQUIRED_FOR_1546_CORE | Covers direct-owner approval, copied-envelope forgery, edited/refetched records, wrong bindings, ID reuse and issuer disconnection. | Focused Python CI; offline permit verifier contract. |

`docs/evidence/pr1546_minimal_publication_security_2026-09-16.md` is also `REQUIRED_FOR_1546_CORE` as the review/package evidence file. Its content hash is recorded externally with the final aggregate package hash to avoid self-reference.

## Required dependencies that are intentionally not owned by #1546

- `oracle_autopilot/github_codex_callback.py` — `REQUIRED_DEPENDENCY_FOR_1546`; existing immutable command/terminal parsing and binding API consumed by publication code. No #1546 diff is needed.
- `.github/workflows/autopilot-role-dispatch-sql-ci.yml` — `REQUIRED_DEPENDENCY_FOR_1546`; merged-main CI covering 0336/0337 repair/callback lifecycle and, after #1625, safely unwinding/reapplying an installed 0338/0339/0340 chain around its lower-chain roundtrip. #1546 deliberately does not modify it.
- `database/migrations/0337_autopilot_role_repair_admission.sql`, its rollback, `database/tests/337_autopilot_role_repair_admission.sql`, and `database/tests/337a_autopilot_role_repair_callback.sql` — `REQUIRED_DEPENDENCY_FOR_1546`; merged into `main` and inherited unchanged.
- `database/migrations/0336_autopilot_codex_terminal_implicit_delivery.sql` and its merged-main lifecycle support — `REQUIRED_DEPENDENCY_FOR_1546`; present on `main` as the predecessor to 0337. #1546 does not own or modify it.

## DROP set and minimization rationale

- `.github/workflows/autopilot-role-dispatch-sql-ci.yml` as a **#1546 update** — `UNRELATED_DROP`: it is authoritative on merged `main`, including the #1625 upper-chain-aware roundtrip fix. #1546 inherits it unchanged rather than duplicating dependency scope.
- `.github/workflows/bridge-video-3.1-free.yml` — `UNRELATED_DROP`: frozen V5 chunk 3 was reconciliation-only Video workflow state; bounded publication does not execute or modify Video pipelines.
- `bridge_runtime_hardening_r26.py` — `UNRELATED_DROP`: frozen V5 chunk 4 was current-main reconciliation. No publication/permit/native receipt import or test requires it.
- `bridge_vision/**` including `bridge_vision/anchor_registration.py`, `bridge_vision/bridgit_event_frame_selector.py`, and the never-started next cursor `bridge_vision/bridgit_gambler_rank_layout.py` — `UNRELATED_DROP`: Vision/recognizer layout code has no path in the publication receiver, permit issuer, or native receipt contract.
- Any Video workflow, runtime/recognizer, vision, media, or other path that appeared solely because of reconciliation with current main — `UNRELATED_DROP`; current base versions remain inherited unchanged.
- Old #1546 `database/migrations/0336_autopilot_bounded_publication_permit.sql`, rollback, and test 336 — `UNRELATED_DROP` from the target package because merged `main` owns 0336 for the terminal-delivery prerequisite; bounded publication remains collision-neutralized as 0338.
- Old #1546 `database/migrations/0337_autopilot_native_cli_receipts.sql`, rollback, and test 337 — `UNRELATED_DROP` because merged `main` owns 0337 for repair admission; native receipts remain collision-neutralized as 0339.
- `docs/evidence/slavik_bounded_publication_2026-09-15.md` and `docs/evidence/slavik_native_cli_integration_2026-09-15.md` — `UNRELATED_DROP`: stale evidence for obsolete numbering/pre-minimization package; replaced by this exact package evidence.

No unenumerated path from the retired 69-path manifest is silently carried forward. If an old-stream path not named above is ever proposed again it is `UNCERTAIN_NEEDS_PROOF` until a direct 1546 security/runtime/test dependency is demonstrated. It is not part of `PR1546_MINIMAL_V1`.

## Exact operations and content identities (functional core)

Canonical core hash algorithm: SHA-256 over `PR1546_MINIMAL_V1\nbase=<main-head>\n` plus lexicographically sorted tab-separated `path, operation, base_blob_sha, content_sha256, mode` records.

- Functional core aggregate SHA-256: `15a3687eae41b8a31516aae848f89f2ffd6bf66706f9d13de73f395fbc2b87e8`.
- DELETE operations: **none** relative to merged `main`.

| Operation | Base blob SHA | Content SHA-256 | Mode | Path |
|---|---|---|---|---|
| UPDATE | `2c49fcf95bc3ded580c842ec0dbe61f08271f6f5` | `b8107d35df94adae2fe83c788fd3ba71284bd25be7f13650abef183524dbffbf` | `100644` | `.github/workflows/autopilot-codex-event-callback.yml` |
| CREATE | `-` | `6306e146bb070fc6fd322daab767c25379a90e1f45d0095f3a5208de7696afb5` | `100644` | `.github/workflows/autopilot-publication-security-ci.yml` |
| CREATE | `-` | `12ea33b39d50ee1afb0436959230c070a89ab968f948bf4c738c599290bb289e` | `100644` | `database/migrations/0338_autopilot_bounded_publication_permit.sql` |
| CREATE | `-` | `5f37acb6ae97694f93851c3288b0c580bbeecde9246c5bb2665f70327436a3a5` | `100644` | `database/migrations/0339_autopilot_native_cli_receipts.sql` |
| CREATE | `-` | `137dc726b2edc156c51e04ecfeea4a1031ede01498d1c7dd28e04509d37b4d38` | `100644` | `database/migrations/0340_autopilot_publication_permit_issuer.sql` |
| CREATE | `-` | `65040ae7eb04f90aff558f6745564f7377e5fc1aa5cbe49fdca38e9aa92b01bb` | `100644` | `database/rollbacks/0338_autopilot_bounded_publication_permit.sql` |
| CREATE | `-` | `849176f6595f857f9fed2f7a29a65ba1f85729c7ab8e9fce517bfda47098351e` | `100644` | `database/rollbacks/0339_autopilot_native_cli_receipts.sql` |
| CREATE | `-` | `aabb543cdcd257b83e244667077922582d950249ff4c2dd08dd605eae7f12e9d` | `100644` | `database/rollbacks/0340_autopilot_publication_permit_issuer.sql` |
| CREATE | `-` | `7d5c63a5ec3d368b099387a158b33a64b217e70291ca6556728bad4fa3f1f81a` | `100644` | `database/tests/338_autopilot_bounded_publication_permit.sql` |
| CREATE | `-` | `b5981aa2a5c4af870618b34bedfdcfdcf77403de2737924173b86bd2791c940` | `100644` | `database/tests/339_autopilot_native_cli_receipts.sql` |
| CREATE | `-` | `bf3fedee059b4487c009467d99c10cfaf8f3620913ed2e7fa5d83a0fca54642e` | `100644` | `database/tests/340_autopilot_publication_permit_issuer.sql` |
| CREATE | `-` | `d3ef676359bd23c9dce1f73c2594f2f41c4a567b40b8fd591d1f44128001c525` | `100755` | `database/tests/340a_autopilot_publication_permit_concurrency.sh` |
| CREATE | `-` | `4ca1c67eb2e80742ba02cf8cb69add6a9122f874ee7e5ff783990b140d6710de` | `100644` | `oracle_autopilot/codex_cli_bridge.py` |
| CREATE | `-` | `72ce096ce6792ffbc3938a94cb90b9d04d7e4d1f290f43181fde79229a146010` | `100644` | `oracle_autopilot/codex_cli_delivery.py` |
| CREATE | `-` | `39c6fe1acbca6bb2075066014e7df15ea5850` | `100644` | `oracle_autopilot/codex_cli_queue.py` |
| CREATE | `-` | `5a3db72ee352854eab33da712f3e9ef8aef6932275f4a2939a87122003862e47` | `100644` | `oracle_autopilot/github_codex_publication.py` |
| CREATE | `-` | `1e831b5ed4236ff71ce3f4d4d8a78778c6b3881fefdc2b35028dcd121815b984` | `100644` | `oracle_autopilot/github_codex_publication_permit.py` |
| CREATE | `-` | `bb1dc5120896afaa38c2bdf015c7ea3a2d1537bbdc9a7a07b41b6bb9de044ee5` | `100644` | `tests/test_oracle_autopilot_codex_cli_bridge.py` |
| CREATE | `-` | `ac1ca1aa466906b2126dd9e523df3ad343f625efa6f9d6977b249aadb71f16a4` | `100644` | `tests/test_oracle_autopilot_codex_cli_delivery.py` |
| CREATE | `-` | `36cd1bdc53792db0a65b6ab773bc53364972b672de2075066014e7df15ea5850` | `100644` | `tests/test_oracle_autopilot_codex_cli_queue.py` |
| UPDATE | `ee49d635e7151a466e35ee6e7506a3fe0b267e31` | `0aaeb9fe2b883c8f88acda4c8e8c0765991cf453e608c6e710d072b893c7342d` | `100644` | `tests/test_oracle_autopilot_github_codex_callback_workflow.py` |
| CREATE | `-` | `4ec05baba723dcade22a4c71ba1479172498738fe851010be54be947b6284037` | `100644` | `tests/test_oracle_autopilot_github_codex_publication.py` |
| CREATE | `-` | `6e1513cf22d01444de320f830b68da51896390e4fa36f535f7e09aa66b6be8d8` | `100644` | `tests/test_oracle_autopilot_github_codex_publication_permit.py` |

## Dependency and application map

`main 0336` → `main 0337` → `#1546 0338 bounded publication` → `#1546 0339 native receipts` → `#1546 0340 owner permit issuer`.

- Repository integration base is merged `main` exact head `2189f1014bedaee69c3304b7e90670a3a1785d46`; #1546 must not duplicate 0336/0337 or their callback/repair semantics.
- 0338 installs permit ledger + callback authorization RPC; callback can consume a permit but cannot issue one.
- 0339 installs native receipt/reservation fencing, disabled by default and without runtime grants.
- 0340 installs owner-only issuance. The offline verifier is not wired into a workflow/service; explicit owner-gated execution is required later.
- Publisher job remains gated by `AUTOPILOT_BOUNDED_PUBLICATION_ENABLED == true`; this package does not set that variable.

Intended repository commit message: `fix: reconcile minimal publication security package`.

## Rollback plan

Clean rollback order is `0340 → 0339 → 0338`. 0340 and 0338 refuse destructive rollback when permit evidence exists; 0339 takes an `ACCESS EXCLUSIVE` ledger lock before checking emptiness, so a concurrent receipt writer cannot cross the check/drop boundary, and refuses rollback when native receipt evidence exists. The focused PostgreSQL18 job verifies clean rollback, preservation of merged-main 0337, reapply, and retest. Populated-ledger concurrency coverage verifies rollback failure retains evidence. No production rollback/apply is authorized by this package.

## Verification performed before repository publication

- Focused Python publication/callback/permit/native-receipt suite: **134 passed**.
- Both touched workflow YAML files parse successfully.
- Python compile and `git diff --check`: PASS.
- `database/tests/340a_autopilot_publication_permit_concurrency.sh`: shell syntax check PASS.
- Exact-head review of `b02d130f0bc448cceb4f3e03073248bc24f16d31` found P2: the publisher could rewrite existing `database/migrations/0000–0099` files. The remediation removes `database/migrations/**` from the publication allowlist entirely and adds regression coverage for historical migrations; new migration creation was already impossible because bounded publication only modifies existing files.
- Exact-head review of `dfdbee10e37881877860624c747b1f36227d4c58` found P2: 0339 checked receipt-ledger emptiness without first excluding concurrent writers, allowing an insert to commit between the check and `DROP TABLE`. The remediation takes `ACCESS EXCLUSIVE` on `autopilot.native_cli_receipt` before the check. `340a` now deterministically stalls rollback after the check path with a backup-table blocker and proves a concurrent receipt insert cannot cross the rollback lock (`native receipt rollback/write serialization: PASS`).
- Independent review of predecessor `41037d512ebeb1d15df8603341d7533594eb3933` found a P2 receipt-window gap: a CAS could start with only 45 seconds left and cross the callback/permit deadline before exact readback and terminal retention. The remediation reserves 180 seconds before any mutation, requires a second pre-CAS authorization to remain `SENT`, downgrades near/expired permit authority to `RECOVERY_ONLY`, forbids a CAS in recovery-only state, and permits only read-only exact-commit recovery while the canonical callback task still has at least 120 seconds to retain the terminal receipt. The owner-only issuer and offline verifier now require a 180–900 second permit TTL.
- The same review cycle found a P2 nondeterministic `340a` rollback/write race proof. Fixed sleeps were replaced by `pg_locks`/`pg_stat_activity` synchronization barriers that prove the blocker lock and the rollback session's granted `ACCESS EXCLUSIVE` receipt-table lock before launching the competing writer.
- Dependency PR #1625 passed its exact PostgreSQL18 role-dispatch roundtrip CI and Current-Main Authoritative CI before merge as `26615eb9689f1e4bb05d1b22d7e6a15214588803`; no production state was touched.
- Ephemeral local PostgreSQL 18 validation on the current package tree: migrations through 0340 applied; SQL tests 338/339/340 passed; deterministic `pg_locks`-synchronized 340a conflict fencing, rollback/write serialization and retained-evidence rollback refusal passed; clean rollback 0340→0339→0338 preserved 0337; reapply and retest passed.
- The local PostgreSQL run used an isolated disposable Docker database only. No production database or server state was changed. Hosted exact-head CI remains required after repository publication.

## Exact-head CI checklist after repository publication

- [ ] `Autopilot publication security CI / focused-python` on exact #1546 head.
- [ ] `Autopilot publication security CI / postgresql18-publication-chain` on exact #1546 head: 0338/0339/0340 apply, focused SQL tests, concurrent issuer fencing, populated-evidence rollback refusal, clean rollback 0340→0339→0338, 0337 preservation, reapply.
- [ ] Existing `Autopilot role dispatch SQL CI` inherited from merged `main`.
- [ ] `Bridge School Database CI` PostgreSQL18 full migration/invariant suite.
- [ ] `Current-Main Authoritative CI` exact-head compatibility.
- [ ] Migration namespace guard proves one 0338/0339/0340 and no obsolete #1546 0336/0337 names.
- [ ] Independent I2+ security review on the exact head, after all CI fixes.

## Independent security-review checklist

- Immutable receiver: publisher executes `github.workflow_sha`, not submitted PR code.
- Only guarded publish job has `contents: write`; ACK/terminal remain read-only.
- Bounded existing-file allowlist plus sensitive-path denial is enforced before mutation; `database/migrations/**` is never publication-writable, preventing mutation-history rewrites.
- Publication is expected-head atomic and verifies parent/files/blob/live-head readback.
- Direct owner approval is distinct from command and publication comments; app-mediated copied approval is rejected; command/publication/approval records are re-fetched before issuance.
- Issuer binds dispatch/epoch/role/fingerprint/target/head/comment IDs/payload/provenance hash under database locks; replay is exact-idempotent, conflicting reuse fails closed.
- Concurrent conflicting permit issue loses under row lock; exact replay survives.
- `PUBLICATION_OUTCOME_UNKNOWN` and receipt-pending states do not claim false success.
- Rollback never silently destroys populated permit/native evidence.
- Permit issuer has no runtime/callback grant and no GitHub Actions invocation.
- No Video, Vision, recognizer, runtime-hardening, media, planner, server, credential, ruleset, Canon or Drive change is in the target diff.

## Remaining blockers

1. Exact-head hosted CI listed above must pass on the repository-published package.
2. Independent I2+ exact-head security review must be obtained after the final code/CI head.
3. Merge, production migration apply, publication activation, production permit issuance and canary remain separate owner gates.

Until those gates are satisfied, this package is repository/CI/review preparation only and publication stays disabled.
