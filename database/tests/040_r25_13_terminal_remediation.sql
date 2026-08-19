\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
    v_target_run constant uuid := 'fd23b047-db2e-5c29-aaa6-adf6d54f27c1'::uuid;
    v_idempotency constant text := 'r25-13-independent-terminal-quarantine-20260820-v1';
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM schema_migration
         WHERE migration_key='0055_terminalize_r25_13_meta_quarantine'
    ) THEN
        RAISE EXCEPTION 'r25.13 terminal remediation migration is not registered';
    END IF;

    -- Fresh CI has no production-specific run and validates the migration's no-op path.
    -- Production has the exact run and must validate its fully terminalized path.
    IF EXISTS (
        SELECT 1 FROM analysis_run WHERE analysis_run_id=v_target_run
    ) THEN
        IF NOT EXISTS (
            SELECT 1
              FROM analysis_run ar
             WHERE ar.analysis_run_id=v_target_run
               AND ar.algorithm_key='bridge-video-master-analysis'
               AND ar.algorithm_version='3.1-free-r25.13-checkpoint'
               AND ar.parameters_snapshot->>'job_id'='86e814014cabee88785a53340ab85666'
               AND ar.parameters_snapshot->>'source_drive_id'='1rGX92YskXRtXHc53lyj9JMU3g24H5vCI'
               AND ar.run_status='failed'
               AND ar.completed_at IS NOT NULL
               AND ar.technical_record_status='quarantined'
               AND ar.quality_confirmation_status='rejected'
               AND ar.publication_authorization_status='blocked'
        ) THEN
            RAISE EXCEPTION 'production r25.13 target is not in the expected terminal quarantine state';
        END IF;

        IF NOT EXISTS (
            SELECT 1
              FROM bridge_video_evidence_gate g
             WHERE g.analysis_run_id=v_target_run
               AND g.assessment_status='quarantined'
               AND g.assessor_authority='independent_meta'
               AND NOT g.self_reported
               AND NOT g.publication_allowed
               AND g.idempotency_key=v_idempotency
               AND g.evidence_ids=ARRAY[
                    'bb36e652-93ac-5da0-19f0-c85598a527be'::uuid,
                    'f436c1c0-63ee-f5c6-8c3c-782b0bec4b7d'::uuid
               ]
        ) THEN
            RAISE EXCEPTION 'production r25.13 terminal quarantine lacks the expected independent META gate';
        END IF;

        IF NOT EXISTS (
            SELECT 1
              FROM outbox_message o
              JOIN domain_event de ON de.event_id=o.event_id
             WHERE de.aggregate_id=v_target_run
               AND de.event_type='BridgeVideoMetaAssessed'
               AND de.idempotency_key=v_idempotency
               AND o.status='published'
               AND o.published_at IS NOT NULL
               AND de.event_position IS NOT NULL
        ) THEN
            RAISE EXCEPTION 'production r25.13 terminal quarantine lacks a published META event';
        END IF;
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
