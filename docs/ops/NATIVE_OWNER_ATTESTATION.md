# Read-only owner connection attestation

2026-09-26. Recovery prerequisite, ASSURED; no production permission activation.

The general repository source preflight failed owner/callback/health authentication
in run 36242635568, while the worker principal passed. This does not establish the
value of an environment-scoped secret. The dedicated workflow uses the existing
`database-production` environment and an optional dedicated
`LIGHT_MAINTENANCE_DATABASE_URL`, falling back to its `NEON_DATABASE_URL`.
It does not rotate credentials, update secrets, or modify the running Light host.

Dispatch is owner-only, exact current main, with a fresh API check before and after
the database observation. The connection is reconstructed against the fixed direct
host with verified TLS and GSS disabled. The permission engine verifies actual
server provenance and session/role identity. A repeatable-read read-only transaction
checks dormant state, exact minimal ACL scope and absence of native privileges.
Timeouts bound each statement and the job. Only the snapshot digest and nonsecret
target/status are logged; no snapshot or credential is persisted or uploaded.

A passing observation is not an approved manifest, operator exclusion, a host
credential delivery proof, or a grant permit. Production remains held until the
separate runtime, off-VM intent durability, writer coordination and pilot admission
are complete. Rollback is source revert; there is no external mutation to undo.
