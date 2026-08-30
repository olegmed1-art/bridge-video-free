\set ON_ERROR_STOP on
BEGIN;

DO $$
BEGIN
    IF to_regclass('video_queue.job') IS NOT NULL
       AND EXISTS (SELECT 1 FROM video_queue.job) THEN
        RAISE EXCEPTION 'VIDEO_QUEUE_ROLLBACK_REFUSES_NONEMPTY_QUEUE';
    END IF;
END $$;

DROP SCHEMA IF EXISTS video_queue CASCADE;
DELETE FROM schema_migration WHERE migration_key='0056_universal_video_queue';

COMMIT;
