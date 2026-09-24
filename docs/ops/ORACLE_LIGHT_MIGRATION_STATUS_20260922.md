# Autopilot migration checkpoint — 2026-09-22 13:30 UTC

## Verified deployed state

- Allocated OCI home-region volumes: 47 GB boot + 50 GB data = 97 GB;
  unallocated reserve 103 GB against the 200 GB allowance.
- Baseline boot backup AVAILABLE; one older FAULTY backup remains untouched.
  System-image restore has not been drilled.
- Data disk attached, initialized as ext4, persistently mounted at
  `/srv/autopilot-data` by UUID. Independent host verification passed.
- PostgreSQL 18.6 ARM64 runs from a pinned existing image on the data disk.
  Systemd requires the mount; Docker autonomous restart is disabled.
- Listener is strictly `127.0.0.1:55432`; TLS certificate/hostname and data
  checksums verified. TCP authentication is SCRAM; plaintext is rejected.
- Limits: 2 GiB RAM, one CPU, 128 PIDs, three 10 MB log files.
- Administrative credentials and private CA stay in root-private host files.
  No password or private key is stored in this repository.
- Local disk/inode monitor runs every 15 minutes. External alerts and automated
  backup verification are not yet deployed.

## Evidence

| Operation | PR | Live workflow | Job |
| --- | --- | --- | --- |
| Data-volume creation | 1797 | 35730618446 | 106755055310 |
| Attachment | 1798 | 35732387572 | 106761048444 |
| Persistent mount | 1799 | 35732945441 | 106762902909 |
| Empty PostgreSQL staging | 1800 | 35733817294 | 106765873717 |

PR1785 adds explicit PostgreSQL target validation to application code; defaults
remain Neon. Its merge does not deploy or switch any running service.

## Current data authority and remaining gates

Production Light, shadow and observer were independently verified still using
Neon. Queue/task-board authority remains Neon until coordinated cutover.
GitHub role callbacks and paused reconciliation also reference Neon credentials;
these consumers must move together with workers, not independently.

The original 08:23 UTC production and 08:28 UTC shadow snapshots are copied to
Light. Only these two branches have confirmed dumps. The Neon inventory contained
65 branches including 24 archived branches; do not delete them as supposedly
already exported.

`oracle_light_candidate_restore.py` prepares a new NOLOGIN candidate from pinned
old production snapshots and compares table content, functions and permissions.
This is rehearsal, not fresh production synchronization or acceptance.

Before cutover: complete consumer inventory, fresh consistent synchronization,
role/behavior canaries, off-VM backup and an actual restore drill, rollback fence,
then coordinated writer/callback switch and end-to-end verification.
School knowledge and its Neon data are outside the migration scope.

No new paid service, public database listener, firewall opening, Neon deletion,
or production database cutover has been performed at this checkpoint.
