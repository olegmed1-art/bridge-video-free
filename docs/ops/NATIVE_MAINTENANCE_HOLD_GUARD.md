# Live HOLD continuity for native permission maintenance

2026-09-26. ASSURED implementation; production mutation remains unwired.

The existing read-only host audit now exposes `attest()` returning a private,
immutable HoldIdentity. It contains the Light process/invocation/release and a
fingerprint of the observed service and protected configuration. Full audit
checks still verify host, root files, live DSN equality, HOLD, actual read-only
Neon branch binding and empty queue. Route and broker-pin file bytes must also
remain unchanged across the child login. Leaf files open nonblocking, rejecting
FIFOs before a blocking read. CLI success output is unchanged and never emits
the identity or credential-derived fingerprint.

`HoldMaintenanceGuard` binds one exact production target and apply/revoke operation
to a caller-supplied approved HoldIdentity. It never approves its own first
observation. Every assertion checks the separate writer guard, runs a fresh full
audit, compares the identity, and checks the writer guard again. Any failed check
latches refusal for that instance, even if later observations look normal.

This guard implements the host side of the session's external-guard interface.
It requires an independently established critical-writer guard; supplying only
HOLD is refused. The future runner must bind its approved private identity to
the reviewed package and maintenance record. No constructor or dataclass proves
that operator coordination exists.

## Verification and limits

Tests cover target/operation binding, absent approvals, process/release/config
identity changes, writer loss on either side of the audit, latched failure,
parent audit drift, nonblocking FIFO refusal and unchanged public output. Parent
host/DB inputs are controlled unit fixtures; the existing main-push workflow
separately executes the real read-only host audit after merge.

Each observation is point-in-time. It does not exclude a transient change and
restoration between checks or atomically stop another host operator. That remains
part of the agreed critical-writer window. Audit calls are bounded individually;
the caller must account for their duration within the route/DB fence deadlines.
Repeated audits can cause safe refusal when the overall window expires.
The fingerprint binds observed configuration bytes and the release path, not
the entire release file tree. Broker pins are observed on disk; this change
does not add comparison of every broker pin with the process environment.

No native RPC, production ACL/config change, service restart or task launch is
added. Default session admission stays refusing without real external coordination.
Rollback is a code revert; no production state rollback is required.
