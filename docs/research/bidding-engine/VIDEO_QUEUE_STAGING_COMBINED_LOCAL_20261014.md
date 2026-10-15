# Combined queue → video persistence → staging candidate

Date: 2026-10-14. Status: local candidate only. Base queue commit:
`f0df703a828de0e9db9a225c7d2d42b72acea2c4` (published recovery CI passed).
The reviewed direct Drive handoff is included in this isolated checkout. No
production worker, database, Drive job, CI workflow, or queue activation ran.

The resident `process_one_neon` still constructs no staging capability. Its
default shadow-review path and `_stable_environment` still keep
`BRIDGE_PERSIST_DATABASE=false` and remove the inherited writer DSN. For a
separate bounded combined rehearsal, `process_claim` accepts an explicitly
constructed `ExactLeaseStagingHandoff`. The capability is bound to one batch,
job, lease token, worker key, stable job key, source file, revision, output
folder, work folder, and lesson sequence. Mismatch fails before the writer is
called. Its writer DSN must be supplied explicitly and differ from the queue
DSN; there is no fallback from ambient queue credentials. The capability
temporarily installs the exact job environment, calls the supplied
`persist_completed_drive_job` function, and restores the prior environment in
`finally`. The actual persistence helpers still apply their existing
`normalize_dsn` production worker-principal and pooled-endpoint checks; this
candidate creates no new role or key. No general queue admission or global
persistence flag is changed.

The existing producer then passes the actual `persist_video_result` return to
candidate staging in the same process. The combined capability accepts only a
committed producer result with blocked publication, exact Drive source
identity, a completed `database_persist` checkpoint, and
`knowledge_staging.status=PERSISTED` with a UUID replay receipt. It then
requires a separate read-only, repeatable-read connection to observe the
active `video_staging_replay_snapshot`, its hash, exact run/source binding,
and `analysis_run_output` link. The caller must provide that reader explicitly;
`database.video_staging_receipt_read.read_committed_receipt_on_connection`
implements the query without obtaining a DSN. The source is read back once
more after staging, so a mid-staging source change also blocks success. The
queue calls `finish_job`
with `REVIEW_READY` only after this committed readback. Missing receipt,
readback failure, wrong lease, or writer failure takes the existing bounded
retry path and never calls `finish_job` for success. A forged
`knowledge_staging` key in the processor's output is discarded.

Local synthetic tests cover exact success, missing receipt, missing durable
readback after writer return, wrong lease, mid-staging source change, writer
exception and environment restoration, plus queue/writer DSN separation.
Existing direct-handoff tests
cover idempotent retries. A disposable PostgreSQL test for the new readback is
prepared but was not run on this host; the reviewer must run it with the
unchanged migrations and an isolated, read-only connection.

The source-hash preflight was also corrected to use the committed LF bytes of
`database/video_candidate_persistence.py`; the prior Windows checkout hash
would have blocked the Linux workflow before optional persistence.

**Promotion blocker:** migration `0056_universal_video_queue.sql` still
requires `SHADOW_REVIEW_ONLY` and `database_persistence_allowed=false` in
`finish_job`. The queue's admission contract does not yet represent a
separately authorized staging write. This code is deliberately not wired to
the resident entrypoint. A production design must reconcile that semantic
boundary and provide a scoped writer plus independent reader before activation;
do not treat the local `REVIEW_READY` test as production authorization. There
is no new migration or role/key grant in this candidate. Rollback is to revert
the local combined commit; the published recovery and direct-handoff code
remain separately reviewable.
