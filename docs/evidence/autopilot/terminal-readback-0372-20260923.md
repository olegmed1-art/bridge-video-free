# Codex terminal readback — 2026-09-23

Status: repository candidate; no migration, workflow, or production callback has been activated.
Both recovery workflows are disabled by default until the 0372 migration and
exact-head CI have passed, and `AUTOPILOT_CODEX_READBACK_ENABLED` is set to
`true` through the reviewed rollout. The event receiver stays active.

## Scope and reason

Issue #1510 records original Codex terminal comments that appeared on GitHub
without a durable callback receipt. The event-only receiver has no recovery
when a GitHub `issue_comment` workflow event is missed. This change adds a
five-minute bounded sweep while the original delivery/callback deadline is
still open. The receiver and database RPC retain authority over actor/app,
dispatch epoch/fingerprint/target, head, deadline, idempotence, and slot release.

Migration 0372 exposes only up to seven active v3 dispatch identities to the
existing `autopilot_callback` principal; it does not grant table SELECT. The
sweep refuses more than six candidates, reads at most two pages of 100 comments
per PR, double-reads one original comment, rejects an edited/duplicate result,
and calls a separate REST provenance terminal RPC only after the exact PR head check. The
manual one-comment readback path is retained for a precise incident recovery.

The REST RPC derives its binding/deadline/idempotence logic from the exact
installed event RPC, but takes `p_api_readback_verified`, not the event path's
`p_signature_verified`. Its private core is owner-only; the callback principal
can execute only the public wrapper. New receipts retain
`GITHUB_REST_DOUBLE_READ` as durable provenance. Existing event receipts retain
`ISSUE_COMMENT_EVENT`; rollback refuses to erase any REST receipt provenance.
Every REST admission compares the private core to the currently installed
event RPC. A later hotfix to either function causes `CORE_DRIFT` and fails
closed until both are reviewed together.

Malformed bot results, duplicate terminal comments, wrong target head, and an
unapplied repair head retain distinct bounded diagnostic codes with dispatch,
PR and original comment ID where available. Callback role can execute only a
guarded diagnostic RPC; it cannot insert into the diagnostic table directly.

## Boundaries and acceptance

- No synthetic result, deadline extension, direct status update, retry budget
  reset, mailbox change, 0355 removal, server rollout, or Neon relocation.
- GitHub cron timing is best-effort. A comment that appears too near deadline
  or after it remains unaccepted; database acceptance checks its own clock.
- More than 200 comments in the candidate's publication window fails closed.
- A generic provider failure comment is handled by the existing event receiver
  or the exact manual readback; the automatic sweep admits machine-bound
  `AUTOPILOT_CODEX_RESULT_V1` comments only.
- A genuine callback accepted once, a duplicate readback, an unrelated review,
  a stale head, or an expired callback must retain the existing SQL behavior.
- Rollback: disable the new schedule/remove its workflow, then apply 0372
  rollback only if no REST receipts or diagnostics exist. Existing event receiver
  and retained receipts remain intact. If either exists, preserve its table/column.

## Evidence before publication

Focused Python tests pass locally. SQL migration, rollback, and test files
parse in `pglast`; PostgreSQL 18 execution remains an exact-head CI gate. The
existing SQL 336 regression verifies expired and duplicate terminal RPC
behavior. Independent I2 review and a fresh production queue/route inventory
are still required before migration/activation.
