# META Truth Layer — reliability checkpoint

Date: 2026-08-17

## Scope

This checkpoint records the first Truth Layer reliability pass after Auth round 8. It is factual status evidence, not a production-promotion declaration.

## Production state remains unchanged

Direct Neon verification after the candidate work confirmed:

- project: `bridge-school-core`;
- PostgreSQL: `18.4`;
- production `schema_migration`: `0001–0019` only;
- production operational health: 15 `ok`, 0 warning, 0 critical;
- production Bridge Video `analysis_run`: 11 successful rows, all still unlinked to `algorithm_version_id` because candidate Truth migration has not been promoted;
- production `storage_verification`: 0 rows because candidate Truth migration has not been promoted.

No production database changes were made during this checkpoint.

## Candidate merged to `main`

The Truth candidate is `0045_truth_storage_provenance` with regression test `032_truth_storage_provenance.sql`.

The semantic candidate was first tested while temporarily numbered `0044`. Concurrent development then introduced `0044_instructor_education_scope.sql`. Because neither Truth migration had been promoted to production, the Truth migration was safely renumbered to `0045` and the Truth regression test to `032`; the migration runner was also hardened to reject any future duplicate migration or database-test numeric prefix before applying migrations.

The candidate adds:

1. canonical `bridge-video-master-analysis` Algorithm identity;
2. registered AlgorithmVersion identities through `3.1-free-r25.11`;
3. historical backfill of `analysis_run.algorithm_version_id`;
4. fail-closed rejection of future unregistered Bridge Video revisions;
5. consistency guard between string algorithm identity and canonical AlgorithmVersion ID;
6. explicit append-only `storage_verification` evidence derived from verified AssetLocation state plus Asset checksum identity;
7. automatic evidence capture for future verified AssetLocation writes;
8. removal of runtime UPDATE/DELETE rights on storage verification history.

Registration of an AlgorithmVersion is identity/provenance only. It does not imply Stable or Operational quality status.

## CI evidence

The semantic Truth candidate passed GitHub Actions database run `32000454269` on PostgreSQL 18 before the sequence-only renumbering.

Verified gates:

- clean migration installation;
- all invariant/adversarial database tests;
- migration idempotence;
- checksum-tamper guard;
- migration registry verification.

The final `0045`/`032` numbering and duplicate-sequence guard require their own post-renumber CI confirmation before this checkpoint is used as release evidence.

## Production-snapshot migration evidence

A temporary Neon branch was created from the production database solely to test the Truth migration semantics against real current data.

Temporary branch ID: `br-fragrant-dawn-b11ncyzk`.

After applying the candidate migration on that isolated production snapshot:

- canonical Bridge Video algorithms: 1;
- registered Bridge Video versions: 9;
- historical Bridge Video AnalysisRun rows: 11;
- linked AnalysisRun rows after backfill: 11;
- unlinked rows: 0;
- AssetLocation rows: 15;
- StorageVerification evidence rows: 15;
- AssetLocation rows with verification evidence: 15;
- all 15 verification records were `available` and checksum-bound to the Asset registry.

The only health signal reported as critical on the manual test branch was `migration_checksums`, because the migration was executed directly through the Neon test connector rather than through `database/scripts/migrate.sh`; the manual test inserted the migration key but intentionally did not forge a repository checksum. The normal migration runner has an independent checksum contract.

The temporary Neon branch was deleted after verification to avoid leaving unnecessary resources.

## Remaining reliability boundaries

Truth Layer is a tested repository candidate, not production operational.

Before production promotion:

1. keep production at `0019` until the complete reviewed release set is deliberately prepared;
2. preserve the recovery branch `recovery-prod-0019-20260817`;
3. verify production fingerprint/health immediately before migration;
4. run the full production migration only through the explicit manual release gate;
5. verify migration checksums, runtime permissions, health, AlgorithmVersion linkage and StorageVerification counts afterward;
6. preserve the pre-promotion recovery state until post-release verification and observation are complete.

## Recovery risk still open

The current Neon project history-retention window is short (approximately 6 hours). A separate recovery branch now preserves the current `0019` production state, improving recoverability, but this is still not a complete long-term backup/restore policy. A restore-test remains required before financial or mass-user rollout.
