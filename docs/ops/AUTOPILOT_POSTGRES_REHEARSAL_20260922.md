# Autopilot PostgreSQL migration preparation — 2026-09-22

Status: STRUCTURAL_RESTORE_ONLY. No production cutover.
School knowledge remains on Neon. Paid resources are not authorized.

## Changes
Autopilot connection entrypoints support explicit AUTOPILOT_DB_BACKEND=postgresql.
Default is neon; no deployed environment or school database validator is changed.
PostgreSQL requires AUTOPILOT_PG_HOST, AUTOPILOT_PG_DATABASE,
AUTOPILOT_PG_PORT (default 5432), and a dedicated principal.
Worker requires AUTOPILOT_EXPECTED_DB_USER; callback pins autopilot_callback_login;
reconcile pins bridge_school_worker_principal.
DSNs require sslmode=verify-full and channel_binding=require.
Alternate libpq routing parameters and duplicate query parameters are rejected.
Configure a trusted CA using sslrootcert or the deployment's trusted libpq CA file.
A production endpoint, certificate, role grants and network policy are NOT provisioned by this patch.

## Evidence
OCI audit run 35704884400 succeeded after PRs #1783 and #1784.
Root-compartment scope: one 47 GB boot volume, no block volumes; estimated
153 GB remaining within the combined 200 GB allowance. This is not a
tenancy-wide inventory or evidence that extra volumes have been allocated.
Light has 2 OCPU / 12 GB. No boot-volume backup or backup policy was found.
Existing $250 budget alerts are not a zero-spend hard limit.

PostgreSQL 18.6 arm64 rehearsal container on Light:
autopilot-db-rehearsal-20260922, network none, no published ports,
memory 1 GB, CPU 0.5. Local trust authentication is restricted to this
isolated rehearsal and is NOT a production configuration.

Production schemas autopilot and autopilot_reconcile restored into
autopilot_rehearsal with public.schema_migration and the dependent
public.autopilot_operational_health_signal view. Verified 64 autopilot
table/materialized relations, 93 functions (81 SECURITY DEFINER), four
reconcile SECURITY DEFINER functions, and readable health view.
Public school/person tables were absent from target.

Shadow branch restored separately into autopilot_shadow_rehearsal.
Its 14-table older schema is not merged into production.
Both pg_restore operations completed with --single-transaction and
--exit-on-error. Extensions pgcrypto and btree_gist were installed first.

Source exports were read-only, direct TLS connections using PostgreSQL 18
pg_dump. Private exports are held under mode-700 migration directory on
Light; files are mode 600. No secrets or database payloads are in this PR.
Production autopilot dump: 1304783 bytes,
SHA256 d9137d49e3584607055acc35acd65235a69b03c373174da03fe7537a1b158bd5.
Shadow autopilot dump: 71996994 bytes,
SHA256 347eccdddc432c49fd1d32f75deeb9aa5edf58ea61c4aed587344a54ca97d9b3.

## Limits and remaining gates
- Dumps/restores used --no-owner --no-privileges: ownership, role membership,
  ACLs and SECURITY DEFINER behavior are NOT yet validated. Target objects
  are owned by rehearsal postgres; never connect a production worker here.
- Support objects and schemas were exported in separate snapshots.
  Final cutover requires writer fencing and a consistent snapshot.
- External/dynamic SQL dependencies and the live mapping of production,
  shadow, callbacks and reconciliation still need verification.
- No off-host backup or restore proof from an independent backup destination.
- No production TLS endpoint, persistent service or final network rules.
- No source writers stopped; no target workers enabled; Neon is unchanged.
- RDC rejected privileged sudo command with "Command not allowed".
  Current account cannot access /opt/bridge-school/school-autopilot.
  Do not bypass that restriction using Docker mounts or another privilege path.

## Resume order
1. Obtain an explicitly permitted, narrowly scoped administration path for
   runtime inspection and later deployment; do not publish secrets.
2. Establish live database/consumer mapping and audit dependency/role closure.
3. Restore with matching restricted ownership and grants; prove least privilege.
4. Provision persistent PostgreSQL, TLS and restricted connectivity on Light
   within verified free capacity; establish off-host backups and restore proof.
5. Run isolated application canaries without external side effects.
6. Complete required independent assurance under repository governance.
7. Fence all source writers, take consistent final export, restore and compare.
8. Switch all autopilot consumers together, verify health and delivery, observe.
9. Retain Neon rollback data; avoid dual writers and account for target writes
   before any rollback. School knowledge stays on Neon.

## Validation
150 tests passed:
test_autopilot_database_target.py,
test_oracle_autopilot_shadow.py,
test_oracle_autopilot_github_role_callback.py,
test_oracle_autopilot_github_codex_publication.py,
test_autopilot_reconcile_db.py.
These are connection-contract/unit tests, not production migration acceptance.
