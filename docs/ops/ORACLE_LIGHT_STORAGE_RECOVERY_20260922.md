# Light storage and recovery decision — 2026-09-22

Status: design accepted for implementation under owner's standing zero-new-cost authorization.
This document distinguishes intended protections from deployed protections. No database cutover is authorized by this document alone.

## Baseline

Live host inspection: root filesystem 45 GiB, 7.4 GiB used, 37 GiB available;
one 46.6 GiB disk attached. Production, shadow and observer services are running.
PostgreSQL rehearsal container is stopped; this is not a production database.
OCI allocated GB and backup counts must be taken from the complete API inventory,
not inferred from filesystem GiB or old heavy-server deletion.

## Capacity decision

Official source: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
Reviewed 2026-09-22: 200 GB combined boot/block volumes in home region;
five combined boot/block backup objects independently of allocated live volume space.
Object Storage allowance differs by account state: Always Free only 20 GB combined;
paid/trial documentation lists 10 GB Standard plus separate other tiers.
Use Standard storage only for hot recovery; check actual account usage before writing.

| Purpose | Initial allocation |
| --- | --- |
| Existing system disk | Keep actual API size, approximately 50 GB |
| Dedicated PostgreSQL and autopilot data disk | 50 GB, not yet provisioned |
| Unallocated restore capacity | At least 100 GB, subject to actual inventory |

Do not expand root to 200 GB. Keep enough quota to restore both disks beside originals.
With a 50 GB root and 50 GB data disk, originals plus both restored disks use 200 GB.
If any other volume consumes capacity, revise allocation before creation.
No cross-region replication, higher-performance add-ons, or paid backup products.

Suggested data-disk operational budgets, not partitions: PostgreSQL 20 GB,
bounded WAL/staging 10 GB, application files 10 GB, headroom 10 GB.
WAL must never be deleted arbitrarily to enforce this budget; alert and investigate.

## Backup design (pending deployment and restore acceptance)

- Boot: weekly and before significant system changes, retain two known-good copies.
- Data disk: daily, retain two copies; disk snapshots are crash-consistent and do
  not replace PostgreSQL-aware backups or a tested recovery chain.
- Fifth backup slot is transient creation capacity. Validate new backup AVAILABLE
  before rotating the oldest managed copy; never delete unrelated backups.
- Do not use a standard retention policy that can exceed five combined copies.
- PostgreSQL: portable daily dump including necessary roles and extensions;
  plan PostgreSQL-aware base backups plus WAL archive for a target recovery point
  within 15 minutes. This target is not met by daily dumps alone.
- Off-VM backup repository: proposed private Object Storage Standard bucket,
  maximum 8 GiB including all versions and retained WAL, pending inventory.
  Suggested retention: seven daily and four weekly logical backups if they fit.
  Retention must preserve at least one verified recoverable chain; alert rather
  than deleting required WAL or silently exceeding the free boundary.
- Encrypt and restrict backup access; never place backup payloads, role passwords,
  environment files, or database data in the public GitHub repository/logs.
- Code and non-secret configuration remain reproducible from GitHub; secret
  recovery requires scoped secret storage and must be verified separately.

## Recovery sequence

1. Stop/fence affected writers and preserve the original disks for diagnosis.
2. Application-only fault: restore the reviewed deployment/configuration; validate health.
3. Database logical fault: restore into an isolated database, replay required WAL
   to before the incident, validate roles, counts, health and tenant boundaries;
   switch only after checks, retaining the old database for rollback.
4. System-disk failure: restore boot backup into quota reserve and recover the VM;
   attach the existing healthy data disk, or restore its backup if necessary.
5. Whole-VM loss: recreate from recorded infrastructure/configuration; restore
   system/data, secrets, networking and access; verify actual application behavior.
6. Record actual restore duration and recovered timestamp. Run isolated restore
   drills monthly and after material backup/schema changes.

Target recovery time is 2–4 hours, not a guarantee. Free A1 capacity shortages can
delay replacement compute; this storage reserve does not reserve compute capacity.
Same-account backups do not protect against total Oracle account loss. An encrypted
copy with another existing provider is a separate pending decision requiring actual
capacity/access review; do not promise off-provider protection until tested.

## Deployment in this change

- Complete paginated read-only home-region volume/backup/bucket inventory via GHA.
- Fixed local capacity monitor every 15 minutes, warning at 75% blocks or inodes.
- Local state: /var/lib/bridge-light-health/status.json; journal contains only metrics.
- Explicitly reports future data mount and backup verification NOT_CONFIGURED.
- No external notification delivery yet; local warnings alone are not an alert service.
- No disk creation, filesystem formatting, DB migration or backup upload in this change.

Validation: syntax checks, independent I2 review, CI, live audit, live monitor run
and enabled/active timer. Deployment acceptance requires actual workflow evidence.

Rollback: disable and stop bridge-light-health.timer, then remove only the two
bridge-light-health systemd units and /usr/local/lib/bridge-light-health/check.py;
daemon-reload. Preserve local status for diagnostics. Existing services are untouched.
School knowledge database stays on Neon.
