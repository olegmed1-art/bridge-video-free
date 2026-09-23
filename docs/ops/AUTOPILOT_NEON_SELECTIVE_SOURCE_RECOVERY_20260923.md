# Autopilot Neon source recovery: production and shadow

Status: **BLOCKED; no export or cutover has run.** Read-only primary-source
inventory was observed on 2026-09-23 19:04 UTC. This is an `ASSURED` recovery
gate, not a claim that the Oracle destination or an off-host restore exists.

| Branch | Neon ID | Direct host | Database | PostgreSQL | App relations | Size |
| --- | --- | --- | --- | ---: | ---: | ---: |
| production | `br-wispy-lab-b1rq54of` | `ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech` | `neondb` | 18 | 64 | 66 MB |
| shadow | `br-still-tooth-b1ilkfcj` | `ep-floral-field-b1pjs2of.c-5.eu-central-1.aws.neon.tech` | `neondb` | 18 | 14 | 688 MB |

Both are mixed databases. `public.schema_migration` has 137 entries in
production and 75 in shadow. Only 69 production and 13 shadow entries match
reviewed `03xx_autopilot_*` migration SQL files; unrelated school migrations
must never move to the two Oracle Autopilot databases. The shadow branch has a
different schema generation and cannot reuse production's archive or ledger.
Twelve of its 13 Autopilot migration receipts have historical NULL checksums;
production has valid SHA-256 for all 69. Preserve those twelve NULLs byte for
byte on restore and flag `migration_checksum_complete=false`. An independently
computed hash of reviewed migration SQL is separate provenance and must not
be written into the historic source ledger.
The 69 production checksum values were compared read-only with SHA-256 of
their corresponding `database/migrations/*.sql` at this checkout: zero missing
files or mismatches. The selector verifies each non-NULL value again within
the held source snapshot and fails on source drift.
`ops/autopilot_selective_ledger.py` provides a fail-closed read-only selector;
the caller must use its connection **within the same repeatable-read snapshot**
used by PostgreSQL 18 `pg_dump --snapshot`. The destination must have exactly
those selected rows, with identical key, checksum and timestamp; copying the
whole shared ledger, even in a rehearsal, is prohibited. Live SQL identified
zero incoming/outgoing foreign keys on the shared ledger on both branches;
production has four Autopilot functions referencing it. Review these
dependencies again against the final source snapshot.
Read-only relational dependency check on 2026-09-23 found zero cross-schema
incoming and outgoing foreign keys for Autopilot tables and zero Autopilot RLS
policies on both branches; it also found 194 other public relations in
production and 193 in shadow, confirming the whole database cannot be dumped
into the scoped Oracle target. Function bodies, dynamic SQL, ACLs and views
still require the final snapshot and independent review.

## Required protected export and restore

1. Validate both separate direct hostnames and branch IDs using owner DSNs
   inside a protected trusted `main` runner. Reject pooled endpoints, a
   credential for the other branch, mismatched `neondb`, PostgreSQL major,
   extension set, migration generation, and unexpected app schema objects.
   Do not print or persist either DSN. The current workflow inventory has no
   confirmed **shadow branch DSN**; a new scoped secret and actual connection
   proof are prerequisites.
2. Before exporting a final recovery point, stop new task admissions and
   coordinate a protected pause with the #1769 executor. The held mailbox 1703
   task must complete with a linked terminal receipt or be proven safely
   paused; never re-register/resend it. Independently establish the full
   writer/client list and fence every production and shadow source writer,
   including owner, old/manual clients and GitHub jobs, at database level.
   Recheck active sessions and the route under the root-owned lock. The
   current route is `backend=neon, epoch=0`; 0355 remains in force.
3. For each branch, hold one read-only repeatable-read transaction open,
   export a PostgreSQL MVCC snapshot, and run **all** scoped `pg_dump` commands
   with that snapshot: only `autopilot`, `autopilot_reconcile` and reviewed
   public support definitions. Exclude the *data* of the shared migration
   ledger from `pg_dump`; export only the exact selected rows from the held
   transaction. Verify no school rows or unrelated schema appear in `pg_restore
   --list`. Capture separate source IDs, schema generation, start/end times,
   source LSN, dump hashes, ledger key/checksum digest and extension versions.
   A PostgreSQL snapshot alone does not freeze sequence values: take a final
   sequence value/is_called capture **after** proving the writer fence.
4. Save both encrypted dumps and their manifests in a private existing
   object-storage bucket **outside Light Oracle**, with create-only keys,
   checksum/size metadata, limited readers and no public/preauthenticated
   links. A GitHub artifact or an OCI object that cannot be downloaded after
   Light Oracle fails does not satisfy the recovery gate. Live OCI bucket,
   credentials, permissions, capacity, retention, and independent recovery
   reader remain unverified.
5. Independently download each versioned object by its exact key, verify
   SHA-256, restore into *two new isolated PostgreSQL 18 databases* with
   outgoing callbacks disabled, and compare relation content digests,
   functions, schema/database/table/sequence/default ACLs, RLS/policies,
   role attributes and memberships, extension versions and post-fence
   sequence values. Test both operational principals without enabling external
   delivery. Preserve durable trusted workflow IDs, both object identities,
   both manifest digests, exact comparison results and independent I2 review.

## Promotion and reverse path

No source fence, final export or route switch may run while #1769 remains at
HOLD/READY or the inventory of writers is incomplete. PR #1855 is a *future*
production-only, post-route backup and does not supply the above recovery point
or shadow coverage. A separate scheduled backup of both Oracle databases,
bounded retention, missed-run/failure alert and independent restore drill must
be proven before declaring Neon retirement ready. Before the first Oracle
write, reverse by stopping Oracle writers and restoring the old route and
source grants under lock. After any Oracle write, fence both sides and
reconcile Oracle-only records and sequence state onto Neon; prohibit a reverse
switch until a separate checked reconciliation proves no lost or duplicated
task dispatch, ACK, terminal answer or other data. Never permit two
authoritative writers.

Current blockers: separate protected shadow DSN; validated OCI off-host
bucket/write/read access; protected export of each source; target databases;
verified independent restore for both branches; full writer fence; controlled
runtime pause and genuine terminal receipt. This document authorizes none of
the mutating steps.
