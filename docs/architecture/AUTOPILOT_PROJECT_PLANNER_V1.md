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

1. Admit at most six role tasks concurrently: five normal slots and one P0-only
   reserve. Per-target exact-head fencing still prevents repair races.
2. Select a `READY` dependency-eligible item by priority. If none exists,
   consider a previously `BLOCKED` item whose observation delay expired.
3. Fetch the target PR through the pinned broker's bounded read-only GitHub App
   token and validate repository, PR identity, state, and the 40-character head
   SHA. The installation token never leaves the broker; the resident receives
   only the head, open/closed state, and pinned release provenance.
4. For an open new head, materialize exactly one `READ_ONLY` role task bound to
   that head. Task keys and dispatch epochs are deterministic per generation.
5. A technical `BLOCKED` result enters the existing bounded path: one `REPAIR`,
   then one exact-head `VERIFY`. Owner/account/payment/canon blockers never get
   a guessed repair.
6. If the lane still fails, retain it as `BLOCKED`, delay its next head probe,
   and select the next independent `READY` item.
7. Never redispatch an unchanged blocked head. A changed head reactivates that
   lane automatically.
8. A dependent item is durably stored as `WAITING_DEPENDENCY`; after its
   prerequisite reaches `DONE`, a database trigger atomically promotes it to
   `READY` and emits a best-effort wake notification. Recovery polling reads
   the durable state, so a missed notification cannot lose the wake-up.

## Explicit idle outcomes

- `WAITING_FOR_ACTIVE_ROLE_TASK`: a role lane is still running.
- `IDLE_NO_REGISTERED_WORK`: the backlog has not been populated.
- `WAITING_FOR_DEPENDENCY`: registered work is waiting on a prerequisite.
- `WAITING_FOR_RETRY_WINDOW`: blocked work is in its bounded cooldown.
- `WAITING_FOR_PROBE_LEASE`: another fenced probe still owns the candidate.
- `IDLE_NO_ELIGIBLE_TASK`: reserved for an otherwise unclassified invariant
  gap; ordinary dependency and cooldown waits no longer collapse into it.
- `PROJECT_DONE`: every registered item is `DONE` or intentionally `PAUSED`.

No idle outcome disables the resident service or its webhook executor.

## Delivery proof

GitHub is only the discovery transport. Creating or reusing a draft dispatch
pull request records `PUBLISHED`; it can never record `SENT`. A dispatch reaches
`SENT` only after an owner-authenticated `@codex` command on the exact target PR
receives an `eyes` reaction from the pinned Codex Connector bot identity. The
callback independently rechecks that the target PR is open on `main` and still
has the bound head SHA. A dispatch PR or GitHub comment by itself is never
delivery.

Codex Cloud creates one isolated task/chat per accepted command. Its terminal
comment must come from the same pinned GitHub App, repeat the exact dispatch,
epoch, role, fingerprint, target PR and current head, and have a prior retained
ACK. The callback is idempotent and writes one terminal receipt and retained
evidence before the next durable item is woken. Chat registry rows remain only
for v1/v2 compatibility and dashboards; they are not a v3 admission gate.

## Safety and recovery

- no model call, merge, deployment, infrastructure change, or paid action is
  available to the planner; its only GitHub read goes through the attested
  broker and cannot exhaust the 60-request anonymous API quota;
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
