\set ON_ERROR_STOP on
BEGIN;

-- Defense in depth for rolling deployments: new/updated REVIEW_READY rows must
-- bind both terminal Drive artifacts to explicit revision metadata. Historical
-- 0057 REVIEW_READY rows may predate those fields, so install NOT VALID: PostgreSQL
-- still enforces the check for every new or updated row without rejecting legacy
-- rows during migration. Historical remediation/VALIDATE is a separate operation.
ALTER TABLE video_queue.job
    DROP CONSTRAINT IF EXISTS video_job_terminal_revision_check;

ALTER TABLE video_queue.job
    ADD CONSTRAINT video_job_terminal_revision_check CHECK (
        status <> 'REVIEW_READY'
        OR (
            NULLIF(output #>> '{artifact_manifest,artifacts,0,modified_time}', '') IS NOT NULL
            AND NULLIF(output #>> '{artifact_manifest,artifacts,0,version}', '') IS NOT NULL
            AND COALESCE((output #>> '{artifact_manifest,artifacts,0,version}') ~ '^[0-9]+$', false)
            AND NULLIF(output #>> '{artifact_manifest,artifacts,1,modified_time}', '') IS NOT NULL
            AND NULLIF(output #>> '{artifact_manifest,artifacts,1,version}', '') IS NOT NULL
            AND COALESCE((output #>> '{artifact_manifest,artifacts,1,version}') ~ '^[0-9]+$', false)
        )
    ) NOT VALID;

COMMENT ON CONSTRAINT video_job_terminal_revision_check ON video_queue.job IS
    'New/updated REVIEW_READY requires hash-bound Drive modified_time/version; legacy rows staged NOT VALID';

COMMIT;
