# PR1546_MINIMAL_V1 — publication security evidence

## Identity and hard boundaries

- Package identity: `PR1546_MINIMAL_V1`.
- Functional base: PR #1608 exact head `48619755728d7eb02538a54cbfebaf484b0d2d0b`.
- Main anchor used for minimization: `eef7d5f216db0ff620959f87736b2315aded6a6b`.
- Frozen pre-minimization PR #1546 head: `b8a48795fff264220f7df915c119e2936a35256e`.
- External migration boundary: `0336 = EXTERNAL_0336_DEPENDENCY_PENDING`. PR #1600 is not inspected, reviewed, reconstructed, or modified here.
- PR #1608 owns migration 0337 and repair-admission/callback semantics. Those files are inherited unchanged.
- PR #1546 owns only 0338 bounded publication, 0339 native CLI receipts, 0340 owner-bound permit issuance, their focused integrations/tests, and strictly necessary evidence.
- Publication remains **DISABLED**. Permit issuance remains **DISCONNECTED FROM RUNTIME/ACTIONS**. No merge, production migration, activation, production permit, canary, server/credential/ruleset/Canon/Drive change is part of this package.

Old `PUBLICATION_V5` aggregate `c54cec548c5ae41205a714bd66be2dc98b10005166ad78916a6881ba8f803c03` is retired and is not reused. The frozen chunk stream stops after chunk 6/69; chunk 7 is not continued.

## Minimal keep classification

Every changed functional path below has an explicit security/runtime reason. “Needed to match main” is not used as a keep reason.

| Path | Classification | Contract that fails without it | Required by |
|---|---|---|---|
| `.github/workflows/autopilot-codex-event-callback.yml` | REQUIRED_FOR_1546_CORE | Only existing callback receiver may get the guarded publisher job; without it no immutable `github.workflow_sha` receiver or job-scoped `contents: write` path exists. | `tests/test_oracle_autopilot_github_codex_callback_workflow.py`; runtime module `github_codex_publication`. |
| `.github/workflows/autopilot-publication-security-ci.yml` | REQUIRED_FOR_1546_CORE | Provides #1546-only exact-head Python/PostgreSQL18/migration-lifecycle/namespace checks without taking ownership of #1608 SQL CI. | Hosted PR CI; parsed by callback-workflow test. |
| `database/migrations/0338_autopilot_bounded_publication_permit.sql` | REQUIRED_FOR_1546_CORE | Creates the bounded publication permit ledger and locked authorization RPC consumed by the callback publisher; carries owner-approval binding while keeping issuer grants absent. | SQL test 338; `github_codex_publication.authorize`. |
| `database/migrations/0339_autopilot_native_cli_receipts.sql` | REQUIRED_FOR_1546_CORE | Adds native CLI reservation/receipt fencing and unknown-submission retention required by the requested native receipt contract. | SQL test 339; `codex_cli_queue.py` RPCs and delivery state machine. |
| `database/migrations/0340_autopilot_publication_permit_issuer.sql` | REQUIRED_FOR_1546_CORE | Adds the owner-only, exact-dispatch permit issuer with idempotent replay and reuse conflict fencing; no runtime/callback grant is made. | SQL test 340 and concurrent 340a test; offline permit verifier `issue()`. |
| `database/rollbacks/0338_autopilot_bounded_publication_permit.sql` | REQUIRED_FOR_1546_CORE | Defines fail-closed rollback for publication ledger and refuses destructive rollback when permit evidence exists. | Focused PostgreSQL18 lifecycle CI. |
| `database/rollbacks/0339_autopilot_native_cli_receipts.sql` | REQUIRED_FOR_1546_CORE | Restores pre-native receipt functions/contract and refuses rollback when native evidence exists. | Focused PostgreSQL18 lifecycle CI. |
| `database/rollbacks/0340_autopilot_publication_permit_issuer.sql` | REQUIRED_FOR_1546_CORE | Removes only owner issuer on clean ledgers and refuses rollback if retained permit evidence exists. | Focused PostgreSQL18 lifecycle CI and 340a populated-ledger check. |
| `database/tests/338_autopilot_bounded_publication_permit.sql` | REQUIRED_FOR_1546_CORE | Proves exact assignment/provenance/ACL/revocation/deadline checks for callback authorization. | PostgreSQL18 CI after migration 0338. |
| `database/tests/339_autopilot_native_cli_receipts.sql` | REQUIRED_FOR_1546_CORE | Proves disabled-by-default native path, reservation ownership, replay/conflict, unknown outcome retention and ACL isolation. | PostgreSQL18 CI after migration 0339. |
| `database/tests/340_autopilot_publication_permit_issuer.sql` | REQUIRED_FOR_1546_CORE | Proves exact owner-bound evidence fields, wrong task/dispatch/head rejection, replay/reuse/expiry/revocation and no runtime grants. | PostgreSQL18 CI after migration 0340. |
| `database/tests/340a_autopilot_publication_permit_concurrency.sh` | REQUIRED_FOR_1546_CORE | Provides a two-session conflict proof and populated-ledger rollback evidence retention that a single SQL transaction cannot prove. | Focused PostgreSQL18 cloned-database CI job. |
| `oracle_autopilot/codex_cli_bridge.py` | REQUIRED_FOR_1546_CORE | Maintains crash-safe local provider evidence and fail-closed `PUBLICATION_OUTCOME_UNKNOWN`/submission ambiguity handling for native CLI. | CLI bridge and delivery unit tests. |
| `oracle_autopilot/codex_cli_delivery.py` | REQUIRED_FOR_1546_CORE | Orchestrates reserve/begin/ack/finish without treating an unconfirmed mutation as success. | `test_oracle_autopilot_codex_cli_delivery.py` fault injection. |
| `oracle_autopilot/codex_cli_queue.py` | REQUIRED_FOR_1546_CORE | Binds native client state to owner-only SQL receipt RPCs and uses parameters rather than executable receipt values. | `test_oracle_autopilot_codex_cli_queue.py` and migration 0339. |
| `oracle_autopilot/github_codex_publication.py` | REQUIRED_FOR_1546_CORE | Implements bounded allowlist/sensitive-path denial, immutable event binding, expected-head atomic `createCommitOnBranch`, exact blob/readback verification, replay and unknown-outcome semantics. | Publish job runtime path and publication unit tests. |
| `oracle_autopilot/github_codex_publication_permit.py` | REQUIRED_FOR_1546_CORE | Offline verifier re-fetches command/publication/direct-owner approval, rejects copied/edited envelopes, hashes provenance, and calls only the owner-only SQL issuer in explicit `issue` mode; no Actions/runtime wiring. | Permit unit tests plus migration 0340. |
| `tests/test_oracle_autopilot_codex_cli_bridge.py` | REQUIRED_FOR_1546_CORE | Exercises native provider evidence parsing, crash/retry boundaries and ambiguous submission behavior. | Focused Python CI. |
| `tests/test_oracle_autopilot_codex_cli_delivery.py` | REQUIRED_FOR_1546_CORE | Fault-injects native delivery and proves no false terminal success on ambiguous provider/receipt failures. | Focused Python CI. |
| `tests/test_oracle_autopilot_codex_cli_queue.py` | REQUIRED_FOR_1546_CORE | Proves queue values are bound SQL parameters and validates receipt authority calls. | Focused Python CI. |
| `tests/test_oracle_autopilot_github_codex_callback_workflow.py` | REQUIRED_FOR_1546_CORE | Pins workflow permission/flag/immutable-checkout expectations and proves #1608 SQL CI is inherited rather than duplicated by #1546. | Focused Python CI. |
| `tests/test_oracle_autopilot_github_codex_publication.py` | REQUIRED_FOR_1546_CORE | Covers parser, bounded paths, sensitive denial, base/live head, CAS readback, replay and fail-closed publication outcomes. | Focused Python CI; runtime publisher contract. |
| `tests/test_oracle_autopilot_github_codex_publication_permit.py` | REQUIRED_FOR_1546_CORE | Covers direct-owner approval, copied-envelope forgery, edited/refetched records, wrong bindings, ID reuse and issuer disconnection. | Focused Python CI; offline permit verifier contract. |

`docs/evidence/pr1546_minimal_publication_security_2026-09-16.md` is also `REQUIRED_FOR_1546_CORE` as the review/package evidence file. Its content hash is recorded externally with the final aggregate package hash to avoid self-reference.

## Required dependencies that are intentionally not owned by #1546

- `oracle_autopilot/github_codex_callback.py` — `REQUIRED_DEPENDENCY_FOR_1546`; existing immutable command/terminal parsing and binding API consumed by publication code. No #1546 diff is needed.
- `.github/workflows/autopilot-role-dispatch-sql-ci.yml` — `REQUIRED_DEPENDENCY_FOR_1546`; #1608-owned CI covering 0337 repair/callback lifecycle. #1546 deliberately does not modify it.
- `database/migrations/0337_autopilot_role_repair_admission.sql`, its rollback, `database/tests/337_autopilot_role_repair_admission.sql`, and `database/tests/337a_autopilot_role_repair_callback.sql` — `REQUIRED_DEPENDENCY_FOR_1546`; owned by #1608 and inherited unchanged.
- `0336 = EXTERNAL_0336_DEPENDENCY_PENDING` — required external predecessor. No implementation detail is assumed in this package.

## DROP set and minimization rationale

- `.github/workflows/autopilot-role-dispatch-sql-ci.yml` as a **#1546 update** — `UNRELATED_DROP`: it entered the reconciled stream because #1608 changed the file. #1546 now inherits it unchanged; duplicating it would steal #1608 scope.
- `.github/workflows/bridge-video-3.1-free.yml` — `UNRELATED_DROP`: frozen V5 chunk 3 was reconciliation-only Video workflow state; bounded publication does not execute or modify Video pipelines.
- `bridge_runtime_hardening_r26.py` — `UNRELATED_DROP`: frozen V5 chunk 4 was current-main reconciliation. No publication/permit/native receipt import or test requires it.
- `bridge_vision/**` including `bridge_vision/anchor_registration.py`, `bridge_vision/bridgit_event_frame_selector.py`, and the never-started next cursor `bridge_vision/bridgit_gambler_rank_layout.py` — `UNRELATED_DROP`: Vision/recognizer layout code has no path in the publication receiver, permit issuer, or native receipt contract.
- Any Video workflow, runtime/recognizer, vision, media, or other path that appeared solely because of reconciliation with current main — `UNRELATED_DROP`; current base versions remain inherited unchanged.
- Old #1546 `database/migrations/0336_autopilot_bounded_publication_permit.sql`, rollback, and test 336 — `UNRELATED_DROP` from the target package because 0336 is externally owned; the bounded publication migration is collision-neutralized as 0338.
- Old #1546 `database/migrations/0337_autopilot_native_cli_receipts.sql`, rollback, and test 337 — `UNRELATED_DROP` because #1608 owns 0337; native receipts are collision-neutralized as 0339.
- `docs/evidence/slavik_bounded_publication_2026-09-15.md` and `docs/evidence/slavik_native_cli_integration_2026-09-15.md` — `UNRELATED_DROP`: stale evidence for obsolete numbering/pre-minimization package; replaced by this exact package evidence.

No unenumerated path from the retired 69-path manifest is silently carried forward. If an old-stream path not named above is ever proposed again it is `UNCERTAIN_NEEDS_PROOF` until a direct 1546 security/runtime/test dependency is demonstrated. It is not part of `PR1546_MINIMAL_V1`.

## Exact operations and content identities (functional core)

Canonical core hash algorithm: SHA-256 over `PR1546_MINIMAL_V1\nbase=<1608-head>\n` plus lexicographically sorted tab-separated `path, operation, base_blob_sha, content_sha256, mode` records.

- Functional core aggregate SHA-256: `a060633d4c70ff8a3240ebdc34976e520562bb3eef45ced182b87c72ae60c94c`.
- DELETE operations: **none** relative to #1608.

| Operation | Base blob SHA | Content SHA-256 | Mode | Path |
|---|---|---|---|---|
| UPDATE | `2c49fcf95bc3ded580c842ec0dbe61f08271f6f5` | `b8107d35df94adae2fe83c788fd3ba71284bd25be7f13650abef183524dbffbf` | `100644` | `.github/workflows/autopilot-codex-event-callback.yml` |
| CREATE | `-` | `e3a7602069c46588b63dc0b39b07c68f47f1a73d7e609acc90bfc206730af88a` | `100644` | `.github/workflows/autopilot-publication-security-ci.yml` |
| CREATE | `-` | `510f74783d914498dba1e6d7b0195c5ed7bedb240cce060f9478cb361d1b6fad` | `100644` | `database/migrations/0338_autopilot_bounded_publication_permit.sql` |
| CREATE | `-` | `5f37acb6ae97694f93851c3288b0c580bbeecde9246c5bb2665f70327436a3a5` | `100644` | `database/migrations/0339_autopilot_native_cli_receipts.sql` |
| CREATE | `-` | `ace9c41ada9e6603775c33d9c621c8309578e70d15a11410b5641a752604c76b` | `100644` | `database/migrations/0340_autopilot_publication_permit_issuer.sql` |
| CREATE | `-` | `65040ae7eb04f90aff558f6745564f7377e5fc1aa5cbe49fdca38e9aa92b01bb` | `100644` | `database/rollbacks/0338_autopilot_bounded_publication_permit.sql` |
| CREATE | `-` | `daa7a2b1d6e37c620f41c6e780092de325d709d76c23e704c65341bfba1c5c18` | `100644` | `database/rollbacks/0339_autopilot_native_cli_receipts.sql` |
| CREATE | `-` | `aabb543cdcd257b83e244667077922582d950249ff4c2dd08dd605eae7f12e9d` | `100644` | `database/rollbacks/0340_autopilot_publication_permit_issuer.sql` |
| CREATE | `-` | `3d4b5094f23eb828fc19f1cb174364a2e7962e514f35eedfd3d5a355a57dbd90` | `100644` | `database/tests/338_autopilot_bounded_publication_permit.sql` |
| CREATE | `-` | `b5981aa2a5c4af870618b34bdedfdcfdcf77403de2737924173b86bd2791c940` | `100644` | `database/tests/339_autopilot_native_cli_receipts.sql` |
| CREATE | `-` | `c41b511c20850b01cb083434ba98e1b51ffb0f3c12f9eb9d25f004994fa5eba5` | `100644` | `database/tests/340_autopilot_publication_permit_issuer.sql` |
| CREATE | `-` | `656315b86e6eac422aa9abecbc41bdd9f92247559a3f829c6a57efa767ea1d82` | `100755` | `database/tests/340a_autopilot_publication_permit_concurrency.sh` |
| CREATE | `-` | `4ca1c67eb2e80742ba02cf8cb69add6a9122f874ee7e5ff783990b140d6710de` | `100644` | `oracle_autopilot/codex_cli_bridge.py` |
| CREATE | `-` | `72ce096ce6792ffbc3938a94cb90b9d04d7e4d1f290f43181fde79229a146010` | `100644` | `oracle_autopilot/codex_cli_delivery.py` |
| CREATE | `-` | `39c6fe1acbca6bb207e52df2ba07a2bb26b5a38a0e411db8b2818fe431e84cf5` | `100644` | `oracle_autopilot/codex_cli_queue.py` |
| CREATE | `-` | `8662fe46d0bc43d3463c3b8b8aef78dd6a2e5c3fa81b78e0335909d2887fc189` | `100644` | `oracle_autopilot/github_codex_publication.py` |
| CREATE | `-` | `35dc5676c346577df94af4ab272f70aead1e8e678f22bc770c7c2a391a34f3f4` | `100644` | `oracle_autopilot/github_codex_publication_permit.py` |
| CREATE | `-` | `bb1dc5120896afaa38c2bdf015c7ea3a2d1537bbdc9a7a07b41b6bb9de044ee5` | `100644` | `tests/test_oracle_autopilot_codex_cli_bridge.py` |
| CREATE | `-` | `ac1ca1aa466906b2126dd9e523df3ad343f625efa6f9d6977b249aadb71f16a4` | `100644` | `tests/test_oracle_autopilot_codex_cli_delivery.py` |
| CREATE | `-` | `36cd1bdc53792db0a65b6ab773bc53364972b672de2075066014e7df15ea5850` | `100644` | `tests/test_oracle_autopilot_codex_cli_queue.py` |
| UPDATE | `ee49d635e7151a466e35ee6e7506a3fe0b267e31` | `5d51053f1e015b1a0f692760200d26a8f4bff6958166704cd0869402d74e26de` | `100644` | `tests/test_oracle_autopilot_github_codex_callback_workflow.py` |
| CREATE | `-` | `bfd00a159878f7a3afa1348cb18ad2b5831572a9f7f60282bebfc2e7e1afcd54` | `100644` | `tests/test_oracle_autopilot_github_codex_publication.py` |
| CREATE | `-` | `039e130a3ae805540bda5e7ce588f24040db2b8777a657549a466a5b0c4785d3` | `100644` | `tests/test_oracle_autopilot_github_codex_publication_permit.py` |

## Dependency and application map

`EXTERNAL 0336` → `#1608 0337` → `#1546 0338 bounded publication` → `#1546 0339 native receipts` → `#1546 0340 owner permit issuer`.

- Repository integration base is #1608 exact head; #1546 must not duplicate 0337 or its repair/callback semantics.
- 0338 installs permit ledger + callback authorization RPC; callback can consume a permit but cannot issue one.
- 0339 installs native receipt/reservation fencing, disabled by default and without runtime grants.
- 0340 installs owner-only issuance. The offline verifier is not wired into a workflow/service; explicit owner-gated execution is required later.
- Publisher job remains gated by `AUTOPILOT_BOUNDED_PUBLICATION_ENABLED == true`; this package does not set that variable.

Intended repository commit message: `Autopilot: minimize PR1546 publication security package`.

## Rollback plan

Clean rollback order is `0340 → 0339 → 0338`. 0340 and 0338 refuse destructive rollback when permit evidence exists; 0339 refuses rollback when native receipt evidence exists. The focused PostgreSQL18 job verifies clean rollback, preservation of #1608 0337, reapply, and retest. Populated-ledger concurrency coverage verifies rollback failure retains evidence. No production rollback/apply is authorized by this package.

## Verification performed before repository publication

- Focused Python publication/callback/permit/native-receipt suite: **129 passed**.
- Both touched workflow YAML files parse successfully.
- Python compile and `git diff --check`: PASS.
- `database/tests/340a_autopilot_publication_permit_concurrency.sh`: shell syntax check PASS.
- No PostgreSQL production database or server state was changed for this local preparation. Hosted PostgreSQL18 lifecycle evidence is intentionally obtained only after repository publication.

## Exact-head CI checklist after repository publication

- [ ] `Autopilot publication security CI / focused-python` on exact #1546 head.
- [ ] `Autopilot publication security CI / postgresql18-publication-chain` on exact #1546 head: 0338/0339/0340 apply, focused SQL tests, concurrent issuer fencing, populated-evidence rollback refusal, clean rollback 0340→0339→0338, 0337 preservation, reapply.
- [ ] Existing `Autopilot role dispatch SQL CI` inherited from #1608.
- [ ] `Bridge School Database CI` PostgreSQL18 full migration/invariant suite.
- [ ] `Current-Main Authoritative CI` exact-head compatibility.
- [ ] Migration namespace guard proves one 0338/0339/0340 and no obsolete #1546 0336/0337 names.
- [ ] Independent I2+ security review on the exact head, after all CI fixes.

## Independent security-review checklist

- Immutable receiver: publisher executes `github.workflow_sha`, not submitted PR code.
- Only guarded publish job has `contents: write`; ACK/terminal remain read-only.
- Bounded existing-file allowlist plus sensitive-path denial is enforced before mutation.
- Publication is expected-head atomic and verifies parent/files/blob/live-head readback.
- Direct owner approval is distinct from command and publication comments; app-mediated copied approval is rejected; command/publication/approval records are re-fetched before issuance.
- Issuer binds dispatch/epoch/role/fingerprint/target/head/comment IDs/payload/provenance hash under database locks; replay is exact-idempotent, conflicting reuse fails closed.
- Concurrent conflicting permit issue loses under row lock; exact replay survives.
- `PUBLICATION_OUTCOME_UNKNOWN` and receipt-pending states do not claim false success.
- Rollback never silently destroys populated permit/native evidence.
- Permit issuer has no runtime/callback grant and no GitHub Actions invocation.
- No Video, Vision, recognizer, runtime-hardening, media, planner, server, credential, ruleset, Canon or Drive change is in the target diff.

## Remaining blockers

1. `EXTERNAL_0336_DEPENDENCY_PENDING` from the separately owned #1600 lane.
2. Exact-head hosted CI listed above must pass on the repository-published package.
3. Independent I2+ exact-head security review must be obtained after the final code/CI head.
4. Merge, production migration apply, publication activation, production permit issuance and canary remain separate owner gates.

Until those gates are satisfied, this package is repository/CI/review preparation only and publication stays disabled.
