# Autopilot production database backup v1

Scope: the PostgreSQL 18 `autopilot` database on Light Oracle after a verified
route switch. The 22 September candidate archive is excluded. This is an
`ASSURED` recovery capability; source backup and restored data must be observed
before any migration or Neon retirement gate can accept it.

## Run contract

`python -m ops.oracle_autopilot_production_backup /private/receipt.json` runs as
root on Light Oracle (`autopilot-lite-vnic`) from a trusted `main` workflow,
with Docker, PostgreSQL 18
client binaries, psycopg and OCI SDK. Supply `AUTOPILOT_BACKUP_DSN` through
scoped secrets; `OCI_CONFIG_FILE`, `OCI_PROFILE`, `OCI_TENANCY` and an existing
private `AUTOPILOT_BACKUP_BUCKET`; a digest-pinned
`AUTOPILOT_RESTORE_IMAGE=postgres@sha256:...`. Its service account needs read
access to all database objects plus TEMP permission but no table write permissions,
and OCI permission to PUT, HEAD and GET objects
in this bucket. Never print the DSN or bundle it into the receipt.
The DSN must target `127.0.0.1:55432/autopilot`. Before a full database dump, the source check rejects application relations outside the `autopilot` and `autopilot_reconcile` schemas and the two reviewed public support relations; extension-owned relations are excluded. This also rejects an accidentally combined shadow database. Any legitimate new relation needs explicit review and an updated manifest contract before backup activation. A root-owned live route lock
is held from before snapshot export until after restore verification; the route
must remain `postgresql`, `epoch >= 1` throughout. The receipt is issued after
the lock is released and its final state checked.

The operation exports a repeatable-read MVCC snapshot, calculates a source
data, function and ACL manifest within that transaction, imports that snapshot
into `pg_dump`, writes a private object with a create-only condition, retrieves
the object, checks bytes and SHA256, restores the retrieved dump in an isolated
PostgreSQL 18 container, and independently recalculates the manifest. Function
owners and sequence definitions/owners are compared separately and hashed in
the receipt. Sequence *values* can advance outside an MVCC snapshot; final
cutover must compare them after fencing writes. Receipt format is
`PRODUCTION_AUTOPILOT_BACKUP_V1`; `PASS` requires all checks.
Failure exits nonzero and must trigger an operations alert. No partial result
authorizes cutover. The container is temporary, has no network, and never
mounts the production data directory.

## Activation gates still open

- Run a fresh on-host proof after the production route is PostgreSQL at an
  epoch greater than zero. The utility checks hostname, pinned route and
  loopback DSN. Separately verify the Neon owner/legacy/manual write fence,
  active sessions and absence of a second writer: route metadata alone does
  not prove this.
- Put this command on a scheduled trusted `main` workflow (recommended daily)
  and wire job failure or missed run to an acknowledged alert. Confirm at least
  one real run and its independent restore. This repository change does not
  install a timer or declare a successful production backup.
- Configure bounded Object Storage lifecycle retention (the existing storage
  plan suggests seven daily and four weekly copies within an 8 GiB cap),
  object access controls and recovery credentials in OCI; measure production
  and shadow sizes and verify lifecycle and budget before activating. Do not delete historical
  backups to meet a budget. Confirm a separate backup reader can download the
  object when Light Oracle is unavailable.
- Cutover gate must independently verify the trusted successful workflow run
  and current head, route epoch, receipt freshness, OCI object metadata and a
  fresh download; neither a pasted receipt nor a self-declared `PASS` suffices.
- If restore fails on a role outside the explicitly enumerated Autopilot roles,
  add a reviewed, least-privilege role inventory. Never silently drop ACLs.

No production or billing changes were made by this implementation. Rollback is
to disable the schedule; existing immutable objects remain available for
manual recovery. A real restore drill and operations alert remain deployment
blockers.
