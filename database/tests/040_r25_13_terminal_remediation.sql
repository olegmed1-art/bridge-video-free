\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_target_run constant uuid := 'fd23b047-db2e-5c29-aaa6-adf6d54f27c1'::uuid;
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM schema_migration
         WHERE migration_key='0055_terminalize_r25_13_meta_quarantine'
    ) THEN
        RAISE EXCEPTION 'r25.13 terminal remediation migration is not registered';
    END IF;

    -- A clean/fresh database must not manufacture the production-only target row.
    -- The migration therefore proves its intended no-op path in ordinary CI.
    IF EXISTS (
        SELECT 1
          FROM analysis_run
         WHERE analysis_run_id=v_target_run
    ) THEN
        RAISE EXCEPTION 'production-specific r25.13 target unexpectedly exists in fresh CI';
    END IF;

    IF to_regprocedure('record_bridge_video_meta_assessment(uuid,text,uuid[],jsonb,text)') IS NULL THEN
        RAISE EXCEPTION 'independent META terminalization function is missing';
    END IF;

    IF has_function_privilege(
        'bridge_school_worker',
        'record_bridge_video_meta_assessment(uuid,text,uuid[],jsonb,text)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'worker can invoke independent META remediation authority';
    END IF;

    IF NOT has_function_privilege(
        'bridge_school_meta',
        'record_bridge_video_meta_assessment(uuid,text,uuid[],jsonb,text)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'independent META authority cannot invoke remediation function';
    END IF;
END $$;

ROLLBACK;
