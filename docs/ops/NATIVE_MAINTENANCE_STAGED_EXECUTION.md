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

## Runtime coordination implementation

`native_maintenance_coordination` provides the readers for the staged runtime:

- `Agreement` checks a separately accepted record for the exact operation spec,
  evidence reference, direct-owner/host/workflow-admin/rerun coverage and an
  explicit window no longer than 30 minutes. Wall-clock rollback, monotonic
  expiry or changed bytes latch refusal. The code cannot supply the director's
  missing future commitment by hashing a record it has just invented.
- `WorkflowDrain` requires zero runs in each of GitHub's five nonterminal states
  for every workflow in the accepted pause plan. It does not filter by branch,
  source or creation date, so historical reruns count. A nonzero, malformed or
  incomplete/paginated response refuses; no page traversal is needed to establish
  refusal after any unfinished run is found. API status definitions:
  https://docs.github.com/en/rest/actions/workflow-runs#list-workflow-runs-for-a-workflow
- `OwnedConnections` verifies the target of actual owned connection objects and
  registers their backend PID plus start time. It never accepts caller-provided
  PID exclusions or trusts application names. Foreign/invisible backends and
  prepared transactions refuse.
- `PriorSupervisors` reads independently accepted unique unit/invocation/cgroup
  identities and requires completion/empty cgroups. It never stops a process.
  A cross-run operator cannot omit the original preparation unit evidence.
- `Operator` binds those components to the exact scope, source and authenticated
  run. Any observation failure latches refusal. The executor still supplies the
  route fence, table locks and independent permission postcheck.

A live read-only observation found `pg_read_all_stats=true` for the maintenance
owner and one idle Light worker connection. The latter is deliberately allowed
only for the exact recipient `autopilot_light_worker_login`, at most one client
backend, idle with no transaction or backend xid/xmin, visible identity, unchanged
approved HOLD and freshly verified disabled native configuration, empty queue and
zero receipts. An active/transactional/second worker or any other foreign client
refuses. This is a scoped HOLD exception, not proof that idle connections cannot
write later; route/database fences and operator coordination remain necessary.
Stopping the existing service would invalidate its approved HOLD identity and is
not part of this implementation.

The disposable PG18 executor fixture now uses the actual owned-backend drain for
both execution modes, including a real foreign owner connection that must refuse.
It temporarily supplies the CI owner with statistics visibility and restores the
original role membership afterward. External operator agreement, GitHub and host
authorities remain explicitly simulated in that fixture. Production still needs
the reviewed runner to supply accepted agreement/prior-unit/head evidence and
execute the complete bounded rehearsal before granting rights.

## Consolidated admission observation (opt-in)

Assembly review after #2011 found recursive remote observations: for one planned
workflow and the permitted idle Light backend, a legacy HOLD assertion issues
about 64 GitHub GETs and five HOLD attestations. This is a call-graph count, not a
live measurement. It is not a viable basis for assuming the 60-second run and
30-second route windows will suffice.

`observed_admission=True` is explicit, staged-only, and binds
`admission_mode=observed_v1` into the immutable operation scope. It requires the
actual `Operator` and authenticated `RunBinding` on the same run. The concrete
`ObservedSessionWindow` replaces recursive authority reads with one no-effect
observation chain on **every** HOLD guard call:

1. Check local scope, journal locks, supervisor, agreement and fixed run deadline.
2. Observe the exact paused workflow states, all unfinished workflow runs, prior
   supervisors and registered/foreign database backends.
3. Observe the exact approved HOLD, and observe paused workflow states again to
   catch a re-enable during drain or HOLD inspection.
4. Obtain a fresh complete authenticated run/workflow/main/job observation, then
   recheck local agreement, journal, supervisor and deadline continuity.

The database drain retains its two HOLD checks for the idle worker exception.
There is no cached successful observation, lease renewal, inferred approval or
SQL dispatch in these read methods. The existing engine/session calls remain
unchanged, including checks before GRANT/REVOKE, before COMMIT and around fresh
outcome inspection. Route and database fence checks still follow the external
observation. OCI publication and workflow mutation guards keep their existing
ordering. Legacy callers retain their original guard path; a same-named method
on an arbitrary writer cannot opt into this composite.

Fault tests compose the actual coordinator/run/pause/HOLD classes over explicitly
simulated external infrastructure. They verify fresh reads on every call, a
re-enabled workflow during drain, cancellation/main drift after HOLD, agreement
expiry during final authentication, changed HOLD, foreign backends and subsequent
route/fence loss. One planned workflow now uses 12 GitHub GETs and three HOLD
attestations per guard call. These tests neither grant production authority nor
prove a real timing budget. Complete supervised no-write rehearsal remains a
production gate; the 60/30/100-second limits have not been increased.
