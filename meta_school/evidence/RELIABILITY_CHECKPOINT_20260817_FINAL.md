# META School Reliability Checkpoint — 2026-08-17

Status: CROSS_SERVICE_PARTIAL / DATABASE_HISTORICAL_RECOVERY_VERIFIED

## Re-verified live state

Neon project: bridge-school-core (misty-poetry-18012774)
- plan: Scale
- production branch: br-wispy-lab-b1rq54of
- production protected: true
- history retention: 2592000 seconds (30 days)
- production state: ready
- historical recovery point retained: meta-reliability-baseline-20260817 / br-raspy-fog-b1l6rbbv, parent timestamp 2026-08-17T06:47:57Z
- isolated DR branch retained: dr-drill-20260817 / br-weathered-silence-b11nrc37

GitHub/Vercel:
- main commit re-verified: 7a4a1a264be81b9c6fd829a6befe5bad0dc60f36
- Vercel combined commit status: success
- DR artifact manifest exists in meta_school/evidence/DR_ARTIFACT_MANIFEST_20260817.json

Google Drive representative large-video recovery:
- source: 1rGX92YskXRtXHc53lyj9JMU3g24H5vCI, 1699568400 bytes
- provider-side recovery copy: 134m-MIMziO05BkvLvArWQtGT_zNDcRhY, 1699568400 bytes
- source and copy sizes match
- production source was not modified or deleted

## Large-video policy

Large videos MAY be copied server-side inside Google Drive automatically when the copy operation itself is free and available storage quota is sufficient. Large size alone is not a reason to skip a recovery copy. Do not delete source or recovery copies automatically.

## Evidence status

PASS:
- protected production
- 30-day retention
- retained historical recovery point
- isolated DB recovery/integrity evidence
- representative Drive provider-side recovery copy
- GitHub evidence traceability
- current Vercel commit status success

NOT YET PROVEN:
1. Creating a new arbitrary timestamp/LSN PITR branch through the currently exposed Neon connector (the connector create_branch action has no timestamp/LSN parameter).
2. Running the application stack specifically against the historical recovery branch and proving application-level smoke checks on that recovered state.
3. An independent off-Drive raw copy of the 1.7 GB representative video through the currently connected Drive transfer channel. Provider-side Drive copy is proven; independent raw transfer is not.

Therefore whole-school RECOVERY_READY is intentionally not asserted. The strongest evidence-backed status is CROSS_SERVICE_PARTIAL with database historical recovery verified.

No production data was modified by this checkpoint.