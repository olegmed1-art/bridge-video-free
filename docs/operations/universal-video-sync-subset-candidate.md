# Fixed-subset queue through the existing resident

This is a local candidate for one bounded School of Bridge batch. It uses the
existing `spool_worker` resident, `neon_worker.stable_review_processor`, Google
Drive source and artifact readbacks, local PDF/AI_DONE creation, and the 0060
worker API. There is no callback ingress, provider verifier, new OIDC grant,
automatic review decision, publication, or canonical persistence in this path.

## Exact candidate and activation boundary

The candidate is `universal_video/neon_worker.py`,
`universal_video/subset_drive_observation.py`, `universal_video/subset_worker.py`,
`database/migrations/0060_fixed_subset_admission.sql`, and
`database/candidates/reviewed_release_candidate.sql` with
`reviewed_release_guard.sql`. The release SQL is a **new synchronous variant**:
its reviewed receipt binds the consumed post-work `source_observation` and the
terminal job event directly. The earlier callback-dependent release candidate
cannot be installed on 0060 alone. The existing late wall-clock expiry check
is retained after the final release write; an expired approval raises and rolls
back the transaction.

The opt-in is `UNIVERSAL_VIDEO_SUBSET_SYNC=1`. `process_one_neon()` rejects a
missing batch binding, checks the worker and collector login capabilities,
calls `video_queue.subset_claim_job` with the pinned batch ID, verifies the
returned batch, reads the Drive source, commits a
fresh collector ATTEMPT observation, calls `authorize_subset_attempt`, runs the
existing processor, verifies live terminal artifacts and the source again,
commits a second ATTEMPT observation, then calls `finish_subset_job`. Heartbeat
and retry use the matching 0060 functions. The canary ends in `CANARY_REVIEW`;
the dependent job remains `PENDING_CANARY` until a separate reviewer releases
it. Each terminal result remains shadow/review only.

Required settings on the existing resident, supplied through its existing
secret facility rather than written into this repository:

| Setting | Value or requirement |
| --- | --- |
| `BRIDGE_VIDEO_QUEUE_DATABASE_URL_FILE` | DSN file for a restricted login with only `video_subset_worker_v2` among the four subset capabilities. The existing direct-URL setting is also accepted by the runtime. Do not use `neondb_owner`. |
| `BRIDGE_VIDEO_QUEUE_WORKER_KEY` | Stable 0060 worker key; the default resident key is also supported. |
| `BRIDGE_VIDEO_SUBSET_COLLECTOR_DATABASE_URL_FILE` | Absolute path to a separate restricted `video_subset_collector` login DSN. |
| `BRIDGE_VIDEO_SUBSET_EXPECTED_HOST` | Exact host in both DSNs for the pinned target endpoint. |
| `BRIDGE_VIDEO_SUBSET_EXPECTED_DATABASE` | Exact database in both DSNs. |
| `BRIDGE_VIDEO_SUBSET_APPROVAL_ID` | UUID of the one live, explicitly authorized fixed-subset approval. |
| `BRIDGE_VIDEO_SUBSET_BATCH_ID` | UUID of the one finalized admission batch. |
| `UNIVERSAL_VIDEO_SUBSET_SYNC` | `1` only for the bounded run, after every gate below is met. |

The two DSNs must have the same host, database and port 5432. Remote DSNs
must explicitly require TLS and channel binding. The Google Drive OAuth and
media/PDF dependencies are the resident's existing ones. This candidate neither
provisions a principal nor starts another compute service.

The last read-only target record names Neon project `misty-poetry-18012774`,
branch `br-aged-mud-b1i64914`, endpoint `ep-noisy-pine-b1pe30sf`, database
`neondb`, and connector principal `neondb_owner`. That owner is an installer
identity, not a resident worker or collector identity. The target and migration
state require fresh read-only reconciliation before any later activation; the
last supplied production record said 0059/0060 were not installed.
The corrected synthetic recovery CI at SHA
`744d068a2cddfb8347411c8e4fb2bd69d19c09ab`, run `38394164538`,
completed successfully with installer, verifier, preserving recovery, 16
negative controls and cleanup. It did not install anything in production.
That CI predates the scoped-claim addition to 0060, so it is not a runtime
proof of this amended migration or its recovery.

## One-batch sequence and stop conditions

1. Verify the exact production target read-only, installer/verifier/recovery
   receipts for 0059/0060, checksum/history, ACL and owner state. Confirm that
   no migration or schema drift occurred after those receipts. The earlier
   full 116-file preflight need not be repeated for this integration.
2. Independently review the new synchronous release SQL, including the
   retained late expiry check and rollback after GRANT/owner changes in the
   migration recovery path. Install SQL only through the separately approved
   migration and release procedure. Its presence does not enable processing.
3. Verify the existing resident image/commit, Drive credential and media
   dependencies; confirm restricted worker and collector logins already exist
   for the pinned endpoint and that no other batch is runtime-enabled. Prepare
   the fixed-subset plan, explicit approval,
   fresh ADMISSION observation, and finalized one-batch admission under the
   0060 approver/collector/admitter roles. Only then enable that approval's
   runtime and set the resident's bounded environment above.
4. Let the canary run once. Require `REVIEW_READY` plus `CANARY_REVIEW`, two
   consumed ATTEMPT observations on its lease, live PDF/AI_DONE readback,
   `released_jobs=0`, and the other job still `PENDING_CANARY`. A missing or
   ambiguous result holds the batch. Stop at this point for independent human
   review of the actual canary artifacts and evidence.
5. A trusted, separate approver may submit a canonical
   `universal-video-reviewed-release-sync-v1` receipt with the batch, approval,
   canary job, consumed post-work observation, terminal and manifest digests,
   review reference, reviewer identity and three false publication/persistence
   flags. Its SHA-256 covers the canonical receipt without `review_sha256`.
   Only a matching active approval and exact pending set release the remaining
   queue rows. No resident process creates or signs this review decision.
6. Let the one dependent job run, inspect its `REVIEW_READY` and final batch
   `REVIEW` state, verify no more claimable job, and disable that approval's
   runtime through the approver role. Preserve jobs, reservations, observations,
   release receipt and events. If any gate fails, stop queue polling and turn
   off approval runtime through the authorized approver; do not delete rows or
   switch a running queue back to the legacy finish API.

## Evidence and untested boundaries

The disposable Windows PostgreSQL 18.6 run at
`../sync-subset-evidence/evidence-20261014T134635Z-afd08aeb/result.json`
installed 0056–0060 and this synchronous release candidate. Two synthetic
Drive files produced local PDF/AI_DONE fixtures. The first job held at
`CANARY_REVIEW`; a synthetic explicit review released exactly one dependent
job; the second ended at `REVIEW`. Missing/denied review, wrong observation,
wrong artifact digest, bad hash, foreign batch/approval/reviewer, worker
release and conflicting replay were rejected without state change. Identical
replay, including after approval revocation, stayed idempotent. Cleanup left
zero active approvals and the local server stopped.
The focused two-active-batch PG18 receipt at
`../sync-subset-evidence/evidence-20261014T134542Z-2dc0b087/result.json`
shows that target selection did not lease the older batch's canary, and target
cleanup did not alter the older batch's synthetic expired third lease or
events. Exact before/after snapshots of the foreign batch were equal in both
cases. The new function is owned by `video_subset_owner`, grants EXECUTE to
`video_subset_worker_v2`, and revokes an injected unknown default EXECUTE
recipient. The old four-argument claim API remains available for legacy callers.
`tests/test_universal_video_subset_sync.py` passed 3 offline tests. Existing
queue tests passed 20 with a Windows-only `SIGALRM` shim; the separate timeout
test was not run on Linux here.

This does not prove real Google Drive I/O, the actual media model/PDF result,
live terminal artifact readback, production role availability, review identity,
or production migration installation. Those are explicit gates for a later
authorized one-batch run. No production SQL, rights, secrets, deploy, merge,
paid compute, or external callback was executed for this candidate.
