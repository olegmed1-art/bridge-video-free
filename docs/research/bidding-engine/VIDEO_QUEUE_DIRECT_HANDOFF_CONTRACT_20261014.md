# Video queue to staging: integration contract

Source checkout: `C:/Users/Oleg/Documents/Codex/2026-10-10/task/outbox-api-integration-20261014`
on branch `codex/video-staging-direct-handoff-20261014`, baseline implementation
commit `7d3f69a8cd8c7d764b626e765d63209c4c85a63b`. The resident queue's
separate checkout is
`C:/Users/Oleg/Documents/Codex/2026-10-13/task-3/sync-subset-resident-worktree`;
this contract does not modify it.

## Exact producer result and invocation

`database.video_result_persistence.persist_video_result(raw_dsn, master, done)`
returns a dict. On a committed run, `persisted is True` and `rolled_back is
False`. Its lineage selectors are `source_id`, `source_asset_id`,
`analysis_run_id`, `artifact_version_id`, `school_id`, `algorithm_version_id`,
and `changeset_id`. It also returns `transcript_id`,
`learning_interaction_id`, counts, `technical_record_status='recorded'`,
`quality_confirmation_status='pending'`, and
`publication_authorization_status='blocked'`. These IDs select database facts;
they are not themselves proof of source or school authority.

`bridge_neon_persistence.persist_completed_drive_job(token)` obtains that
result, adds `source_identity`, then sets `result['outbox']` from
`publish_if_authorized` and `result['checkpoint']` from the completed
`database_persist` checkpoint. The required outbox state for this handoff is
`BLOCKED_PENDING`; the technical event remains unpublished. It then calls
`video_completed_staging_handoff.stage_completed_result(result)` **in the same
Python process**, passing the same dict object. This is the sole handoff point
for the active Drive worker. The default route calls this function inside
`run_master_3_1_free_semantic.process_job` for a fresh or retried job; r26
routes go through `run_master_3_1_free_semantic_v2.process_job`, which delegates
to that same function. The workflow's terminal-job callback also calls it. A
queue worker that uses this function must not add a second staging call.

`stage_completed_result(result, *, postprocess=None, environment=None)` invokes
`diana_longitudinal_postprocess_v3.main(persisted_result=result,
staging_receipt=receipt)` only when `BRIDGE_PERSIST_DATABASE=true` and both
`BRIDGE_OUTPUT_FOLDER_ID` and `BRIDGE_LESSON_NUMBER` are nonempty. The v3 wrapper
forwards these keywords to v4.2. V4.2 builds the quality payload in memory,
then calls `database.video_staging_handoff.persist_quality_handoff(
BRIDGE_WORKER_DATABASE_URL, payload, persisted_result)`. The payload must have
`job_id` and `quality_v2.candidate_staging_records` plus a full
`video-canon-staging-replay-v1` replay for video candidates. Do not reconstruct
`persisted_result` from a Drive artifact, queue record, checkpoint, or IDs.

The handoff uses `database.video_candidate_persistence.persist_quality_candidates`
with that actual result as a separate keyword argument. The SQL writer checks
the fixed active school, exact source/asset/run/artifact, registered source
identity, job ID, source locator, immutable media hash, and run input/output
links. A mismatch fails; there is no latest-run fallback. It commits staging
candidate rows, the `video_staging_replay_snapshot` candidate receipt, and an
`analysis_run_output` binding in one idempotent transaction. The returned
`replay_receipt_id` is required for `knowledge_staging.status='PERSISTED'`.
The resulting producer dict also exposes `knowledge_staging.candidate_records`.
An ordinary non-video quality observation with no requested replay yields
`BLOCKED_NO_REPLAY`, no candidate write, and no receipt. Missing database mode,
output folder, or lesson number yields `NOT_REQUESTED`.

## Retry and failure boundary

The `database_persist` checkpoint precedes staging. If staging fails after that
checkpoint, the job must fail; that checkpoint alone is not a staging receipt.
A retry re-enters `persist_completed_drive_job`, reuses the deterministic video
result and source identity, keeps the technical outbox blocked, and repeats the
candidate writer. Exact repeats return the same candidate and receipt IDs with
zero new rows. Failure after the candidate transaction but before Drive
artifact upload is handled by the same retry. A nonzero postprocess exit, a
missing receipt, invalid/empty quality, or an incompatible publication state
must never be acknowledged as `PERSISTED`. Queue acknowledgement must follow
the returned `knowledge_staging` state and the queue's own durable completion
rules; a prior `database_persist` checkpoint is insufficient.

## Database and rollout boundary

No new migration is introduced by this handoff. It relies on the existing
migrated schema: `0001_global_registry.sql` (outbox),
`0002_learning_core.sql` (source identity), `0010_knowledge_media.sql`
(analysis-run output), `0046_meta_reliability_core.sql` (checkpoint events),
and `0049_analysis_candidate_staging.sql` (staging candidates and worker
privileges). The selected algorithm revision must be registered under the
normal migration chain; `0051` and `0053` register/gate r25.12 and r25.13
diagnostic candidates, not production authorization.
Apply the repository's normal migration chain in an isolated PostgreSQL
rehearsal, never cherry-pick these files into production. Existing DSN
normalization and worker-role checks remain in force. No schema change,
technical-event publication, canon activation, student delivery, production
worker activation, or queue checkout edit is authorized by this contract.
