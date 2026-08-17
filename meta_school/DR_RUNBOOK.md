# META School Disaster Recovery Runbook

Status: IMPLEMENTED-AS-RUNBOOK / NOT YET RECOVERY_READY

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
4. Compare expected schema/version.
5. Verify critical invariants: canonical IDs are unique; identity mappings remain resolvable; evidence links point to existing runs/artifacts; critical tables are queryable.
6. Run representative read-only application smoke checks.
7. Record elapsed time and calculate achieved RTO.
8. Record recovery-point age and calculate achieved RPO.
9. Only after successful isolated verification may a production cutover/restore be considered.

## Artifact recovery

1. Select representative critical Drive artifacts and irreplaceable sources.
2. Verify FileID/SourceID/provenance against ArtifactManifest.
3. Download/read a sample and compare checksum when available.
4. Verify independent copy/export path for irreplaceable material.
5. Confirm restored DB references resolve to the intended artifact/version.

## Code/config recovery

1. Identify last Stable Git commit associated with the known-good production evidence.
2. Verify migration files and tests are present.
3. Reproduce configuration from versioned non-secret config.
4. Secrets are restored from their authorized secret store, never from Git history or Drive documents.

## Recovery completion gates

Do not declare recovery complete until:
- database restore test PASS;
- schema/invariant checks PASS;
- critical artifact checks PASS;
- minimal application smoke PASS;
- RPO/RTO measured;
- Evidence record created;
- any discovered failure creates a regression control.

## Current baseline

Neon project: bridge-school-core / misty-poetry-18012774
Production: br-wispy-lab-b1rq54of
Recovery baseline created 2026-08-17: br-raspy-fog-b1l6rbbv (`meta-reliability-baseline-20260817`).

This baseline is an additional recovery point, not proof that the complete recovery procedure has passed.

## Owner-only blockers

If higher Neon recovery retention, Scale-only production protection/private networking, billing activation, or another paid account-level setting is required, stop before purchase/account-plan modification and request owner action/approval. Continue all non-billing technical work independently.