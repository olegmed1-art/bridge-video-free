# 0061/0062 application plan — prepared only, 2026-10-15

**No production migration, grant, role change, private job, or deploy is
authorized by this document.** Files `database/proposals/0061_one_job_staging_admission_v1.sql`
and `0062_one_job_private_result_gate_v1.sql` are proposals, not registered
migrations. The PostgreSQL 18 rehearsal on `ca57b2bb` predates the bounded
approval change; CI and local tests of a later commit do not replace an
exact-version disposable PostgreSQL rehearsal or a current production audit.

## Required existing identities

- An independently verified installer that already owns the affected schema
  and 0060 objects; historical `neondb_owner` is not proof of the current
  production installer. Use a direct, non-pooled connection only during an
  authorized maintenance window.
- Existing `video_subset_owner` as the owner of new tables and
  `SECURITY DEFINER` functions, with its pre-existing 0060 dependencies and
  `canonical_json_text(jsonb)` EXECUTE checked in the catalog.
- Separate restricted logins for `video_subset_approver`,
  `video_subset_worker_v2`, and `video_subset_collector`; the approved subset
  is admitted by the existing `video_subset_admitter` capability. Record
  `session_user`, `current_user`, memberships and effective privileges for
  each actual login. The approver must not inherit worker, collector, owner,
  app or broad-writer capabilities; the queue worker must not inherit
  approver, owner, app or broad-writer capabilities; the collector must not
  have public/private result DML. The private route never uses
  `bridge_school_worker_principal` or its broad writer DSN.
- Existing queue and subset objects from migrations 0001–0060, including
  the validated v2 output constraint. Any missing identity, object, ACL or
  dependency is a hold. Do not create a replacement login or grant broad
  privileges to overcome a missing role.

## Gated sequence for a later authorized application

1. Reconcile current `main`, the exact SQL hashes, the actual database
   project/branch/endpoint, applied migration ledger, live role membership,
   inherited table/column/function ACLs, output constraint, active leases,
   subset approval and resident state. Use the read-only inventory in
   `VIDEO_ONE_JOB_ACTIVATION_PREFLIGHT_20261015.md`; do not trust its
   historical endpoint or login names. Stop if 0061/0062 are already present,
   modified, or partly installed.
2. Confirm a current recoverable backup with tested restore, maintenance
   window, and a quiesced ordinary resident for the target batch. No sibling
   may become claimable when the one-shot lock is released. Record a
   before-state snapshot and a rollback owner.
3. Rehearse exact SQL 0061 then 0062, their necessary ownership and
   least-privilege ACL delta, and the exact Python head on a disposable
   PostgreSQL 18 database or isolated production-like branch. Test a live
   0060 claim, wrong role/token/source, bounded approval with lease and subset
   deadlines shorter than the requested nine minutes, under-three-minute
   refusal, no extension past either deadline, same-attempt replay,
   revocation, private stage/read/finish, ACK loss and zero sibling release.
   Obtain independent I2 review of the exact artifacts and rollback.
4. Only after a separate production decision, register immutable migrations
   for 0061/0062 according to the repository's migration mechanism. Apply
   0061 before 0062 through the verified installer on a direct connection;
   do not edit a checksum-registered migration in place. The required future
   ACL delta is limited to approver EXECUTE on approval/revocation and queue
   worker EXECUTE on private stage/read/finish, owned by
   `video_subset_owner`, with PUBLIC EXECUTE and direct table DML absent.
   Revoke the obsolete public-writer route EXECUTEs described in
   `VIDEO_ONE_JOB_PRIVATE_RESULT_GATE_20261015.md`. No runtime role receives
   broad result-table DML. This step is a **future requirement**, not a
   grant performed or requested by the present task.
5. Re-read catalog ownership, effective inherited ACLs, migration checksums
   and v2 constraint from each restricted login. Require exact `main`/service
   image binding before a single explicitly admitted job. Keep the resident
   in its previous mode until an independently reviewed, one-job canary and
   source/result readback pass. A database installation alone does not
   activate or prove the service.

## Failure and rollback

- Before the first private output, do not launch a claim. If 0062 or its ACL
  check fails after 0061 installation, block new approvals, withhold/revoke
  the new runtime EXECUTEs, and retain an inert schema while reconciling the
  partial install. Restore the verified snapshot only under a separate
  data-loss-reviewed decision; never force a down migration to hide drift.
- After any private output exists, preserve the private rows, receipts and
  v2 output constraint for recovery. Disable new approvals and runtime
  EXECUTEs, return the unchanged resident to its prior shadow route, and
  reconcile the exact job and Drive evidence. Do not drop private tables or
  restore the v1 output constraint while v2 rows may exist.
- An uncertain claim COMMIT is reconciled read-only from the precommit
  receipt and database token; never reclaim blindly. An expired/short-budget
  approval causes no private write and requires a fresh attempt according to
  the existing retry rules.

No current production target, effective login ACL, backup/restore evidence,
service image, or live queue state has been verified in this follow-up.
