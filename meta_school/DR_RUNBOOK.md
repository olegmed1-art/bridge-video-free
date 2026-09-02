# META School Disaster Recovery Runbook

Status: IMPLEMENTED-AS-RUNBOOK / DATABASE INTEGRITY_VERIFIED / NOT YET WHOLE-SCHOOL RECOVERY_READY

## Trigger

Use for suspected database corruption/data loss, accidental destructive change, broken identity/evidence links, unavailable critical data, or a planned recovery drill.

## Immediate response

1. Classify incident P0/P1/P2.
2. For P0, stop nonessential writes and experiments.
3. Preserve the current state; do not delete or overwrite suspected evidence.
4. Record incident time, affected components, last known good RunID/EvidenceID and recovery-point candidates.
5. Activate reliability budget override only when it materially improves recoverability.

## Database recovery

1. Never rehearse destructive recovery on production.
2. Create/select an isolated Neon branch from the chosen recovery point.
3. Confirm branch/database availability.
4. Verify that the branch represents the intended point in time; compare known historical counts/state with current production when useful.
5. Verify schema and critical invariants: canonical IDs unique; foreign keys validated; identity mappings resolvable; evidence/artifact links valid; critical tables queryable.
6. Run representative read-only application smoke checks against the restored target itself when an isolated application configuration is available.
7. Record elapsed time and calculate achieved RTO.
8. Record recovery-point age and calculate achieved RPO.
9. Only after successful isolated verification may a production cutover/restore be considered.

## Artifact recovery

1. Select representative critical Drive artifacts and irreplaceable sources.
2. Verify FileID/SourceID/provenance against ArtifactManifest or equivalent identity evidence.
3. Copy/export/download a sample and compare checksum or equivalent deterministic indicators when available.
4. Verify an independent copy/export path for irreplaceable material.
5. Confirm restored DB references resolve to the intended artifact/version.
6. Never alter or delete the source artifact as part of a recovery drill.

## Code/config recovery

1. Identify last Stable Git commit associated with the known-good production evidence.
2. Verify migration files and tests are present.
3. Reproduce configuration from versioned non-secret config.
4. Secrets are restored from their authorized secret store, never from Git history or Drive documents.
5. Verify the candidate application deployment is READY and run a health check against the restored database target before any production cutover.

## Recovery completion gates

Do not declare recovery complete until:
- current-state database restore/branch test PASS;
- historical recovery-point test PASS;
- fresh arbitrary timestamp/LSN recovery path PASS;
- schema/invariant checks PASS;
- critical artifact recovery sample PASS;
- independent recovery path for large irreplaceable media PASS;
- minimal application smoke against restored target PASS;
- RPO/RTO measured;
- Evidence record created;
- any discovered failure creates a regression control.

## Verified baseline — 2026-08-17

Neon project: bridge-school-core / misty-poetry-18012774
Production: br-wispy-lab-b1rq54of
Production protected: true
History retention: 30 days

Verified current-state drill:
- `dr-drill-20260817` / br-weathered-silence-b11nrc37
- integrity checks passed

Verified historical checkpoint:
- `meta-reliability-baseline-20260817` / br-raspy-fog-b1l6rbbv
- production parent timestamp: 2026-08-17T06:47:57Z
- historical counts differed from current production as expected
- unvalidated foreign keys: 0
- sampled orphan-reference checks: 0

Verified Drive recovery sample:
- `98 — Recovery snapshots/2026-08-17 — DR drill`
- two critical algorithm documents copied; source/copy text exports matched by byte size
- one 1.7 GB source lesson video copied server-side; source/copy byte size matched
- direct raw download of that video is blocked by the connected Drive action's 256 MiB limit

Verified current application/code health:
- latest checked GitHub Neon Health Monitor run: success
- latest checked Vercel production deployment: READY
- production `/healthz`: HTTP 200 / status ok
- no Vercel runtime errors observed in the preceding hour at the drill

Evidence:
- `meta_school/evidence/DR_DRILL_20260817.md`
- `meta_school/evidence/HISTORICAL_RECOVERY_DRILL_20260817.md`

## Remaining blockers

1. The connected Neon branch-creation action cannot select a timestamp/LSN, so a fresh arbitrary historical PITR branch has not yet been created during a drill.
2. The restored/historical branch has not yet been used by an isolated application deployment for end-to-end smoke; current production was deliberately left unchanged.
3. Independent off-Drive recovery for large irreplaceable source media remains unproven because the connected download path caps files at 256 MiB.

Until these are closed, do not label the whole school RECOVERY_READY.