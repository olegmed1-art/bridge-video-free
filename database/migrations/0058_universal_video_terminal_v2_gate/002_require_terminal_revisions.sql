\set ON_ERROR_STOP on
BEGIN;

-- Defense in depth for rolling deployments: even an older authorized worker
-- cannot persist REVIEW_READY unless both terminal Drive artifacts are bound
-- to explicit Drive revision metadata. The Python verifier also hash-binds
-- these values in the manifest and rereads them immediately before finish_job.
ALTER TABLE video_queue.job
    DROP CONSTRAINT IF EXISTS video_job_terminal_revision_check;

ALTER TABLE video_queue.job
    ADD CONSTRAINT video_job_terminal_revision_check CHECK (
        status <> 'REVIEW_READY'
        OR (
            NULLIF(output #>> '{artifact_manifest,artifacts,0,modified_time}', '') IS NOT NULL
            AND (output #>> '{artifact_manifest,artifacts,0,version}') ~ '^[0-9]+$'
            AND NULLIF(output #>> '{artifact_manifest,artifacts,1,modified_time}', '') IS NOT NULL
            AND (output #>> '{artifact_manifest,artifacts,1,version}') ~ '^[0-9]+$'
        )
    );

COMMENT ON CONSTRAINT video_job_terminal_revision_check ON video_queue.job IS
    'REVIEW_READY requires hash-bound Drive modified_time/version for master PDF and AI_DONE';

COMMIT;
