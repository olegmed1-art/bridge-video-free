\set ON_ERROR_STOP on
BEGIN;
DO $pre$
BEGIN
 IF NOT EXISTS (SELECT FROM schema_migration WHERE migration_key='0058_universal_video_terminal_v2_gate') THEN
   RAISE EXCEPTION 'VIDEO_TERMINAL_SIZE_GUARD_REQUIRES_0058';
 END IF;
END $pre$;

-- Match terminal_evidence_v2's bounded readers: PDF 512 MiB, AI_DONE 8 MiB.
-- NOT VALID preserves historical rows while enforcing every new/updated row.
-- CASE avoids unsafe casts and SQL NULL allowing malformed evidence through.
ALTER TABLE video_queue.job ADD CONSTRAINT video_job_terminal_size_check CHECK (
    status <> 'REVIEW_READY' OR (
            CASE WHEN jsonb_typeof(output #> '{artifact_manifest,artifacts,0,size_bytes}') = 'number'
                 THEN (output #>> '{artifact_manifest,artifacts,0,size_bytes}')::numeric BETWEEN 1 AND 536870912
                      AND trunc((output #>> '{artifact_manifest,artifacts,0,size_bytes}')::numeric) = (output #>> '{artifact_manifest,artifacts,0,size_bytes}')::numeric
                 ELSE false END
        AND
            CASE WHEN jsonb_typeof(output #> '{artifact_manifest,artifacts,1,size_bytes}') = 'number'
                 THEN (output #>> '{artifact_manifest,artifacts,1,size_bytes}')::numeric BETWEEN 1 AND 8388608
                      AND trunc((output #>> '{artifact_manifest,artifacts,1,size_bytes}')::numeric) = (output #>> '{artifact_manifest,artifacts,1,size_bytes}')::numeric
                 ELSE false END
    )
) NOT VALID;
COMMENT ON CONSTRAINT video_job_terminal_size_check ON video_queue.job IS
 'REVIEW_READY requires integer artifact sizes: PDF 1..536870912 and AI_DONE 1..8388608 bytes; historical rows not validated';
INSERT INTO schema_migration(migration_key) VALUES ('0059_universal_video_terminal_size_guard');
COMMIT;
