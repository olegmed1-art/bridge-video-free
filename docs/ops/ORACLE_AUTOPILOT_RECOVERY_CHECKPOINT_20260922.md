# Oracle autopilot recovery checkpoint — 2026-09-22

Status: migration preparation; production queue and taskboard still use Neon.
School knowledge remains on Neon. This checkpoint does not authorize deleting
Neon data, historical branches, backup objects, or the old rollback configuration.

## Verified live state

- Light: A1, 2 OCPU / 12 GB, Frankfurt, running.
- Boot 47 GB + separate data volume 50 GB = 97 GB allocated; 103 GB remains
  unallocated within the 200 GB combined boot/block allowance.
- Data filesystem is mounted by UUID at `/srv/autopilot-data`; PostgreSQL's
  systemd service requires that mount. Root uses 7.4 GiB; data uses 91 MiB at
  the 14:24 UTC check. These are observed sizes, not retention guarantees.
- PostgreSQL 18.6 is pinned to the reviewed image, bounded to 2 GiB/1 CPU,
  listens only on 127.0.0.1:55432, and requires TLS plus SCRAM.
- Baseline full boot backup is AVAILABLE. A full system-image restoration has
  not been rehearsed. An older FAULTY boot backup is preserved for diagnosis.
- Health timer checks disk/inode usage every 15 minutes with a 75% warning.
  External notification delivery is not yet configured.

## Database recovery proof

The prepared candidate is the 08:23 UTC production snapshot, not a current
production replacement. It contains both autopilot schemas, the migration ledger,
the health view, source-matched permissions, and 97 function definitions.
Application roles remain NOLOGIN.

PR1805 merge `ac6c8adfbe6920d48127261cf3c202cd6e489125`:

- Workflow35739001008, backup job106783579049: private Object Storage upload and
  download PASS, with all bucket/access/tiering/size guards retained.
- Archive: 846351 bytes; SHA256
  `c7876e55891650edcceb9b3e13cea451d9b9e7a5a3f472cb9737a2c41e09850f`.
- Downloaded archive restored into `autopilot_restore_drill_v2_20260922`.
- All 68 data/definition/effective-permission manifest entries matched.
- Total account object bytes upper bound at upload: 636723348; no objects deleted.
- No automatic recurring production backup is active yet. The archive proves
  the mechanism, not a production recovery-point objective.

## Transport proof

PR1808 installs an unprivileged `autopilot-db-tunnel` account. PR1809 adjusts the
negative Unix-socket test to OpenSSH 9.6's exact policy-refusal response.
Workflow35740871198, job106790113661: PASS for CA/hostname-verified TLS,
other TCP destination denial, reverse/Unix forwarding denial, command denial,
and unchanged Ubuntu access. No PostgreSQL public port or firewall opening.

The same automation private key still authenticates to Ubuntu for previously
authorized administration. The new account narrows selected operations; it
does not isolate compromise of that private key. A separate future key requires
a separate GitHub secret and is not claimed to exist.

## Cutover prerequisites and rollback boundary

The routing lease protocol is prepared separately from consumer activation.
Its initial state is Neon. Root-owned route state and a permanent-inode lock
allow cutover to wait for all participating GitHub writers to finish.
GitHub tokens remain on the runner. Shared school database secrets stay unchanged.

The workflow inventory records 27 relevant definitions at its pinned source
commit. Four continuous workflow definitions and three systemd services need
coordinated handling. Legacy manual/branch workflows remain a reason to enforce
a database-side source fence; route state is not the authoritative fence.

A further shared reader was found through the called Python code:
`database-health-monitor.yml` runs `database/runtime_health_preflight.py`, which
also reads `public.autopilot_operational_health_signal`. Its general school checks
must stay on Neon while the autopilot health query follows Oracle. This shared
health consumer is not yet covered by the eight prepared wrapper entrypoints;
consumer activation must wait for that split and the dedicated health-role canary.

Fresh source catalog checks found no functions outside the autopilot schemas
referencing them, no nonowner grant options, and 57 nonowner grants across five
grant-bearing roles. All autopilot objects are owned by neondb_owner.

`ops/oracle_light_source_fence_rehearsal.sql` passed on the isolated rehearsal
database inside a rolled-back transaction: three effective application principals
lost schema/function/table/sequence privileges; public relation ACLs and global
role memberships remained identical. This did not fence or mutate Neon.

Before actual cutover: activate and prove leases for all live consumers, pause
new jobs, stop services, drain legacy runs, capture source ACLs, apply and verify
the autopilot-schema-only source fence, take a fresh consistent snapshot, restore
to a new production database, verify manifests/permissions and actual connection
canaries, then release jobs and services onto Oracle together.

Before any Oracle production write, restoring saved Neon ACLs and routing is a
valid rollback. After Oracle receives writes, fence Oracle and reconcile those
writes before considering a reverse migration; a simple route flip is unsafe.

Remaining work: consumer activation, fresh production/shadow migration,
scheduled verified backups and retention, recovery runbook/restore drill,
certificate-expiry monitoring, and independent verification of archive coverage.
