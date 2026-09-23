# Autopilot Oracle source fence gate — 2026-09-23

Status: PREPARATION. No source fence, production migration or cutover executed.

## Current read-only observations

At about 11:19 UTC, an unprivileged session on `autopilot-lite-vnic` read:

- route state: `backend=neon`, `database=autopilot`, `epoch=0`;
- production Light, shadow, online observer and local PostgreSQL services: active;
- `/srv/autopilot-data`: 49 GiB filesystem, 217 MiB used;
- systemd timers: `bridge-light-health.timer` exists; no Autopilot PostgreSQL
  backup timer appears in `systemctl list-timers --all`.

The Neon connector did not resolve an active project in this session (`INVALID_ARGUMENT`
for project and branch discovery). No live source database query was performed.
No privileged path, Docker mount or credential file was used.

## Read-only application-principal check

`ops/autopilot_source_fence_probe.py` requires the pinned Neon owner DSN with
hostname-verified TLS. It executes only SELECT statements in a read-only
transaction and checks effective schema, function, table and sequence grants for
the production Light worker, callback and school worker principals in
`autopilot` and `autopilot_reconcile`. It prints no DSN or database error body.

`application_principals_fenced=true` means only those three principals lack
effective privileges on those two schemas. The probe always reports
`full_cutover_ready=false`: owner/legacy credentials, active sessions, other
databases, backup recovery and target consumers are outside this proof.

The PostgreSQL 18 isolated integration test exercises actual GRANT/REVOKE
including PUBLIC function grants. A live probe may be run through an authorized
owner credential path **after** coordinated source fencing, without moving the
secret into a PR or revealing it in a log.

## Remaining cutover conditions

1. Reconcile all current consumers, including migration 0372 once deployed,
   legacy/manual workflows and the three resident services.
2. Establish regularly scheduled off-host production backups with bounded
   retention and an independent restore drill. The September 22 candidate archive
   is fixed, old and explicitly `production_backup=false`.
3. Pause admissions; drain live workers, callbacks and route leases. Capture
   source ACLs and apply the reviewed source-schema fence. Validate effective
   privileges and owner/legacy writer exclusion independently.
4. Take a fresh consistent export under the fence, restore a new Oracle
   production database, compare data/definition/permission manifests and test
   each login role without side effects.
5. Run independent assurance, switch consumers together, verify a genuine
   delivery receipt, observe, and preserve the Neon rollback boundary. After an
   Oracle write, a simple route flip back is unsafe without write reconciliation.
