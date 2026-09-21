# Reliable Autopilot progress — 2026-09-21

Mode: ASSURED. Base: `ff7ac8951a2aec755d0542a5b5c8d7731de84277`.
Scope: reviewed manifest intake, bounded queue scheduling, PAUSED evidence recovery,
and production rollout verification. No new task authority or repair scope.

## Defects and decisions

- A changed batch digest changed the source of unchanged work keys, violating
  universal intake idempotency. Migration 0367 reuses strictly identical items
  only with proven original manifest provenance. Universal intake stays strict.
- PAUSED TSV parsing collapsed NULL fields, and the shell stopped after NO_CHANGE.
  The typed runner preserves NULLs, scans past no-ops and unreadable candidates,
  caps actual mutations at three, and reports partial read failures. Comment-only
  updates are not recovery evidence. Owner holds remain holds.
- A stale candidate could override the locked row's owner reason. The database
  now uses the authoritative reason; the new runner additionally uses timestamp CAS.
- Short-circuit and unbounded drains could starve other lanes. Each cycle visits
  all three lanes, at most six items each. Manifest failure is isolated, logged
  without database exception text, and retried no faster than once per minute.
- Service health alone did not establish intake. Rollout now checks all active
  statuses and probe reservations again after stopping, requires this systemd
  invocation's exact-digest intake log, and reads the durable receipt through a
  narrow read-only RPC before disarming rollback.
- Existing native CLI subprocess use conflicted with the global textual CI guard.
  Its exception is limited to the existing adapter; a structural regression
  requires its single fixed-CLI argv call, bounded timeout, no shell, and sanitized
  child environment. Other resident modules cannot spawn children.

## Evidence before promotion

- Local Python suite: 18,816 passed before final review additions; final count and
  exact-head CI results are recorded in the PR.
- Production-derived Neon rehearsal branch `br-wild-silence-b1b5s3e3`:
  migration apply and SQL 367 assertions passed. Test transaction rolled back;
  zero fixture rows remained.
- Actual release manifest `6e9668c97cd4c2b6b631bbb4e53564df45befd9a470b77202316fa0316da26b6`
  registered exactly one new item on that branch, retained all original work rows
  byte-for-byte, and produced a four-item receipt.
- Rollback restored both original function definitions (database digest comparison),
  retained the new work item/history, and reapply succeeded.
- Separate read-only Red Team review found CI rollback-chain and unreadable-first
  candidate gaps; both were addressed. PostgreSQL transactional tests and GitHub CI
  provide independent execution evidence beyond the same-model review.

## Promotion and rollback

Require exact-head CI green and no unresolved review blockers. Apply only migration
0367, then invoke the existing owner-gated worker rollout on control issue #1131.
Verify release SHA, current-process manifest receipt, and real task/dispatch state.
Do not equate SENT with completed work or PAUSED with success.

Worker rollout restores previous release/config/service on failure. Database
rollback 0367 restores function definitions and removes only new API/backup objects;
it never deletes work, tasks, dispatches or receipts. Roll back worker before database.
Old manifest RPC behavior can again reject future changed batches after DB rollback;
already admitted work/history remains intact. Keep the isolated rehearsal branch
until promotion evidence has been accepted; do not delete production evidence.

Permanent broker contract errors still fail closed under the existing bounded
delivery-attempt policy. This change does not relax broker pins, ownership checks,
scope, callback verification, or owner-authenticated dispatch requirements.
