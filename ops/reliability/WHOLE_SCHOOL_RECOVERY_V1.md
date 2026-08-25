# Whole-school Recovery v1

Status: **IN PROGRESS**

Goal: prove that the critical School of Sports Bridge technical stack can be recovered from durable evidence without relying on undocumented manual state.

## Completion criteria

Recovery may be marked `RECOVERY_PROVEN_V1` only when all of the following are evidenced:

1. Neon has an independent backup outside Neon.
2. A restore test from that independent backup succeeds in an isolated target.
3. OCI boot-volume backup exists and a recurring backup policy is attached.
4. A planned OCI reboot has been performed after backup and post-boot acceptance succeeds.
5. DDS3 post-recovery golden request returns the expected result with `engine=DDS3` and `fallback_used=false`.
6. Resident worker/control path resumes without duplicate completion.
7. Explicit RPO/RTO are recorded for critical components.
8. Backup freshness and recovery evidence have automated checks.
9. No unique durable evidence exists only on Oracle local disk.
10. The technical-state registry is updated only from observed evidence.

## Recovery order

1. Establish control plane and repository access.
2. Restore/verify durable database state.
3. Restore public API/control layer from Git and environment configuration.
4. Restore deterministic compute from code/images/configuration.
5. Verify DDS3 golden acceptance.
6. Verify worker idempotency and queue state.
7. Verify source/evidence archive accessibility.
8. Record recovery duration and data-loss window.

## Evidence policy

Do not convert `partial`, `unproven` or `unknown` to `proven` by inference. A successful internal Neon branch checkpoint is useful recovery evidence but is not an independent Neon backup. Service-active status is not equivalent to workload acceptance.

## Current 2026-08-25 evidence

- Production Neon branch is protected and ready.
- A fresh internal recovery checkpoint `recovery-governance-baseline-20260825` was created and read-tested successfully; it contained 194 public tables at verification time.
- The checkpoint remains inside Neon and therefore does not satisfy the independent-backup criterion.
- DDS3 has prior golden no-fallback evidence, but full cross-service recovery remains partial.
- OCI administrative bootstrap/recovery remains tracked separately and must be proven before OCI backup/reboot work is considered complete.

## Safe rollback principle

Recovery drills must use isolated branches/targets. They must not repoint production traffic until acceptance criteria pass. Destructive cleanup is performed only on temporary recovery resources after evidence is retained.
