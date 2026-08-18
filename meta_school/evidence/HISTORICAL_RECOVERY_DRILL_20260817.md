# Historical Recovery / Cross-Service Drill — 2026-08-17

Status: HISTORICAL_RECOVERY_POINT_VERIFIED / CROSS_SERVICE_PARTIAL

No production database writes, resets, restores, or cutovers were performed during this drill.

## Neon facts

Project: bridge-school-core
Project ID: misty-poetry-18012774
Production branch: br-wispy-lab-b1rq54of
Production protected: true
History retention: 2,592,000 seconds (30 days)

Historical recovery checkpoint used for the drill:
- branch: meta-reliability-baseline-20260817
- branch ID: br-raspy-fog-b1l6rbbv
- parent: production
- parent timestamp: 2026-08-17T06:47:57Z
- age at verification: more than 4 hours 50 minutes
- branch metadata showed no branch writes after creation

The checkpoint is materially historical, not an alias of current production. At verification time it contained:
- public base tables: 115
- analysis_run: 11
- transcript: 11
- transcript_segment: 8,975
- episode: 348
- decision: 109
- skill: 40
- evidence: 0
- evidence_link: 0
- dependency_edge: 0

Current production at the same drill contained later state:
- analysis_run: 12
- transcript: 12
- transcript_segment: 9,761
- episode: 698
- decision: 124
- skill: 121
- evidence: 356
- evidence_link: 549
- dependency_edge: 205

This difference is expected and demonstrates preservation of an older database state.

Historical integrity checks:
- unvalidated foreign keys: 0
- orphan transcript_segment -> transcript references: 0
- orphan asset_location -> asset references: 0
- orphan artifact_version_source -> artifact_version references: 0
- historical Drive locator for the sampled source video remained present and marked available/active

Result: PASS for historical recovery-point readability and internal database integrity.

Important scope note: the connected Neon branch-creation action does not expose a timestamp/LSN selector, so this run verified an existing isolated production snapshot from a known historical parent timestamp. It did not create a brand-new branch from an arbitrary timestamp during this run. Therefore strict arbitrary-timestamp PITR creation remains unproven.

## Google Drive artifact recovery sample

Created recovery area without modifying or deleting originals:
- folder: 98 — Recovery snapshots
- drill folder: 2026-08-17 — DR drill

Provider-side recovery copies created successfully for three representative critical artifacts:
1. current video-analysis algorithm document
2. current tournament-analysis/layout algorithm document
3. one irreplaceable source lesson video

Verification evidence:
- video-analysis algorithm original and recovery copy both exported to text/plain with exactly 143,202 bytes
- tournament-analysis algorithm original and recovery copy both exported to text/plain with exactly 47,624 bytes
- source video original and recovery copy both report exactly 1,699,568,400 bytes
- source video provider-side copy completed successfully without modifying the original

A direct raw-download verification of the 1.7 GB source video could not be completed through the connected Drive download action because that action has a 256 MiB per-file limit. This is a connector/tooling limit, not evidence of Drive source corruption.

Result: PASS for representative Drive provider-copy recovery and native-document export checks. Independent off-Drive recovery of large irreplaceable video sources remains unproven.

## GitHub / application health

Latest checked GitHub `Bridge School Neon Health Monitor` run on main:
- run ID: 32023086686
- status: completed
- conclusion: success
- started: 2026-08-17T11:03:33Z

Current production deployment observed in Vercel:
- deployment state: READY
- deployment commit: 5cc27890d31af670c814909e39590718de171e6f

Direct production health fetch at 2026-08-17T11:41:44Z:
- HTTP status: 200 OK
- body: {"status":"ok"}

Vercel runtime error check for the preceding hour: no runtime errors found.

Result: PASS for current production application health and code/deployment traceability.

## Recovery state decision

Database recovery evidence remains at least INTEGRITY_VERIFIED and now includes a verified historical recovery point older than one hour plus representative Drive recovery copies.

Whole-school RECOVERY_READY is intentionally NOT declared yet because two material gates remain unproven:
1. creation of a fresh Neon branch from an arbitrary historical timestamp/LSN during the drill;
2. application smoke test against the restored/historical branch itself, rather than against current production.

A third resilience gap remains for irreplaceable large source media: off-Drive independent recovery is not yet proven; the connected download path cannot verify files above 256 MiB.

These are evidence gaps, not observed production failures.