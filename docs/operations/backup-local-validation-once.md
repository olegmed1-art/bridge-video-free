# Review-only local backup validation, 2026-10-01

Status: prepared for review; NO live dispatch is authorized by publication.
ASSURED / I2. This supersedes the preflight-only harness at the same registered
workflow path on the review branch. Historical preflight observations remain
valid for their own exact SHAs; this code requires a new owner-approved attempt.

## Exact contract

- Repository: `olegmed1-art/bridge-video-free`.
- Branch: `test/neon-backup-maintenance-review-20261001`.
- Workflow: `.github/workflows/native-registry-credential-probe.yml`.
- `expected_main_sha`: `1440920191e1778fb9a9ba24e6701937a1a7459c`.
- `expected_review_sha`: exact published and independently reviewed commit;
  must match event SHA and checked-out HEAD. Never use a moving branch alone.
- `operation`: `LOCAL_VALIDATION_NO_UPLOAD_V1` (old preflight inputs cannot run it).
- `deadline_epoch`: explicitly approved UTC Unix deadline, in the future and
  no later than first job step timestamp +1200 seconds. Queue time can consume
  the approved window; expiry refuses source access.
- Actor AND triggering actor `olegmed1-art`; dispatch only; attempt 1.
- Only one new dispatch may be authorized. Attempt 1 blocks rerun but cannot
  prevent another fresh dispatch; the owner/operator enforces that restriction.

No operational workflow gate, trigger, schedule or main code is changed by this
harness. Do NOT merge this temporary workflow into main. Environment branch or
reviewer policy refusal is a blocker; do not change environment policy to bypass
it. GitHub environment bookkeeping is not application deployment.

## Data flow and limits

One standard `ubuntu-24.04` runner job, job timeout20min. It checks exact context,
checkout, and public current-main metadata before production access and again
before success. Existing `database-production` maintenance credential only:
`LIGHT_MAINTENANCE_DATABASE_URL`, role `neondb_owner`, database `neondb`, direct
TLS endpoint `ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech`, project
`misty-poetry-18012774`, branch `br-aged-mud-b1i64914`. The existing shared DSN,
health secret and all secret values remain unchanged/unread during preparation.
The existing `NEON_BACKUP_PASSPHRASE` is used only in an authorized execution.

One catalog identity/ACL/RLS/read-only preflight, one statistics query, one
read-only pg_dump (custom/compress9/no-owner/no-privileges, lockwait5s).
No automatic source retry, privilege change, production restore or writer pause.
The owner role remains powerful; startup read-only is a session restriction.

Source client: 1CPU/512MiB, no swap, read-only rootfs, bounded16MiB /tmp,
no capabilities, no-new-privileges, no core dumps or Docker logs. Only its private
output directory and read-only public CA file are mounted. Source password is
passed only to this subprocess/container, never argv or logs.

200MiB HARD file-size limit applies inside the dump container while writing;
360s client bound. Exceeding either fails, never expands data scope or retries.
Encryption/decryption use AES256CBC/PBKDF2-SHA256/250000 with the unchanged
passphrase, minimal child environment, 60s each and a200MiB prlimit while writing
(CBC overhead can refuse a near-limit dump). Original plaintext is removed after
encryption. Ciphertext hash is checked before decryption; recovered plaintext
hash must equal the original. No hashes or content are printed.

Restore runs in a separate Docker container on the SAME runner: network none,
no ports, no host mounts, no production DSN or passphrase. Read-only rootfs,
1GiB database tmpfs, bounded16MiB /tmp and socket tmpfs, 2CPU/2GiB memory with
swap disabled,256PIDs, no core dumps/logs. Local pg_restore receives plaintext
through stdin, is capped300s and exits on any SQL error. No extension installation
or size increase is attempted if stock PostgreSQL18 cannot restore the source.
This is process/container isolation, not a separate VM/security principal from
the trusted supervisor. The runner/provider is trusted with temporary plaintext.

Exact catalog user-schema/relation and critical job counts plus recovery-table
existence are checked. Role-independent pg_catalog counts include tables,
partitions, views, materialized views and foreign tables; schemas exclude pg_*
on BOTH sides, avoiding owner/superuser information_schema visibility differences.
Source statistics and dump
use different snapshots; active writers can produce a fail-closed mismatch.
Counts are never relaxed and writers are never stopped automatically.

## Deadline, cleanup, evidence

Supervisor signal deadline is the earliest of1080s from supervisor start,
1140s from first job step, and approved wall deadline minus40s. Every subprocess
has a remaining-monotonic-budget cap. An outer timeout and20min GitHub job limit
are additional bounds. Docker CLI death alone is not cleanup: finally removes
the exact run's source/restore containers including volumes, verifies daemon
absence, and removes the private directory. Cleanup reserves22s for Docker plus
small bounded file deletion; workflow always cleanup is a fallback. No unrelated
containers/jobs/concurrency groups are touched. Disk files are private0700/0600;
their maximum combined content is bounded by the three200MiB file caps.

Only sanitized JSON gates/status/source SHA in job summary. No raw exception,
SQL, client stdout/stderr, count, manifest, dump, hash, artifact upload/download,
issue comment, cache or external data destination. Restore success requires
cleanup confirmation. Missing summary, timeout, failed cleanup or cancellation
is NOT PASS; inspect job state and do not retry without new approval. GitHub
runner destruction is the fallback for catastrophic termination; secure media
erasure and instantaneous remote statement termination cannot be guaranteed.

## Meaning, cost, remaining approval

PASS validates a local encrypted roundtrip, NOT off-runner download, durable
backup retention, or disaster recovery after runner loss. Private long-term
storage and any October90day generation remain separate unresolved decisions.
This harness cannot create a monthly generation, even when UTC day=01.

No new Neon resource or service is created. Standard public-repository GitHub
runner compute is free; there is no artifact storage. Existing Neon compute and
egress may incur cost and require separate bounded owner approval. Read-only
dump still loads shared CPU/IO and takes ACCESS SHARE locks; no zero-impact claim.

Before execution: fresh exact main/review SHA and Neon control-plane identity,
independent code review, offline tests, environment policy eligibility, explicit
Neon cost decision, explicit single live-run approval with deadline. Publication
alone authorizes none of these live operations. Rollback: do not dispatch and
discard the review-only harness; no production rollback is needed.

Offline evidence: mock tests cover context/deadline/size/child isolation/cleanup
and all phase failures; standalone fresh loopback PostgreSQL18 fixture exercises
real dump/encrypt/corruption refusal/decrypt/restore, sequence and large objects.
Windows local environment lacks Docker; Docker resource/network enforcement is
specified and contract-tested, not falsely claimed as executed locally.
