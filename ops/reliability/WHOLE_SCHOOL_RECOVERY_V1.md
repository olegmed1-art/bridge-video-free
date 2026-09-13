# Whole-school Recovery v1

Status: **IN PROGRESS**

Goal: prove that the critical School of Sports Bridge technical stack can be recovered from durable evidence without relying on undocumented manual state.

## Completion criteria

Recovery may be marked `RECOVERY_PROVEN_V1` only when all of the following are evidenced:

1. Neon has an independent backup outside Neon.
2. A restore test from that independent backup succeeds in an isolated target.
3. An isolated application deployment passes its read-path smoke against the restored database.
4. OCI boot-volume backup exists and a recurring backup policy is attached.
5. An isolated OCI boot-volume restore or equivalent VM rebuild has been performed and accepted.
6. A planned OCI reboot has been performed after backup and post-boot acceptance succeeds.
7. DDS3 post-recovery golden request returns the expected result with `engine=DDS3` and `fallback_used=false`.
8. Resident worker/control path resumes without duplicate completion.
9. Explicit RPO/RTO are recorded for critical components.
10. Backup freshness and recovery evidence have automated checks.
11. `recovery_checkpoint` and `recovery_verification` contain retained evidence for the drill.
12. No unique durable evidence exists only on Oracle local disk.
13. Local video/result cleanup is blocked until a durable copy and readback are verified.
14. Large irreplaceable source media has an independent recovery path outside the primary Google Drive account/provider boundary.
15. Critical recovery/acceptance workflows actually execute their jobs; an empty job set is not a passing control.
16. The technical-state registry is updated only from observed evidence.

## Recovery order

1. Establish control plane and repository access.
2. Restore/verify durable database state.
3. Restore public API/control layer from Git and environment configuration.
4. Run the isolated application against the restored database.
5. Restore deterministic compute from code/images/configuration.
6. Verify DDS3 golden acceptance.
7. Verify worker idempotency and queue state.
8. Verify source/evidence archive accessibility and independent large-media recovery.
9. Record recovery duration, data-loss window and database recovery evidence rows.

## Evidence policy

Do not convert `partial`, `unproven` or `unknown` to `proven` by inference. A successful internal Neon branch checkpoint is useful recovery evidence but is not an independent Neon backup. Service-active status is not equivalent to workload acceptance. A provider-side Drive copy in the same account is not an independent off-provider recovery copy. A workflow conclusion without an executed job is not acceptance evidence.

## Current 2026-08-25 evidence

- Production Neon branch is protected and ready.
- A fresh internal recovery checkpoint `recovery-governance-baseline-20260825` was created and read-tested successfully; it contained 194 public tables at verification time.
- A separate factual-audit branch restore was also created, read-tested and deleted without production writes.
- Internal Neon checkpoints remain inside Neon and therefore do not satisfy the independent-backup criterion.
- `recovery_checkpoint` and `recovery_verification` were observed empty during the factual audit.
- DDS3 has golden no-fallback evidence and current production health evidence, but full cross-service recovery remains partial.
- OCI read-only evidence run `32820354158` observed one available boot-volume backup and an assigned recurring policy; restore from that backup remains unproven.
- A 1,699,568,400-byte Drive source has a provider-side recovery copy, but raw export through the connected independent path remained blocked at 268,435,456 bytes.
- Vercel production was healthy at the audit snapshot but four commits behind `main` while new deployments were rate limited.
- `.github/workflows/dds3-runtime-container.yml` repeatedly concluded failure with zero observed jobs; its container golden path is not currently a functioning control.
- The machine-readable audit snapshot and deduplicated work queue are recorded in `ops/reliability/technical-state.yml`.

## Work tracking

- Canonical governance/reliability tracker: issue #523.
- Active WIP=1 recovery task: issue #526 — independent Neon backup and restore.
- Oracle backup/rebuild/recovery: issue #265.
- Universal Video durable result gate: issues #547 and #371.
- Other factual-audit blockers remain queued in `ops/reliability/technical-state.yml`; update the existing tracker before creating another issue.

## Safe rollback principle

Recovery drills must use isolated branches/targets. They must not repoint production traffic until acceptance criteria pass. Destructive cleanup is performed only on temporary recovery resources after evidence is retained.
