# Synthetic journal off-VM recovery probe

2026-09-26. ASSURED / independent I2 before live use.

Existing dated candidate/boot backup commands do not back up the newly prepared
native journal store. This probe first verifies the existing private Object
Storage channel using fixed, credential-free synthetic Journal records.

The owner/exact-main dispatch creates the synthetic journal on its temporary
runner, uploads at most64KiB under a dedicated immutable digest key in the existing
managed private bucket, downloads and verifies exact bytes, and reconstructs an
actual Journal whose subsequent append leaves the original unchanged.
No connection to Light and no operational journal export occur.

Before an upload, the complete configured home-region inventory and all object
pages must be available: one compartment, at most100GB allocated volumes, existing
Standard bucket with versioning/auto-tiering disabled, no public access or PARs,
no replication, fewer than10000 objects including the prospective object, and
observed total plus incoming bytes below the existing8GiB project budget.
These are fresh observations under the shared managed backup queue, not atomic
exclusion of independent administrators. No bucket/volume creation, overwrite,
deletion, retention change or cross-region copy is provided.

A lost upload reply is unconfirmed: the process stops without retry. A subsequent
explicit accepted run checks HEAD and GET at the immutable key, never overwrites.
Private keys, object names and payload contents are not logged.

Success proves only transport and synthetic Journal restore. Production backup
still requires a consistent capture of actual operation journals/manifests,
appropriate private retention, and recovery validation at that integration point.
It does not authorize maintenance permissions or satisfy writer coordination.

Rollback: revert source to prevent another probe. Retain the tiny synthetic
object; this change has no deletion path and never alters an existing backup.
