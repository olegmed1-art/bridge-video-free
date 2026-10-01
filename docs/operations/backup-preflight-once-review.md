# Review-only preflight harness — one authorized attempt

Authorization: owner approved one read-only authentication/permissions preflight,
maximum two minutes, on 2026-10-01. No dump, backup, upload, restore, merge, secret
change, grants or production writes. This does not authorize another dispatch or
rerun after a failure. No live attempt has been made during harness preparation.

The backup workflow has no preflight-only dispatch. GitHub requires a manually
dispatchable workflow to exist on the default branch. Therefore this review branch
temporarily reuses the ALREADY registered read-only workflow path
`.github/workflows/native-registry-credential-probe.yml` as a dedicated backup
preflight harness. Its main version is untouched. This temporary harness must not
be merged or cherry-picked as part of the backup credential cutover.

## Exact dispatch contract

- Workflow path: `.github/workflows/native-registry-credential-probe.yml`.
- Branch: `test/neon-backup-maintenance-review-20261001`, never `main`.
- `expected_main_sha`: `1440920191e1778fb9a9ba24e6701937a1a7459c`.
- `expected_review_sha`: the full published harness commit SHA supplied with the
  final review result (must equal the selected branch head/event SHA).
- Actor and triggering actor: `olegmed1-art`; event `workflow_dispatch`; attempt 1.
- Exactly one dispatch. Do not use the operational backup workflow, issue comment
  recovery command, or recovery trigger branch. Missing/unavailable browser inputs
  are a blocker, not permission to choose another workflow or main.

`run_attempt=1` rejects reruns of an existing run; it cannot prevent a second new
dispatch, whose attempt also starts at 1. The operator must enforce the single
authorized dispatch. Concurrency serializes jobs and does not grant retry authority.

The connected toolset used to prepare this patch exposes no dispatch action.
The parent operator must freshly verify branch SHA and Neon project/branch/endpoint
mapping, then use the browser's registered workflow and selected review branch.
If environment branch policy refuses the review branch, stop; do not modify policy,
move secrets or merge as a workaround.

## What executes

One ubuntu-24.04 job with `timeout-minutes: 2`; SHA-pinned checkout; one observer
command under a 100-second timeout plus 5-second kill grace. No setup/install,
artifact action or secondary job. Only the observer step references the existing
`database-production` `LIGHT_MAINTENANCE_DATABASE_URL`; neither the common secret
nor backup passphrase is referenced.

The observer rechecks context/checkout and current main, invokes the reviewed
backup helper's `preflight` exactly once (no generic mode dispatch), then checks
main again. One PostgreSQL 18 container invocation performs catalog SELECT under
BEGIN READ ONLY / ROLLBACK with read-only startup options. The container may need
to pull its image; cold-pull time counts toward the same 45-second client budget.
Connection timeout is 10 seconds, statement timeout 10 seconds; timeout cleanup
targets only its named container and is limited to 15 seconds. No automatic retry.

Expected identity: project `misty-poetry-18012774`, branch `br-aged-mud-b1i64914`,
endpoint `ep-noisy-pine-b1pe30sf`, database `neondb`, current/session user
`neondb_owner`. ACLs cover schemas, tables/partitions/materialized views, sequences
and large objects, with active RLS rejected. No row counts or table data are read.

## Receipt and interpretation

`backup-preflight-receipt.json`, stdout and the GitHub step summary contain the
same sanitized receipt: expected identity, exact source SHA/run ID/attempt,
auth/identity/ACL/RLS/read-only gates, PASS/FAIL, phase, elapsed time and limits.
No artifact upload occurs. PASS means all observed gates passed; FAIL before the
catalog result marks gates NOT_PROVEN rather than inventing an authentication
diagnosis. A post-observation main drift produces overall FAIL even if gates were
observed passing. No client errors, server-returned strings or DSN are serialized.

The offline diagnostic delta adds receipt v2 with fixed failure codes and
incremental verified gates; see `backup-preflight-diagnostics.md`. Historical v1
receipts are unchanged. That delta does not authorize another live dispatch.

If checkout, job cancellation or the outer watchdog prevents receipt generation,
retain the GitHub run status and mark the observation INCONCLUSIVE with gates NOT_PROVEN; do not
claim PASS or rerun automatically. After the single dispatch, the parent should
capture its sanitized receipt and run URL locally. Backup/restore remain separately
gated even after PASS.
