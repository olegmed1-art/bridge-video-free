# Native restore: reuse authenticated read connections

## Evidence and scope

On 2026-09-27, source `6476acbb25d05c51df72e66bb4252fae9946277c`
completed prepare run `36341168983`. Restore `36341738178` refused with
`RPC_TIMEOUT` in `host_exchange`: 103,104 ms launcher binding time, fixed
80-second RPC budget. Its runner profile reported 50,410 ms waiting for host
messages and 21,228 ms serving store RPCs. These overlapping observations do
not identify the precise host operation or establish a full duration estimate.

Independent post-failure reads observed registry active (updated 18:46:46 UTC),
DB BEFORE, and full HOLD. No external enable was performed. The failed restore
remains unaccepted: no retry, head/unit adoption, or journal repair. The owner
window expired at 18:58:38 UTC.

Code inspection found that source and workflow-state reads still used a new
urllib HTTPS connection on every call, although the stage already owned an
authenticated, bounded PersistentAPI with reusable GET connections. Restore
reconciliation repeats those reads around its distinct barriers.

## Change and safety boundaries

The stage explicitly lends its same-token PersistentAPI to source and exact
workflow-ID GETs. Each observation still sends a fresh GET in its original
position; there is no response cache or skipped guard. The host entrypoint
retains connection ownership and closes the original four lanes. The read-only
rehearsal opts in to the same transport wiring.

The exact method/path allowlist and token/type checks precede routing. Transport
errors remain redacted. A stale connection poisons the shared API and cannot
fall back to urllib or retry. PUTs and paginated inventory retain their existing
transport. Plan membership, observed workflow identity, source, run/job,
Agreement, HOLD, backend drain, checkpoint/CAS and single-use claims remain.
No deadline, SQL permission, service setting, workflow contract or production
activation changes.

Parallelizing the five workflow status reads was considered and rejected during
I2: sampling in_progress before and queued after a queued-to-running transition
could miss a run caught by the original order. That proposed code was discarded;
the existing sequential status order is unchanged.

## Validation and remaining gate

Controlled sockets exercise the real stdlib HTTPS/HTTP response parser. Six
source/workflow GETs obtain six distinct fresh responses, in order, through two
connections; failures have no retry or fallback. Tests also cover wrong token
or transport, invalid paths, original PUT/inventory routing, source/workflow
drift before a write, and connection cleanup. The simulated production runtime
still exercises prepare/execute/restore, lost unit acknowledgements, stale
heads and DB drift using explicit fake external authorities.

This removes repeated connection setup but is not proof that full restore fits
80 seconds. Exact-tree CI and independent I2 are required before merge. Any
future operational experiment needs fresh source-bound assets and a separately
accepted request in a new owner window; old failed requests stay quarantined.

Rollback: revert this patch to restore the previous GET transport; retain all
operation evidence and journals. Reverting cannot authorize an old request.
