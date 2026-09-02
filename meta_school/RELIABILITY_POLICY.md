# META School Reliability Policy

Status: IMPLEMENTED-AS-POLICY / DATABASE INTEGRITY_VERIFIED / WHOLE-SCHOOL NOT YET RECOVERY_READY

## Reliability objectives

- Structured system of record: Neon/Postgres.
- Source/artifact storage: Google Drive, with ArtifactManifest and integrity metadata.
- Code/config/migrations/tests: GitHub.
- No single storage service is treated as a complete disaster-recovery strategy for the whole school.
- Initial target RPO: <= 1 hour for critical structured production data.
- Initial target RTO: <= 2 hours for critical production services.
- A backup is not VERIFIED until a restore test has succeeded and integrity checks pass.

## Current verified baseline (2026-08-17)

Neon project: bridge-school-core / misty-poetry-18012774
Region: aws-eu-central-1
PostgreSQL: 18
Production: br-wispy-lab-b1rq54of
Production protection: true
History retention: 2,592,000 seconds (30 days)

Current-state isolated drill branch:
- dr-drill-20260817 / br-weathered-silence-b11nrc37
- current-state schema/data checks passed

Historical recovery checkpoint verified:
- meta-reliability-baseline-20260817 / br-raspy-fog-b1l6rbbv
- parent timestamp: 2026-08-17T06:47:57Z
- historical state differed from current production as expected and remained internally consistent
- unvalidated foreign keys: 0
- tested orphan-reference checks: 0

Google Drive representative recovery sample:
- a dedicated `98 — Recovery snapshots/2026-08-17 — DR drill` area was created without modifying originals
- two critical native algorithm documents were copied and their text exports matched by byte size between source and recovery copies
- one 1.7 GB source lesson video was copied server-side; original and recovery copy reported the same byte size
- the connected raw-download action cannot verify that 1.7 GB file because it enforces a 256 MiB per-file limit

Application/code health at the drill:
- GitHub `Bridge School Neon Health Monitor` latest checked run: success
- latest checked production Vercel deployment: READY
- direct production `/healthz`: HTTP 200 with `{"status":"ok"}`
- Vercel runtime errors in the preceding hour: none observed

Evidence:
- `meta_school/evidence/DR_DRILL_20260817.md`
- `meta_school/evidence/HISTORICAL_RECOVERY_DRILL_20260817.md`

## Required reliability controls

### Database
1. Protect production from accidental destructive workflows where platform controls permit.
2. Maintain recovery points independent of experimental branches.
3. Test restoration in isolation; never rehearse destructive recovery on production.
4. Verify restored schema and critical row counts/invariants.
5. Verify historical recovery points, not only copies of the current state.
6. Record source branch/time, restore target, result and integrity checks.
7. Schema migrations use temporary branches and evidence before production promotion.
8. Critical writes should be idempotent where practical and carry stable IDs.
9. Monitor storage/branch limits and retention against the RPO target.

### Artifact storage
1. Every critical artifact gets stable Artifact/FileID, SourceID, provenance and checksum or equivalent integrity evidence when practical.
2. Student-facing delivery verifies identity/authorization and artifact version.
3. Unique irreplaceable source material should have an independent second copy or export path.
4. Deletion is never used as an automated reliability repair.
5. Periodically sample-copy/download/export critical artifacts to detect silent loss/corruption.

### Code and configuration
1. GitHub is source of truth for code, schemas, tests and non-secret configuration.
2. Stable releases/commits must be identifiable from Run/Evidence records.
3. Production migrations must be reproducible from versioned files.
4. Secrets are not stored in repository content or artifact manifests.
5. Application smoke recovery must be tested against the restored database target, not only against current production.

## Recovery evidence states

- UNVERIFIED_BACKUP: copy/checkpoint exists but restore has not been proven.
- RESTORE_TESTED: isolated restore completed.
- INTEGRITY_VERIFIED: restored content passed defined integrity checks.
- RECOVERY_READY: restore + integrity + artifact recovery + restored-target application smoke + measured RPO/RTO meet current policy.

## Disaster recovery drill

At a safe interval and after major storage/schema changes:
1. Select a known recovery point.
2. Restore/branch into isolation.
3. Verify schema and critical table invariants.
4. Verify identity/evidence and other representative foreign-key relationships.
5. Verify selected artifact references and copy/export integrity.
6. Run minimal application smoke checks against the restored target.
7. Measure elapsed recovery time and recovery-point age.
8. Record failures and create regression controls.
9. Do not declare RECOVERY_READY unless all required checks pass.

## Current factual blockers to RECOVERY_READY

1. A fresh branch from an arbitrary historical timestamp/LSN has not yet been created during a drill; the connected Neon create-branch action does not expose a timestamp/LSN selector. The existing historical recovery checkpoint is verified, but strict arbitrary-timestamp PITR creation remains unproven.
2. Application smoke has not yet been run against the restored/historical branch itself. Current production health is verified, but production configuration was deliberately not changed during the drill.
3. Independent off-Drive recovery for large irreplaceable source media is not yet proven. Provider-side Drive copying works, but the connected download action rejects files above 256 MiB.
4. Because the two recovery-path gates above are unproven, achieved whole-service RPO/RTO is not yet measured and RECOVERY_READY remains false.

These are evidence gaps, not observed production failures.