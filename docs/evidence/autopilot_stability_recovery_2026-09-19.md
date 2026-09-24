# Autopilot production stability recovery — 2026-09-19

- Change: `0356_autopilot_provider_health_recovery`
- Governance mode: `ASSURED`
- Scope: provider circuit recovery, paused-backlog health semantics, mailbox
  pre-rotation database boundary
- Baseline: `main` at `8474548071105e164f79b512b1d0de06a7f17a02`

## Incident facts

- Production had no active tasks, live dispatches, live leases, or expired
  leases. The planner was enabled with 19 intentionally paused work items.
- The CODEX provider circuit remained `OPEN` after its hold expired even though
  a later callback had been accepted. The success path did not reconcile the
  circuit state.
- `autopilot_operational_health_signal` classified a paused-only backlog as
  `critical` despite `open_actionable_count=0`.
- The mailbox pre-rotation workflow used `BRIDGE_WORKER_DATABASE_URL`, whose
  branch did not contain the `autopilot` schema. The already-used production
  callback boundary is the least-privilege replacement after granting only
  `EXECUTE` on `mailbox_rotation_readiness()`.

## Decision

- Close and reset the CODEX provider circuit on every durable transition to
  `CALLBACK_ACCEPTED`, and backfill recovery from the latest accepted callback.
- Report a paused-only planner backlog as `warning`; retain `critical` for
  actionable open work.
- Run mailbox pre-rotation through `AUTOPILOT_CALLBACK_DATABASE_URL` with the
  single additional read-only function privilege.

## Assurance evidence

- Local contract/regression suite: 36 tests passed.
- Ephemeral Neon branch: migration applied successfully; SQL contract returned
  3 cases and 0 failures.
- Trigger probe: an accepted callback changed the provider circuit from `OPEN`
  to `CLOSED`, cleared both failure counters/timestamps, and recorded success.
- Restore probe: before the trigger probe, the rollback removed the migration,
  trigger and callback privilege and restored the prior provider state.

Assurance level is `I2`: PostgreSQL/Neon independently enforced the schema,
constraints, privilege boundary, trigger transition, and rollback behavior.

## Rollback

Run `database/rollbacks/0356_autopilot_provider_health_recovery.sql`. It fails
closed if a new accepted callback occurred after the backup, because restoring
the stale provider state would discard newer recovery evidence.

## Remaining risk

The 19 paused work items remain a deliberate backlog requiring separate
evidence-based reconciliation. This change does not resume, cancel, or rewrite
them.
