# Issue #627 — Oracle idle STOP guard

## Safety contract

The guard has exactly three states: `BUSY`, `IDLE`, `UNKNOWN`.

`STOP_ALLOWED := state == IDLE`.

`BUSY` and `UNKNOWN` always block STOP. Missing, stale, malformed, unexpected,
or contradictory telemetry is `UNKNOWN` and cannot be converted to zero work.

## Required telemetry families

1. `assistant_lab.job` — all `QUEUED`/`RUNNING` jobs.
2. `assistant_lab.control_command` — all `QUEUED`/`RUNNING` commands.
3. `assistant_lab.research_job` — every nonterminal stage (`QUEUED`, `ACCEPTED`, `RUNNING`, `CHECKPOINTED`, `VALIDATING`).
4. Research child jobs — independently counted while the parent is nonterminal.
5. Universal Video Neon queue — `PENDING_CANARY`, `QUEUED`, `LEASED` jobs.
6. Universal Video local spool — `inbox` and `running` payloads.
7. Universal Video resident service/status — service state plus fresh resident status when active.
8. BEN — active `BEN_COMPUTE` jobs.
9. Bulk — active jobs explicitly identified by the existing workload-family/source metadata; the umbrella job count remains authoritative if a producer lacks the tag.
10. Other allowed workloads — every remaining active Assistant Lab job.
11. Operator/maintenance lease — a live lease is BUSY; malformed or expired-but-present lease evidence is UNKNOWN; an observed absent lease is IDLE.

The Assistant Lab database snapshot uses a SECURITY DEFINER read-only RPC so the
worker does not receive direct table privileges. The database observation time
is returned with the counts. Universal Video Neon is queried read-only and is
never optional.

## Freshness and conflict rules

The evaluator requires a complete snapshot and per-family `observed_at` values.
The default maximum age is 120 seconds (bounded to at most 900 seconds). A whole
snapshot or any family older than the configured maximum is `UNKNOWN`.

Conflicting signals inside a family are `UNKNOWN`. A concrete example checked by
the collector is a resident status claiming no active job while the local
`running` spool positively contains work.

## STOP consumer boundary

`ops/oracle_stop_consumer.py` is intentionally not an OCI implementation and is
not connected to production. Its injectable `maybe_stop()` contract invokes a
STOP callable only after the evaluator returns proven `IDLE`; BUSY and UNKNOWN
raise `StopBlocked` before the callable is reached. The CLI is decision-only and
contains no power command.

This PR does not enable automatic STOP, restart or stop Oracle, change production
routing, mutate Neon/Drive, or merge itself.
