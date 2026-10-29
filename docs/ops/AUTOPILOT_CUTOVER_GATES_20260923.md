# Autopilot production and shadow cutover gates — 2026-09-23

Status: BLOCKED. The last observed route is `backend=neon, epoch=0`. This document authorizes no route change, source fence, deletion or new spend. Both PR #1852 and PR #1855 are draft. School knowledge remains on Neon.

## Fresh Neon source inventory (read-only, 2026-09-23 18:01–18:02 UTC)

Neon project `misty-poetry-18012774` returned two distinct current branches:

| Contour | Branch ID | Direct endpoint | Autopilot relations | Functions | Sequences | Database size |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| Production | `br-wispy-lab-b1rq54of` | `ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech` | 64 | 93 | 3 | 66 MB |
| Shadow | `br-still-tooth-b1ilkfcj` | `ep-floral-field-b1pjs2of.c-5.eu-central-1.aws.neon.tech` | 14 | 26 | 3 | 688 MB |

Production has four `autopilot_reconcile` functions and the public operational health view; shadow has neither. Both have the shared `public.schema_migration` ledger. Production has 69 Autopilot-keyed rows and 68 other rows; shadow has 13 Autopilot-keyed rows and 62 other rows (read-only inventory on 2026-09-23). A whole-table `pg_dump --table=public.schema_migration`, as used by the earlier candidate workflow, would also copy school/workflow migration records. Reject it. Export only an explicitly reviewed list of required Autopilot ledger keys from each branch's held snapshot, with key/checksum manifest and restored equality; confirm dependencies before selecting keys. Each branch has `pgcrypto` and `btree_gist`; the required extension subset must be proven by restore. Production has 81 SECURITY DEFINER Autopilot functions, shadow 23. The source databases also contain school and other schemas: production `public` has 161 relations (~37 MB), shadow `public` has 160 (~37 MB). The production `autopilot` schema is ~6.8 MB and shadow `autopilot` is ~631 MB. A full Neon database dump is out of scope and must be rejected; export only reviewed Autopilot schemas and specific dependent public objects/ledger rows, from one held snapshot per branch. The
read-only connector queries identified branches and schema generations, not
source credentials, a consistent export, target data, or a recovery point.
`ops/oracle_autopilot_dual_source_preflight.py` is a protected-runtime
identity check for two separate owner DSNs. It fails on a branch or generation
mismatch and emits no secrets. It has not been run with production credentials.

## Client closure

Pin current main and inspect live DSNs (without printing credentials) before each production mutation. The known resident services are `school-autopilot-production-light`, `school-autopilot-shadow`, and `school-autopilot-online-observer`. The continuous GitHub workflow definitions include role callback, codex event callback, paused reconcile, and mailbox pre-rotation. The shared database health workflow must split its school checks from its Autopilot check. Legacy branch/manual workflows, source owner and old credentials must be fenced at the database, even if route leases appear healthy. Compare this list against the live process table, GitHub runs and the pinned inventory; any unclassified writer blocks cutover.

## Recovery before routing

1. Dispatcher coordinates with executor 1: record mailbox 1703 task ID, dispatch count, acknowledgement and terminal state. Pause admissions and drain leases only after the task has one terminal receipt or a proven safe pause. Never re-register or resend the task during database migration.
2. Administrator supplies the scoped protected runtime and OCI access. Verify target capacity, mounted data volume, PostgreSQL 18 image/extensions and TLS. Create separate `autopilot` and `autopilot_shadow` databases with separate least-privilege roles. The 22 September candidate and rehearsal shadow are not current destinations.
3. Export each source branch separately from direct TLS connections with PostgreSQL 18 tools. Preserve source identity, snapshot time, database definitions, role/ACL/RLS and sequence state. Restrict source writes under the reviewed schema fence and prove effective denial for *every* writer including owner/legacy paths. Fresh final exports must occur after this fence, without mixing production and shadow schema generations.
4. Restore into isolated target databases, compare per-relation row/content hashes, function definitions, ACLs, ownership and sequences, and test each application principal. Check external delivery is disabled during rehearsals. Independently review discrepancies; a count alone is insufficient.
5. Keep the source recovery point outside Light Oracle. Download its off-host object, verify SHA-256 and restore into a separate PostgreSQL instance. Record a signed/trusted workflow run, object version/metadata, checksums, manifest results and target identity. Do this for both production and shadow.
6. Configure daily scheduled immutable/off-host backups, bounded retention based on verified free capacity (the existing storage plan suggests seven daily and four weekly logical backups within an 8 GiB cap; measure both database sizes and preserve a verified recovery chain), missed-run and failure alerts with acknowledged delivery, and independent restore access. PR #1855 currently implements only a *post-route production* backup mechanism and cannot satisfy the preceding source recovery gate or shadow backup gate. Do not enable a schedule before its route and source checks pass.

## Route promotion and rollback

A fresh primary-source audit must show exactly one authoritative writer, matching code and route lease contracts on every service and GitHub client, no outstanding mailbox dispatch, verified backups of both databases, and an independently reviewed restore. Switch the route and clients together under the root-owned lock; enable one writer, observe a genuine task dispatch, acknowledgement and linked terminal answer, then enable the rest. Inspect live DSNs and the route epoch, not just the route file.

Before any Oracle write, rollback restores captured Neon ACLs and the Neon route under the same lock after stopping Oracle clients. After an Oracle write, fence both sides, export/reconcile Oracle-only writes and verify them on Neon before considering a reverse switch. Never operate dual authoritative writers. Keep Neon and off-host objects until recovery acceptance.

Evidence still missing: fresh live DSN/branch mapping, source and target production/shadow manifests, source fence proof, current off-host objects, independent restore, schedule/retention/alert delivery, and Oracle end-to-end task receipt.
