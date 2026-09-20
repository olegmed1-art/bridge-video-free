# PR1546_MINIMAL_V1 — publication security evidence

## Identity and hard boundaries

- Base `main`: `2189f1014bedaee69c3304b7e90670a3a1785d46`.
- Scope: exactly 23 functional paths plus this evidence file; no deletes relative to base.
- Merged dependencies: main owns 0336/0337; PR #1625 supplies the upper-chain-aware role-dispatch CI roundtrip. PR #1546 owns only 0338/0339/0340 and directly required publication/native-receipt code/tests.
- PR #1600 is not inspected, reviewed, reconstructed, commented on, or modified by this lane.
- Publication is **DISABLED**. Permit issuance is **DISCONNECTED FROM RUNTIME/ACTIONS**.
- No merge, production migration/apply, activation, production permit, canary, planner, server, credential, ruleset, Canon or Drive change is authorized here.
- Retired `PUBLICATION_V5` hash `c54cec548c5ae41205a714bd66be2dc98b10005166ad78916a6881ba8f803c03` stays retired; chunk 7/69 is never resumed.

## KEEP / dependency / DROP classification

`REQUIRED_FOR_1546_CORE`: `.github/workflows/autopilot-codex-event-callback.yml`, `.github/workflows/autopilot-publication-security-ci.yml`, migrations/rollbacks/tests 0338–0340 (including 340a), `oracle_autopilot/codex_cli_bridge.py`, `codex_cli_delivery.py`, `codex_cli_queue.py`, `github_codex_publication.py`, `github_codex_publication_permit.py`, their focused Python tests, and this evidence file. These implement the immutable guarded receiver, bounded existing-file allowlist, exact-head CAS/readback, owner-bound provenance permit, replay/reuse/concurrency fencing, native receipt retention, fail-closed unknown outcome, and evidence-preserving rollback.

`REQUIRED_DEPENDENCY_FOR_1546`: `oracle_autopilot/github_codex_callback.py`; merged-main 0336/0337 migrations/rollbacks/tests; and `.github/workflows/autopilot-role-dispatch-sql-ci.yml` including merged PR #1625. They are inherited unchanged.

`UNRELATED_DROP`: any #1546 update to the role-dispatch workflow; all Video/Vision/recognizer/runtime-hardening/media paths; obsolete #1546 0336/0337 publication/native-receipt names; and stale pre-minimization evidence docs. Any retired-stream path not explicitly enumerated is `UNCERTAIN_NEEDS_PROOF` and excluded.

## Exact operations and content identities (functional core)

Canonical core hash algorithm: SHA-256 over `PR1546_MINIMAL_V1\nbase=<main-head>\n` plus lexicographically sorted tab-separated `path, operation, base_blob_sha, content_sha256, mode` records.

- Functional core aggregate SHA-256: `50c4961ea3fbca849404f98835b7ed6594a7d40b4b49727b8fe8cc242ac0f0ca`.
- DELETE operations: **none** relative to merged `main`.

| Operation | Base blob SHA | Content SHA-256 | Mode | Path |
|---|---|---|---|---|
| UPDATE | `2c49fcf95bc3ded580c842ec0dbe61f08271f6f5` | `b8107d35df94adae2fe83c788fd3ba71284bd25be7f13650abef183524dbffbf` | `100644` | `.github/workflows/autopilot-codex-event-callback.yml` |
| CREATE | `-` | `6306e146bb070fc6fd322daab767c25379a90e1f45d0095f3a5208de7696afb5` | `100644` | `.github/workflows/autopilot-publication-security-ci.yml` |
| CREATE | `-` | `12ea33b39d50ee1afb0436959230c070a89ab968f948bf4c738c599290bb289e` | `100644` | `database/migrations/0338_autopilot_bounded_publication_permit.sql` |
| CREATE | `-` | `5f37acb6ae97694f93851c3288b0c580bbeecde9246c5bb2665f70327436a3a5` | `100644` | `database/migrations/0339_autopilot_native_cli_receipts.sql` |
| CREATE | `-` | `ec0de1a0e176a3b2ee875c7c62c2636d8ed9b51bff8446a65874d315ab1d9395` | `100644` | `database/migrations/0340_autopilot_publication_permit_issuer.sql` |
| CREATE | `-` | `65040ae7eb04f90aff558f6745564f7377e5fc1aa5cbe49fdca38e9aa92b01bb` | `100644` | `database/rollbacks/0338_autopilot_bounded_publication_permit.sql` |
| CREATE | `-` | `849176f6595f857f9fed2f7a29a65ba1f85729c7ab8e9fce517bfda47098351e` | `100644` | `database/rollbacks/0339_autopilot_native_cli_receipts.sql` |
| CREATE | `-` | `9e6d92afa13d33e26d536fe39ec92cf48905a98b2c502a73534c1d5da8fbcf66` | `100644` | `database/rollbacks/0340_autopilot_publication_permit_issuer.sql` |
| CREATE | `-` | `7d5c63a5ec3d368b099387a158b33a64b217e70291ca6556728bad4fa3f1f81a` | `100644` | `database/tests/338_autopilot_bounded_publication_permit.sql` |
| CREATE | `-` | `b5981aa2a5c4af870618b34bdedfdcfdcf77403de2737924173b86bd2791c940` | `100644` | `database/tests/339_autopilot_native_cli_receipts.sql` |
| CREATE | `-` | `bf3fedee059b4487c009467d99c10cfaf8f3620913ed2e7fa5d83a0fca54642e` | `100644` | `database/tests/340_autopilot_publication_permit_issuer.sql` |
| CREATE | `-` | `f53f576760f1a5fc5dda6d50ddb4999071b16beef26325a6ed9b3be28db4b94c` | `100755` | `database/tests/340a_autopilot_publication_permit_concurrency.sh` |
| CREATE | `-` | `4ca1c67eb2e80742ba02cf8cb69add6a9122f874ee7e5ff783990b140d6710de` | `100644` | `oracle_autopilot/codex_cli_bridge.py` |
| CREATE | `-` | `72ce096ce6792ffbc3938a94cb90b9d04d7e4d1f290f43181fde79229a146010` | `100644` | `oracle_autopilot/codex_cli_delivery.py` |
| CREATE | `-` | `39c6fe1acbca6bb207e52df2ba07a2bb26b5a38a0e411db8b2818fe431e84cf5` | `100644` | `oracle_autopilot/codex_cli_queue.py` |
| CREATE | `-` | `5a3db72ee352854eab33da712f3e9ef8aef6932275f4a2939a87122003862e47` | `100644` | `oracle_autopilot/github_codex_publication.py` |
| CREATE | `-` | `1e831b5ed4236ff71ce3f4d4d8a78778c6b3881fefdc2b35028dcd121815b984` | `100644` | `oracle_autopilot/github_codex_publication_permit.py` |
| CREATE | `-` | `bb1dc5120896afaa38c2bdf015c7ea3a2d1537bbdc9a7a07b41b6bb9de044ee5` | `100644` | `tests/test_oracle_autopilot_codex_cli_bridge.py` |
| CREATE | `-` | `ac1ca1aa466906b2126dd9e523df3ad343f625efa6f9d6977b249aadb71f16a4` | `100644` | `tests/test_oracle_autopilot_codex_cli_delivery.py` |
| CREATE | `-` | `36cd1bdc53792db0a65b6ab773bc53364972b672de2075066014e7df15ea5850` | `100644` | `tests/test_oracle_autopilot_codex_cli_queue.py` |
| UPDATE | `ee49d635e7151a466e35ee6e7506a3fe0b267e31` | `0aaeb9fe2b883c8f88acda4c8e8c0765991cf453e608c6e710d072b893c7342d` | `100644` | `tests/test_oracle_autopilot_github_codex_callback_workflow.py` |
| CREATE | `-` | `4ec05baba723dcade22a4c71ba1479172498738fe851010be54be947b6284037` | `100644` | `tests/test_oracle_autopilot_github_codex_publication.py` |
| CREATE | `-` | `6e1513cf22d01444de320f830b68da51896390e4fa36f535f7e09aa66b6be8d8` | `100644` | `tests/test_oracle_autopilot_github_codex_publication_permit.py` |

## Dependency, security and rollback map

`main 0336` → `main 0337` → `#1546 0338 bounded publication` → `#1546 0339 native receipts` → `#1546 0340 owner permit issuer`.

- 0338 callback authorization can consume but cannot issue permits. Mutation requires ≥180 seconds; exact-commit recovery is read-only and requires ≥120 seconds callback budget.
- 0339 is disabled by default, installs no runtime grant, retains ambiguous native outcomes, and its rollback takes `ACCESS EXCLUSIVE` before testing ledger emptiness.
- 0340 is owner-only, accepts TTL 180–900 seconds using one captured issuance timestamp, binds exact dispatch/task/head/provenance fields, and has no callback/runtime grant.
- Clean rollback order is `0340 → 0339 → 0338`; 0340 serializes with in-flight owner issuance through a shared transaction advisory fence and post-fence migration-marker recheck, while populated evidence causes fail-closed refusal.
- Publisher is still gated by `AUTOPILOT_BOUNDED_PUBLICATION_ENABLED == true`; this package does not set that variable.

## Verification and review history

- Focused Python publication/callback/permit/native-receipt suite after the latest fixes: **134 passed**.
- Workflow YAML parse, Python compile, `git diff --check`, and 340a shell syntax: PASS.
- Earlier PostgreSQL18 package lifecycle validation passed SQL 338/339/340, 340a fencing/rollback-write checks, clean rollback preserving 0337, and reapply. Because the latest exact-head fixes modify SQL/340a, fresh hosted PostgreSQL18 CI is authoritative and still required.
- Prior P2 migration-history rewrite: fixed by denying all `database/migrations/**` publication writes.
- Prior P2 native-receipt rollback race: fixed by `ACCESS EXCLUSIVE` plus deterministic lock barriers.
- Prior receipt-window P2: fixed with 180-second mutation authority, second pre-CAS authorization, `RECOVERY_ONLY`, and a 120-second recovery receipt margin.
- Latest P2 minimum TTL bug: fixed by capturing `issued_at` once; TTL=180 is explicitly regression-tested.
- Latest P2 concurrent issuer test race: fixed by polling `pg_stat_activity`/`pg_locks` for session A's granted role-dispatch lock before starting session B.
- Latest P2 0340 rollback/issuer race: fixed with a shared transaction advisory fence between owner issuance and rollback, a post-fence migration-marker recheck, and deterministic 340a proofs for both issuer-first and rollback-first ordering.
- Hosted PostgreSQL18 synchronization regression on `94b41f3` was isolated to the 0339 blocker test identity: the blocker changed `application_name` after taking its lock while the barrier polled the original name. The blocker now retains `pr1546-0339-blocker`, so the `pg_locks` barrier observes the intended session deterministically.

## Final gates

Fresh exact-head hosted CI must pass: publication security PostgreSQL18/Python, inherited role-dispatch SQL CI, Bridge School Database CI, Current-Main Authoritative CI, and Migration Namespace Guard. Then a fresh independent I2+ security review must cover that same exact head.

Merge #1546, production migration apply, publication activation, production permit issuance and canary remain separate owner gates.
