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
- production Bridge Video `analysis_run`: 11 successful rows, all still unlinked to `algorithm_version_id` because candidate migration `0044` has not been promoted;
- production `storage_verification`: 0 rows because candidate migration `0044` has not been promoted.

No production database changes were made during this checkpoint.

## Candidate merged to `main`

Migration `0044_truth_storage_provenance` and regression test `031_truth_storage_provenance.sql` are merged to `main`.

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

GitHub Actions database candidate run `32000454269` succeeded on PostgreSQL 18.

Verified gates:

- clean migration installation;
- all invariant/adversarial database tests;
- migration idempotence;
- checksum-tamper guard;
- migration registry verification.

## Production-snapshot migration evidence

A temporary Neon branch was created from the production database solely to test `0044` against real current data.

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

The only health signal reported as critical on the manual test branch was `migration_checksums`, because the migration was executed directly through the Neon test connector rather than through `database/scripts/migrate.sh`; the manual test inserted the migration key but intentionally did not forge a repository checksum. The normal migration runner already has an independent checksum contract and passed CI.

The temporary Neon branch was deleted after verification to avoid leaving unnecessary resources.

## Remaining reliability boundaries

Truth Layer is now a tested repository candidate, not production operational.

Before production promotion:

1. keep production at `0019` until the release path is deliberately hardened;
2. sync the manual-only hardened `database-production` workflow to the actual `database-production` branch;
3. create a recoverable Neon checkpoint immediately before promotion;
4. verify production fingerprint/health before migration;
5. run the full production migration only through the explicit manual release gate;
6. verify migration checksums, runtime permissions, health, AlgorithmVersion linkage and StorageVerification counts afterward;
7. preserve the pre-promotion checkpoint until post-release verification is complete.

## Recovery risk still open

The current Neon project history-retention window is short (approximately 6 hours). This is adequate for immediate rollback experiments but is not, by itself, a long-term backup policy. Backup/restore and retention policy remains an explicit reliability task before financial or mass-user rollout.
