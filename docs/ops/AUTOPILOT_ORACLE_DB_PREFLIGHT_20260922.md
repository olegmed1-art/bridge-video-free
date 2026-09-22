# Autopilot PostgreSQL migration preflight — 2026-09-22

Status: PREPARATION ONLY; restore and cutover NOT performed.
Governance: STANDARD for this read-only audit/parser repair; ASSURED (I2 minimum) for production migration.
Baseline: main 370e487a5eed80b88daffc4258a9d90e216952c1.
Owner direction: move autopilot only; school knowledge remains on Neon; no new paid services without explicit price approval.

## Observed evidence

Neon SQL catalog reads, 2026-09-22:
- production: PostgreSQL 18.6; autopilot has 64 table/materialized-view relations, 6800 kB including indexes/TOAST.
- autopilot-shadow-final-20260830: autopilot has 14 such relations, 631 MB. Different schema generations: do not merge them.
- production autopilot has 93 functions, 81 SECURITY DEFINER; autopilot_reconcile has 4 functions, all SECURITY DEFINER.
- Explicit cross-schema function references include public.digest and public.schema_migration; autopilot_reconcile calls autopilot functions.
- public.autopilot_operational_health_signal depends on autopilot tables/functions.
- Installed production extensions: plpgsql, pgcrypto 1.4, btree_gist 1.8, pg_stat_statements 1.12. Determine actual required subset through restore tests.
- Catalog and text scans are not proof that all dynamic SQL/application dependencies have been found.

Light host read-only inspection: root filesystem 45 GiB, 38 GiB free; approximately 10 GiB available memory in prior same-session sample. PostgreSQL client and OCI CLI absent from PATH. Runtime source directory is denied to the RDC ubuntu account. Do not bypass this with Docker host mounts or alternative privilege paths.

Current source still validates Neon hostname and neondb in worker.py and github_role_callback.py. Reconcile, observer, publication and GitHub callbacks have separate connection settings. Identify every writer and its branch before any switch.

OCI audit run 35698438529 / job 106650523087 failed in capacity collection with JSONDecodeError after successful CLI setup. Empty successful OCI list output is a possible cause; parser fix treats empty list output as no items while still rejecting malformed nonempty JSON. No successful capacity result yet. Root-compartment totals alone cannot prove tenancy-wide free quota. Inventory every relevant compartment and availability domain; confirm home region and Always Free eligibility.

## Ordered execution and gates

1. Obtain successful OCI disk inventory; include detached boot/block volumes and all relevant compartments. Verify total eligible allocation <=200 GB. Do not allocate a speculative 100 GB volume. Existing free host space is sufficient in size for an isolated rehearsal; a separate data volume can be sized after growth/retention planning and quota verification.
2. Map each live service and GitHub connection setting to production or shadow without logging credentials. Use an approved scoped deployment/control route for protected runtime files. Current RDC permission is insufficient.
3. Pin supported PostgreSQL 18 binaries/image and extensions. Use separate target databases and roles for production and shadow. Default no external delivery from rehearsals; preserve TLS and least privilege. Review callback reachability from GitHub; never open unrestricted PostgreSQL access.
4. Build a selective export manifest: autopilot, autopilot_reconcile where present, required extensions, approved migration-ledger rows and operational health objects. Do not copy school tables. Preserve function owners, role membership, ACLs, RLS, sequences, triggers and SECURITY DEFINER search_path. Do not blindly import Neon-managed roles or password hashes.
5. Use pg_dump 18 via direct connections and restore into isolated target databases. Record source branch/version, snapshot time, checksums, row counts and sequence values. Validate queue transitions, advisory locks, LISTEN/NOTIFY, callback authorization, denial of unauthorized operations and school separation. Verify cross-schema dependencies against actual restored behavior.
6. Implement explicit configured PostgreSQL endpoints in DSN validators, keeping Neon defaults and TLS/channel binding requirements. Update all applicable workflows and clients together. Run independent I2 assurance before production approval gate.
7. Configure off-host encrypted backups with bounded retention and restore one independently. A backup on the same Light disk is insufficient. Verify total backup/storage cost remains within approved free limits.
8. For cutover: refresh main/runtime/schema evidence; stop ALL writers to the contour being moved; drain/fence callbacks; take final consistent dump; restore and validate; update connection settings atomically; enable one writer; run a harmless end-to-end canary, then remaining writers. Keep school connections unchanged.
9. Rollback before first Oracle write can return to the frozen Neon source. After new Oracle writes, stop writers and reconcile/export new data before any return; never activate both databases as authoritative writers. Retain source until recovery acceptance; no deletion in this task.

## Costs and remaining blockers

Target incremental Oracle infrastructure cost: $0 only after verified Always Free eligibility/quotas. No managed OCI PostgreSQL service provisioned. Existing Neon/AI charges remain until separately measured; moving shadow alone does not guarantee production compute savings.

Missing: successful full OCI inventory, live service-to-branch mapping, approved protected-runtime deployment access, trial restore, tested off-host recovery, endpoint portability implementation and independent assurance. These are hard cutover gates, not completed work.

This change modifies documentation and a read-only audit parser only. No DB data, credentials, production configuration, disks or service state changed. Revert the commit to undo these preparation changes.
