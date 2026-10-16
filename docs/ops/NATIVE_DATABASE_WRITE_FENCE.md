# Native permission database write fence

2026-09-26; ASSURED preparation following PR #1971. No production invocation,
grant, server command, native task, or activation is part of this change.

## Problem and scope

A route lease can disappear while a database transaction remains alive. The
permission engine's transaction locks also end at COMMIT, before a fresh-session
inspection. `database/native_cli_write_fence.py` supplies an optional separate
owner transaction holding SHARE locks over every ordinary table in `autopilot`.
It remains open across apply/revoke COMMIT and the fenced fresh-session inspect.

The approved manifest's complete relation OID/name set is checked before and after
acquisition and at every assertion. Partitioned, inherited, and foreign relations
are refused. Locks cover callback delivery proofs, terminal receipts, evidence,
and all other ordinary autopilot tables, not only the engine's original seven.
PostgreSQL enforces exclusion of conflicting table writes; a route client does
not need to cooperate. The holder verifies target identity using the existing
Neon binding contract, backend/transaction identity and actual granted pg_locks.
It has 5-second statement, 1-second acquisition, and 60-second idle transaction
timeouts. A caller must supply a fresh, idle connection exclusively to this context.

When supplied a fence, the engine also acquires its own SHARE locks on the same
table set, retaining transaction protection if the separate holder disappears.
Cooperating engine calls serialize on transaction advisory key (1971, 6).
Calls without a fence retain the original SHARE ROW EXCLUSIVE table locks.
The external MaintenanceGuard is still required on both paths; its base
implementation still refuses all changes.

## Failure semantics

- A writer already holding conflicting locks prevents fence acquisition.
- A writer queued after the fence can prevent the engine acquiring compatible
  SHARE locks because of PostgreSQL wait-queue ordering. The engine refuses
  within its lock timeout, with no committed grant delta. This is a safety
  property, not a guarantee of progress under queued traffic. Stop ingress and
  reconcile/drain writers before attempting a production transition.
- Holder loss before commit causes the engine transaction to roll back when
  detected; its own locks protect that transaction until it ends.
- Holder loss detected after acknowledged COMMIT raises
  `COMMITTED_BUT_WRITE_FENCE_POSTCHECK_FAILED`. The grant/revoke has committed;
  do not retry or infer rollback. Re-establish maintenance and reconcile the
  durable manifest through a fresh connection.
- A lost COMMIT response remains an unknown outcome, handled by the existing
  manifest reconciliation protocol. These tests do not inject a network loss.
- `fence.inspect()` checks the holder before and after a separate-session
  snapshot. Its return is evidence while the context remains held; it does not
  guarantee the holder will survive indefinitely after returning.

## Explicit remaining boundaries

Table locks do not exclude function ACL changes, roles/membership changes,
new relations created by a privileged actor, sequence increments, writes in
other schemas, remote side effects, or provider control-plane changes. They do
not prove a previously committed callback was harmless. Catalog drift checks
are detection, not catalog-writer exclusion. A real external maintenance
implementation, writer reconciliation, HOLD/deployment checks and bound live
invocation remain required before production. No default guard is weakened.

## Verification and recovery

The existing disposable PostgreSQL 18 permission-engine rehearsal invokes the new
fixture. It exercises actual engine GRANT and REVOKE commits, checks conflicting
locks on every protected table, attempts actual configuration DML across those
commits, and inspects the result from a fresh session while the fence remains.
An ordered queued-writer test asserts bounded refusal and unchanged ACL state.
Closing the real holder connection immediately before and after commit verifies
different rollback/committed outcomes. The maintenance guard remains a CI stub;
this is not a complete production maintenance integration test.

Expected markers: `NATIVE_WRITE_FENCE_CROSS_COMMIT_PASS`,
`NATIVE_WRITE_FENCE_QUEUED_WRITER_REFUSAL_PASS`,
`NATIVE_WRITE_FENCE_LOSS_OUTCOMES_PASS`. CI must supply actual pass evidence.
The parent fixture restores original ACLs and removes its temporary roles.
Code rollback is reverting this change; no production data rollback is needed.
