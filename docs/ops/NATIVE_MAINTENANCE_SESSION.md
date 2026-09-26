# Composed native permission maintenance session

2026-09-26. ASSURED implementation; external production coordination not supplied.

`database/native_cli_maintenance_session.py` composes the existing route fence,
separate database table fence, six-grant engine and fresh-session inspection.
The external guard is required before acquisition and at engine and inspection
boundaries. Omitting it uses the original refusing guard. The implementation
does not manufacture an operator agreement or treat a local boolean as one.

Resources remain owned through mutation COMMIT and inspection. The mutation
connection stays open while a distinct observer backend reads the durable
manifest state, proving the observer is not the mutation or holder connection.
The route/file identity, deadline, DB fence and external coordination are checked
before and after that inspection. Normal return requires the exact expected
AFTER (apply) or BEFORE (revoke) state.

If the mutation or postcheck fails, one fresh outcome classification is attempted
while resources remain held. BEFORE, AFTER and DRIFT retain their existing
manifest meanings. Failure to establish that protected observation is UNKNOWN.
The module never retries a mutation or automatically revokes grants. Recovery
uses the retained manifest and a newly established maintenance window.
An exception during resource teardown preserves the last established classification
and is separately marked `cleanup_failed`; confirmed AFTER is never called rollback.

This is not an atomic cross-system protocol. A process crash, signal, connection
loss or resource failure may prevent classification or cleanup; absence of a
returned result never means rollback. The caller must enforce bounded process
lifetime and preserve HOLD while reconciling an uncertain outcome.

## Disposable evidence

The root/PG18 rehearsal creates isolated recipient roles and a private manifest,
uses the real route fence/DB fence/permission engine, and supplies a clearly named
CIExternalGuard stub. It tests refusing defaults, apply and revoke across real
commits, a real routed client blocked during mutation, pre-COMMIT guard refusal,
loss of the return value after successful COMMIT, persistent loss of the external
guard, route lock or DB holder, and persistent fresh-observer failure. Loss must
return UNKNOWN while fresh recovery finds the six grants
still present; no blind retry/revoke is allowed. A teardown fault after successful
inspection must preserve AFTER with the cleanup-failure flag. Owned connections
and route resources are checked released. Cleanup restores the baseline.

The discarded-return test does not inject network loss during COMMIT. These tests
do not prove live GitHub group ownership, host HOLD or direct-owner coordination.
Expected marker: `NATIVE_MAINTENANCE_COMPOSED_SESSION_PASS`.

## Remaining production binding

The real external guard still needs pinned source/run/target, simultaneous GitHub
mutation-group exclusions, live HOLD/invocation checks and the agreed short window
covering relevant privileged actors outside those groups. The owner connection
factory must supply authorized, verified connections without exposing credentials.
The session does not deploy a loader, enable native admission or invoke a native RPC.

No production acquisition, grant, deployment or restart occurs in this change.
Code rollback is a revert; fixture data is disposable.
