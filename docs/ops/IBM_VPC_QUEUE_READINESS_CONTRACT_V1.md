# IBM VPC queue readiness contract v1

Status: proposed in draft PR; the migration has not been applied to production.

## Purpose

The IBM VPC lifecycle controller needs a read-only signal that matches the Universal Video worker's actual claim rules. The existing \`video_queue.job_status\` intentionally omits \`next_attempt_at\` and lease fields, so raw \`QUEUED\`/\`LEASED\` totals cannot determine whether work can run now, is waiting for retry, or is still actively leased.

Migration \`0059_ibm_vpc_queue_readiness.sql\` proposes an aggregate-only view for the least-privilege \`bridge_school_reader\` role. It contains no job IDs, source IDs or names, lease owner, or lease token.

## Contract

The view returns one aggregate row per processing profile, algorithm revision, and batch state that exists in queue history. Consumers must filter against an explicitly configured, approved profile/revision pair. Unknown or unexpected pairs must HOLD; the view does not authorize a worker binary or algorithm revision.

| Field | Meaning | Lifecycle use |
| --- | --- | --- |
| \`observed_at\` | One statement timestamp for the snapshot | Reject stale observations |
| \`runnable_now_count\` | Due QUEUED jobs, or expired LEASED jobs with attempts below 3, only in QUEUED_CANARY/RUNNING batches | May trigger start / keep running |
| \`active_leases_count\` | LEASED jobs whose lease has not expired | Keep running |
| \`retry_waiting_count\`, \`next_retry_at\` | Retryable QUEUED jobs in claimable batches whose retry time is still in the future | Schedule a later recheck; does not require IBM to stay on |
| \`pending_canary_count\` | Jobs awaiting canary release | Never triggers start |
| \`expired_attempts_exhausted_count\` | Expired leases at attempt 3 or higher | Not runnable; audit/reconciliation signal |
| \`blocked_nonterminal_count\` | QUEUED/LEASED jobs in a non-claimable batch state, including CANARY_REVIEW | HOLD and reconcile; never treat as claimable |
| \`unknown_*_status_count\`, \`invalid_lease_shape_count\` | Unsupported status or lease shape | HOLD |

The eligibility predicate mirrors \`video_queue.claim_job\`: QUEUED jobs must be due; an expired lease is reclaimable only below the three-attempt limit; the batch must be QUEUED_CANARY or RUNNING. In particular, CANARY_REVIEW added by migration 0057 is not claimable until a separate Director release path changes the batch state.

## Required consumer behavior

1. Run the read query in a read-only transaction with a bounded timeout.
2. Validate snapshot freshness, expected profile/revision, and all unknown/inconsistent counters.
3. Treat query errors, missing configuration, stale observations, nonzero blocked/unknown/inconsistent counters, or unexpected status groups as HOLD.
4. Re-read immediately before any eventual power action and serialize controller actions. The view itself never authorizes an IBM API call.
5. Interpret future retries as scheduled work, not active work: Light must remain available to recheck at \`next_retry_at\`. The lifecycle controller must not leave IBM running only to wait for that timestamp.

## Scope and deployment boundary

This change proposes an additive view and a rollback-tested SQL contract test. It does not connect the view to a production controller, call IBM start/stop, claim queue jobs, or apply any production database DDL. The draft must remain unmerged until query access, controller mapping, snapshot freshness, Light availability, and end-to-end recovery behavior are independently verified.
