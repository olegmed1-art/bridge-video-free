# Conditional OCI journal store and synthetic recovery probe

2026-09-26. ASSURED / I2 before merge. Operational journal export is not activated.

The synchronous checkpoint protocol now has an actual OCI Object Storage adapter.
It addresses only the existing `bridge-light-autopilot-backups` private Standard
bucket in the pinned tenancy. Every mutating call uses an explicit no-retry
strategy and a fresh runtime mutation guard. There are no delete/bucket creation,
public-link, retention-policy or permission-changing methods.

Archives are content-addressed and created with `if_none_match='*'`; an existing
archive must match byte-for-byte. The latest pointer advances with its exact ETag
using `if_match`. Initial creation first writes a create-only registration copy
of the initial head. A registration without a head, or a head without registration,
refuses admission. Lost registration/head replies preserve objects and require
independent reconciliation. No automated repair or blind retry is supplied.
Both registration and head being deleted by an administrator cannot be proven
absent historically; provider administration remains a coordinated trust boundary.

Reads are bounded and streamed, check size and ETag, close response streams and
verify archive hashes. The store refuses public bucket access, any preauthenticated
request, replication or lifecycle rules, changed tag/tenancy/tier, and versioning.
Before writing it performs an exact paginated root-compartment object inventory,
refusing duplicates, loops, more than 10,000 objects or a total at/above the
existing 8 GiB budget. This is not proof of all-region/account billing or exclusion
of independent writers. The caller must establish compartment closure and actual
operator exclusion; the synthetic runner first checks the existing complete
home-region inventory, one compartment and at most 100 GiB allocated volumes.

The implementation uses the documented OCI Python SDK
[conditional PutObject parameters](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/object_storage/client/oci.object_storage.ObjectStorageClient.html#oci.object_storage.ObjectStorageClient.put_object).
The runtime SDK is pinned to 2.186.0; per-operation retries are explicitly disabled.

## Bounded live proof

`native-maintenance-checkpoint-probe.yml` is owner-only manual dispatch on exact
reviewed current main. It shares `oracle-light-backup-mutation`, uses the existing
environment credentials, and creates a fixed synthetic BOUND/PLAN pair tied to
the unique Actions run/attempt. It checkpoints that pair and a synthetic UNKNOWN
intent, verifies the provider rejects a deliberately incorrect ETag with 412,
downloads the accepted pair and restores it into a private recovery-only folder.

The probe uses four small objects (two archives, registration, mutable head),
preserves them and never reads a production journal. The marker explicitly says
`production_journal_backup=false` and `production_mutations=false`. OCI synthetic
objects are the only remote changes. The probe does not pause workflows, connect
to the production DB, transfer an owner credential, release HOLD or launch work.

The adapter is included in the fixed source bundle. Unit tests use an explicit
fake OCI client and prove conditional headers/no retries, lost registration/head
replies, deleted-head detection, privacy/lifecycle/budget refusal, CAS conflict,
bounded reads and the complete fixed probe. A successful live probe still does
not establish production journal durability until the actual bound source,
manifest, latest-head acceptance and trusted supervised host runtime are wired.

Rollback before activation is a source revert. Preserve any created objects;
deleting or rewriting a registration or head is not a rollback procedure.
