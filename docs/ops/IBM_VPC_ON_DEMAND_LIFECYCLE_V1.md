# IBM VPC on-demand lifecycle v1 (implementation plan)

## Intended operating model

The permanently running Light Oracle Autopilot remains the dispatcher. It admits useful,
bounded heavy tasks from the durable queue. An admitted task requests the existing IBM
VPC instance; no new VM is created. IBM starts only for admitted work. Results and
receipts are persisted off-host before an idle stop is considered.

This follows the Oracle policy sequence in `ops/oracle_compute_policy.json`: admit
evidence-bound work, start the exact existing compute only when required, run bounded
work, persist results, prove every workload family idle, and stop after a grace period.

## Decision contract

`ops/ibm_vpc_lifecycle.py` is the first implementation slice. It consumes one
timestamped observation and emits exactly one intent:

- `START`: at least one already-admitted heavy task exists and the exact VSI is stopped.
- `WAIT_FOR_READY`: IBM is transitioning; work remains queued with its lease protected.
- `KEEP_RUNNING`: work/lease exists or the idle grace has not elapsed.
- `STOP`: all configured sources prove empty, the VM is running, and the full idle grace elapsed.
- `HOLD`: any required source, freshness check, capacity signal, or state is unknown/invalid.
- `WAIT_THEN_RECONCILE`: a stop is already in progress while new work has appeared; reconcile actual
  IBM state before considering another action.

Default idle grace is 10 minutes and must remain configurable. The controller must reset
`idle_since_epoch` as soon as any work, lease, spool item, or maintenance lease appears.

## Required production observation sources

Before enabling lifecycle actions, the Light Oracle controller must build a single
consistent observation from:

1. durable queue: admitted pending and running heavy tasks, with family and task IDs;
2. task leases and controller/lifecycle lock;
3. IBM host worker and systemd service health;
4. all IBM local inbox/running spools and active subprocesses;
5. durable result/evidence upload completion;
6. disk headroom, using the platform-specific floor and fresh telemetry;
7. exact IBM VPC instance ID, name, region and provider lifecycle status.

Queue, lease, worker, and storage sources must be complete and fresh. Missing telemetry in these sources is `HOLD`, never idle. Fresh disk headroom is required before admitting/starting workload; a verified low-disk reading alone does not prevent stopping a fully idle VM.
Queue leases must be extended while IBM boots. A new job cancels a pending stop.
Only one lifecycle writer may send start/stop calls at a time.

## Deployment gates

1. Reconcile the production queue schema and the existing IBM worker/service inventory;
   do not infer task eligibility from CPU usage or GitHub workflow activity.
2. Integrate the decision contract with the Light Oracle resident controller and IBM's
   durable dispatch/claim path; keep mutations disabled in this first PR.
3. Run shadow decisions against live queue and host observations; compare them with
   operator-visible task state.
4. Test one admitted bounded job end-to-end, including start, readiness, lease renewal,
   result persistence, and recovery after controller restart.
5. Test safe idle with every source empty, then verify a new task racing the idle grace
   cancels stop. Reconcile ambiguous API outcomes by reading state; never blindly retry
   an action POST.
6. Enable automatic start first. Enable automatic stop only after a measured clean
   idle proof and verified result durability.
7. Retain an emergency keep-running switch, bounded action rate, audit receipts, and
   rollback to read-only observation.

## Current boundary

This first slice is a pure, tested decision core. It is not wired to the production
queue, does not call the IBM API, and does not change the VM. The existing
`.github/workflows/ibm-vpc-power-probe.yml` remains read-only.
