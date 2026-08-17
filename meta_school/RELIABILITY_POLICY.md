# META School Reliability Policy

Status: IMPLEMENTED-AS-POLICY / INFRASTRUCTURE PARTIALLY VERIFIED

## Reliability objectives

- Structured system of record: Neon/Postgres.
- Source/artifact storage: Google Drive, with ArtifactManifest and integrity metadata.
- Code/config/migrations/tests: GitHub.
- No single storage service is treated as a complete disaster-recovery strategy for the whole school.
- Initial target RPO: <= 1 hour for critical structured production data.
- Initial target RTO: <= 2 hours for critical production services.
- A backup is not VERIFIED until a restore test has succeeded and integrity checks pass.

## Current verified Neon baseline (2026-08-17)

Project: bridge-school-core
Project ID: misty-poetry-18012774
Region: aws-eu-central-1
PostgreSQL: 18
Production branch: br-wispy-lab-b1rq54of
Observed project history retention: 21600 seconds (6 hours)
Production branch protection observed: false
Public connections observed as not blocked; IP allowlist observed empty.

A recovery baseline branch was created from production:
- name: meta-reliability-baseline-20260817
- branch ID: br-raspy-fog-b1l6rbbv
- parent: production / br-wispy-lab-b1rq54of

This branch is a recovery checkpoint, not proof of a complete backup/restore strategy.

## Required reliability controls

### Database
1. Protect production from accidental destructive workflows where platform controls permit.
2. Maintain recovery points independent of experimental branches.
3. Test restoration on an isolated branch; never test destructive recovery on production.
4. Verify restored schema and critical row counts/invariants.
5. Record RecoveryEvidenceID, source branch/time, restore target, result and integrity checks.
6. Schema migrations use temporary branches and evidence before production promotion.
7. Critical writes should be idempotent where practical and carry stable IDs.
8. Monitor storage/branch limits and retention against RPO target.

### Artifact storage
1. Every critical artifact gets stable Artifact/FileID, SourceID, provenance and checksum when practical.
2. Student-facing delivery verifies identity/authorization and artifact version.
3. Unique irreplaceable source material should have an independent second copy or export path.
4. Deletion is never used as an automated reliability repair.
5. Periodically sample-download and checksum critical artifacts to detect silent loss/corruption.

### Code and configuration
1. GitHub is source of truth for code, schemas, tests and configuration.
2. Stable releases/commits must be identifiable from Run/Evidence records.
3. Production migrations must be reproducible from versioned files.
4. Secrets are not stored in repository content or artifact manifests.

## Recovery evidence states

- UNVERIFIED_BACKUP: copy/checkpoint exists but restore has not been proven.
- RESTORE_TESTED: isolated restore completed.
- INTEGRITY_VERIFIED: restored content passed defined integrity checks.
- RECOVERY_READY: restore + integrity + runbook evidence meet current RPO/RTO policy.

## Disaster recovery drill

At a safe interval and after major storage/schema changes:
1. Select a known recovery point.
2. Restore/branch into isolation.
3. Verify schema.
4. Verify critical table invariants and identity/evidence links.
5. Verify selected artifact references/checksums.
6. Run minimal application smoke checks.
7. Measure elapsed recovery time.
8. Record failures and create regression controls.
9. Do not declare RECOVERY_READY unless all required checks pass.

## Budget override

Reliability may exceed the normal META hard budget by up to 50%, only under `cost_governor.yaml` reliability-emergency policy. Spending must have a material expected reliability gain, no cheaper reliable alternative, logged reason/cost and post-action evidence.

## Current gaps

- 6-hour observed Neon history retention is shorter than the initial <=1h RPO strategy would ideally be supported by over a meaningful recovery horizon; retention/backup capability must be upgraded or supplemented before production launch.
- Production branch is currently observed unprotected.
- No complete independent restore test of the whole school data path has yet been evidenced.
- Drive artifact redundancy and checksum coverage are not yet proven end-to-end.
- Cross-service disaster recovery (Neon + Drive + GitHub + application) is not yet RECOVERY_READY.

Until these gaps are closed, storage/database reliability must not be labelled OPERATIONAL/RECOVERY_READY.