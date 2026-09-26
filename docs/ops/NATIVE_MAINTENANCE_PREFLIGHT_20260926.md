# Native maintenance preflight, 2026-09-26

ASSURED preparation; baseline main e64f96378bcdcd94b6a7fa434a50a07268987d27.
The cross-commit table-write fence is merged in PR #1972. Privileged catalog
writer exclusion remains incomplete; this preflight does not authorize grants.

## Current observations

At approximately 04:38 UTC, a read-only Neon transaction showed only its own
owner connection and two idle runtime connections: Light runtime HOLD and the
worker reconciliation diagnostic. Neither idle connection had an open
transaction. Provider administration roles remain a trust boundary. A session
snapshot is not a reservation and cannot exclude later owner connections.

Oracle Light was reachable. systemd reported production Light, shadow and
online observer active. The production process was PID 191002, invocation
f8bbad7aff6e439eb82bf8eb47c29b66, release
5eb0e1bb2c2932bd8d02ff187b9cf24f6bc09c7c. These observations do not themselves
prove live HOLD, source integrity or credential/database binding.

RDC runs as uid 1001 and `sudo -n id -u` was refused by Linux NoNewPrivileges.
No service sandbox, command policy, account or host configuration was changed.
The existing reviewed workflow `oracle-light-active-hold-attest.yml` provides
an independent authorized SSH path as ubuntu with sudo for the read-only
attestation. It already runs on main changes to its audit source. No private
key is exported to RDC or scratch; no new execution channel is installed.

## Attestation correction

The old database audit checked role, database name, read-only mode and empty
queue, but not the actual Neon branch behind the endpoint. Strengthen the
existing child to require verify-full TLS with GSS disabled, exact actual host
and port, and all three server pg_settings identities with expected context,
configuration-file source, matching reset value and no pending restart. A
binding failure stops before reading task_status. Credentials and raw database
errors remain absent from the output. The worker's DSN and process are unchanged;
the audit connection uses stricter transport verification.

Unit tests execute the actual child using a synthetic driver and reject wrong
tags, tag provenance, missing/extra tags and routing host. These do not replace
the post-merge real SSH/TLS/server attestation. A live failure must remain a
failure, without falling back to weaker TLS or modifying worker credentials.

## Maintenance work remaining

Seventeen main workflows reference NEON_DATABASE_URL in the earlier complete
inventory. The list includes readers and backups as well as mutations; it must
not be interpreted as seventeen privileged writers. Known role hardening and
schema migration paths share oracle-instance-workload-mutation. Recovery
registry writes, temporary-branch routing and Neon control-plane API paths
require separate closure. Historical branch copies and queued runs also need
checking; a main-only inventory cannot exclude them. Root-capable host workflows
must be considered separately from direct database secrets.

Before grants: obtain successful live HOLD/binding evidence, finish writer
coverage and coordinate the actual maintenance window, then use the reviewed
manifest engine and table fence with fresh checks. Do not turn a point-in-time
empty owner-session list into a claim of exclusion.

Rollback of this code-only correction is a revert; it has no production data or
service mutation to undo. PR checks and the post-merge attestation run carry the
exact commit and outcome evidence.
