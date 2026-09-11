# Autopilot Project Planner V1

Status: implementation candidate

Date: 2026-09-11

Change: `AUTOPILOT_CONTINUE_PROJECT_V1`

## Purpose

One blocked role task must not stop the school project. The planner separates
the durable project backlog from the execution queue, retains the blocker, and
continues with the next dependency-eligible item.

## State and ownership

- `autopilot.project_work_item` is the durable, owner-registered backlog.
- `autopilot.task` remains the execution queue; Neon never invents work.
- `autopilot.project_work_task` records audit, repair, and verification lineage.
- `autopilot.project_planner_state` records an explicit planner decision even
  when nothing is eligible.
- The migration owner registers work. The restricted resident runtime can only
  claim a planner probe, submit its public GitHub observation, or record a
  bounded probe failure through `SECURITY DEFINER` RPCs.

## Algorithm

1. If any role audit, repair, or verification is nonterminal, wait. This keeps
   role execution sequential and prevents repair races.
2. Select a `READY` dependency-eligible item by priority. If none exists,
   consider a previously `BLOCKED` item whose observation delay expired.
3. Fetch the target PR through a credential-free, bounded GitHub `GET` and
   validate repository, PR identity, state, and the 40-character head SHA.
4. For an open new head, materialize exactly one `READ_ONLY` role task bound to
   that head. Task keys and dispatch epochs are deterministic per generation.
5. A technical `BLOCKED` result enters the existing bounded path: one `REPAIR`,
   then one exact-head `VERIFY`. Owner/account/payment/canon blockers never get
   a guessed repair.
6. If the lane still fails, retain it as `BLOCKED`, delay its next head probe,
   and select the next independent `READY` item.
7. Never redispatch an unchanged blocked head. A changed head reactivates that
   lane automatically.
8. A dependent item becomes eligible only after its prerequisite is `DONE`.

## Explicit idle outcomes

- `WAITING_FOR_ACTIVE_ROLE_TASK`: a role lane is still running.
- `IDLE_NO_REGISTERED_WORK`: the backlog has not been populated.
- `IDLE_NO_ELIGIBLE_TASK`: work exists but is blocked, delayed, or waiting on a
  dependency.
- `PROJECT_DONE`: every registered item is `DONE` or intentionally `PAUSED`.

No idle outcome disables the resident service or its webhook executor.

## Safety and recovery

- no model call, merge, deployment, secret access, infrastructure change, or
  paid action is available to the planner;
- all target work is bound to the current public head before dispatch;
- probe leases are fenced and expire;
- runtime roles cannot read or write planner tables directly;
- migration `0324` has an empty-catalog rollback; worker code also tolerates an
  older database by treating an absent planner RPC as a rolling-deploy state;
- reverting the worker stops new planning without affecting retained tasks.

## Verification evidence

The PostgreSQL integration test covers technical failure, single repair,
serialization, owner blocker retention, independent continuation, dependency
release, unchanged-head suppression, changed-head reactivation, and runtime
least privilege. The Python tests cover public head validation, materialization,
retry classification, and migration/worker rolling compatibility.
