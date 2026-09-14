# Autopilot six-worker capacity — change record

- Date: 2026-09-14
- Change ID: `AUTOPILOT-0331-SIX-WORKERS`
- Governance mode: `ASSURED`
- Status: PostgreSQL CI passed on draft PR #1420; production promotion and live executor acceptance pending

## Purpose and scope

Replace the project-wide single-task gate with bounded parallel execution in
the single existing GitHub event-runtime conversation. The project chat
`СЛАВИК / AUTOPILOT` remains the control surface and the Dispatcher remains the
sole planner. The runtime may coordinate at most six transient workers, with
five slots available to normal work and the sixth reserved for P0 work. This
routing is necessary because a GitHub webhook automation runs in its durable
task conversation rather than in a project control chat.

The change affects repository Codex configuration, durable planner admission,
task-level capacity enforcement, role-to-chat routing, rollback, and SQL CI. It
does not merge code, mutate the production Canon or Neon database, create a
chat, spend money, or process real media.

## Decision and evidence

The selected limit is six spawned workers plus the primary coordinator. The
current OpenAI configuration reference states that
`agents.max_concurrent_threads_per_session` excludes the primary thread. The
repository therefore sets the value to `6`.

Confidence is high for admission correctness after independent review. The
implementation uses one transaction-scoped advisory fence, counts active tasks
and live probe reservations, atomically converts an exact probe lease into a
task, rejects over-capacity activation, and fails closed outside READ COMMITTED
isolation. Active role identity and priority are immutable.

Routing changes are fenced against `CLAIMED`, `PUBLISHED`, and `SENT`
publications. The exact pre-migration chat registry is retained for restoration;
rollback acquires the same admission and table locks before proving the system
idle.

## Verification

- I0 static checks: `git diff --check`, Bash syntax, TOML parse, and workflow
  YAML parse.
- Existing Python regression: 76 tests passed for Oracle shadow, GitHub role
  callback, and delivery-proof contracts.
- SQL regression `331_autopilot_six_worker_capacity.sql` covers five normal
  slots, one P0 slot, seventh-worker rejection, direct-task rejection, NULL
  lease rejection, active-priority mutation rejection, and routing.
- Concurrent regression
  `331_autopilot_six_worker_capacity_concurrency.sh` starts seven independent
  database clients and requires exactly six admitted tasks, exactly five normal
  tasks, no leaked reservation, and one waiting normal item.
- I2 independent Red Team review identified six concrete admission, routing,
  and rollback risks; all were corrected before commit.
- I3 GitHub Actions run
  [`34871003212`](https://github.com/olegmed1-art/bridge-video-free/actions/runs/34871003212)
  passed both PostgreSQL 18 jobs. The
  `current-main-baseline` job applied the full migration chain, ran the SQL
  regressions, completed the clean rollback/reapply cycle, and passed the
  seven-client concurrent admission test.

## Rollback and remaining risk

`database/rollbacks/0331_autopilot_six_worker_capacity.sql` is the defined
rollback. It refuses to run while active tasks, live probe leases, or in-flight
publications exist, restores serialized planner admission, preserves strict
lease validation, and restores the exact pre-migration chat registry snapshot.

Repository and database readiness do not by themselves prove six live ChatGPT
worker runs. Production acceptance still requires: reviewed promotion of
migration 0331, an in-place upgrade of the single existing event executor, and
an E2E canary with delivery proof and terminal receipts. Until those steps
pass, the capability must not be reported as live production.

Cost exposure is bounded by the six-worker ceiling, but parallel agents can use
more tokens than serialized execution. No new paid service or subscription is
introduced by this change.
