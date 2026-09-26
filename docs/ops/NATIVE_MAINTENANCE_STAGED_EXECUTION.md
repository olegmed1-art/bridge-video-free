# Staged native permission maintenance

2026-09-26 UTC. ASSURED preparation for #1946; no production activation.

The authenticated synthetic relay took 40.119 seconds of its 60-second run
lease. That result covers two checkpoint publications, not workflow exclusion,
database fencing, permission changes and postchecks. Putting all work into the
same lease is unproven. Increasing the timeout has not been authorized by this
change. The executor now offers an explicit staged mode while preserving the
original single-run mode and its existing journals.

## Durable ordering

| Stage | Required state | Durable result | External effect |
| --- | --- | --- | --- |
| `prepare()` | Original authenticated run, new staged scope | `PREPARED`, fully checkpointed journal pair | Pause only the reviewed workflow list |
| `execute_prepared()` | Different authenticated run, independently accepted current remote head, identical locked local pair | `SESSION_BOUND`, then `SESSION_INTENT/UNKNOWN`, each checkpointed before SQL | Exactly one apply or rollback attempt |
| `restore()` | Fresh independent DB/process reconciliation | `RESTORE_INTENT`, then `RESTORED` | Restore only workflow states owned by this operation |

One immutable scope binds source, target, manifest, plan, route, HOLD and origin
run. `execution_mode=staged_v1` prevents reinterpreting a legacy journal as a
staged authorization. Every stage obtains a new, nonrenewed authenticated run
binding and supervised host lifetime. This does not extend an expired lease.

`PREPARED` is not a permission permit. The execute stage rechecks current source,
HOLD, paused workflow states, scoped operator coordination and drain. Existing
route and database fences still surround the SQL operation and its independent
postcheck. The same unexpired direct-admin no-write commitment must cover the
whole operation, including the interval between stages; none is inferred from
the workflow pause or a historical statement that nobody else was working.

`accept_resume()` reads the independently accepted current remote head, verifies
the private archive and requires exact equality with the locked local pair. It
cannot publish a local suffix or accept an inferred head. Subsequent checkpoint
publication still uses the existing append-only/CAS protocol. A stale acceptance,
local-only PREPARED suffix or competing remote advance refuses.

An ambiguous PREPARED acknowledgement fails the current preparation instance.
It is not a permanent ban on this operation: preparation cannot execute SQL.
If the remote PREPARED head was actually committed, a different run may proceed
only after independent acceptance of that current head, exact pair comparison,
fresh paused-workflow/HOLD observations and real operator/drain reconciliation,
including completion of the previous host process and its database backends.
Accepting a hash copied from the failed local process is not independent
acceptance. A digest alone and the local `prepared` state confer no authority.
The production assembly must obtain that acceptance outside the failed run.
If the remote head still precedes PREPARED, the local suffix cannot authorize
dispatch. An ambiguous SESSION_BOUND/SESSION_INTENT is different: it consumes
the opportunity, so a recovered bound/intended session is never dispatched.

Once `SESSION_BOUND` is appended, no entrypoint can rebind or resume the session,
even if the process stopped before `SESSION_INTENT`. Once intent exists, an
unknown return never implies rollback. Separate reconciliation/restore remains
available; neither success nor failure automatically enables workflows.

## Evidence and remaining assembly

Contract tests exercise the complete three-run ordering with real locked journals
and checkpoint serialization over an explicitly simulated remote store. Fault
cases include missing/stale acceptance, local-only preparation, a committed
PREPARED with lost acknowledgement (refused until fresh drain reconciliation),
death after binding, lost intent acknowledgement and lost SQL return. The disposable PG18
fixture exercises both execution modes, actual apply/revoke, route exclusion,
independent postcheck and recovery after a real commit with a lost return.

These tests do not claim a live staged timing result or real operator authority.
The production workflow/runner, actual job/backend drain observations, explicit
operator commitment and separately accepted checkpoint transfer between runs
still need assembly and one complete no-write rehearsal before the grant.
Preparation itself must also fit its own bounded run. A timeout leaves the
operation for reconciliation; it never silently renews the lease.

The registry observation at main `49f814565adcb6f81feb193283610cc0ac5413f2`
refused authentication in run `36270503687`. Keep
`recovery-registry-population.yml` in the scoped pause/drain plan. The separately
verified maintenance owner connection passed with native disabled and zero Light
EXECUTE grants. Do not change the registry credential to obtain an exemption.

Rollback for this code change is a revert before any staged production scope is
created. Once such a scope exists, retain this reader and its journal format for
reconciliation; reverting code is not restoration of workflow or database state.
