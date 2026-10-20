# Read-only retained-input and current-state reconciliation

This observation checks the original retained issuer inputs using their accepted
inventory digest, the original baseline digest, and the existing strict local
journal validator. It then captures the current legacy process, loaded service
configuration, protected files and hardening. The current witness is validated
in memory as an `OBSERVE_ONLY` anchor with a maximum age of thirty seconds.

The anchor does not prove equality with a historical HOLD fingerprint. Missing
historical fingerprint inputs cannot be reconstructed from current constants.
The original baseline, receipts and incident remain unchanged. Neither an
`OBSERVED_CURRENT` result nor a witness digest authorizes retirement, admission,
replay, a new task, or a service transition.

## Accepted inputs and execution

The existing owner-only read-only observation workflow selects the exact
`observe-current-reconciliation-reference` action. Its canonical reference pins
the current source, controller package, retained Native package, four capture
helpers, installed legacy bundle, original inventory and baseline, policy and
index. A finite Agreement binds every input and the exact read-only operation.
Its clock is checked before any retained-record read and again at exit.

The new branch receives only the existing host SSH credential and short-lived
GitHub run token. It receives no database credential or driver. The isolated root
bootstrap verifies all five blob hashes before imports, extracts only reviewed
helpers to a private temporary directory, and authenticates the actual owner
workflow run. The callback holds the existing observation locks, checks current
main and original local journals, takes the current capture, and rechecks all
guards before emitting a result. Temporary helper files are removed at exit;
no witness or anchor is installed as durable authority.

## Result and refusal

Public output is limited to a strict result schema of at most 4096 bytes:
original inventory/baseline digests, current witness/anchor digests, observation
verification booleans and false authority flags. It contains no process,
environment, file contents, incident records, work identifiers or exception
text. The outer runner binds both original digests to the accepted reference
and rejects return-code promotion or extra fields.

Any failed or stale guard returns `UNKNOWN` with a fixed phase category and
false verification flags. A pre-import failure returns only the fixed minimal
refusal object. Neither outcome permits retrying an old mutating operation.
Changing source or consuming a one-shot observation requires a fresh reference
and independently checked action; no automatic rerun is configured.

## Acceptance and recovery

Portable tests exercise real inventory, Agreement, capture and anchor algorithms
with synthetic ports. Linux tests additionally exercise root metadata and real
Snapshot/flock/local refusal. These are distinct from host acceptance. A real
read-only host run must use the merged source and fresh accepted inputs; CI
success alone cannot claim live systemd integration.

The deployment adds a selectable read-only observer. It does not install a
daemon, stop legacy, activate Native or change admission. Recovery consists of
ceasing dispatches or reverting the observer change; existing services and
historical records need no restoration from this observer.
