\set ON_ERROR_STOP on
BEGIN;

DO $test$
DECLARE
    signal record;
    trigger_definition text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.schema_migration
         WHERE migration_key='0356_autopilot_provider_health_recovery'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_0356_MIGRATION_MISSING';
    END IF;

    IF NOT has_function_privilege(
        'autopilot_callback',
        'autopilot.mailbox_rotation_readiness()',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'AUTOPILOT_0356_CALLBACK_ROTATION_READ_MISSING';
    END IF;

    SELECT pg_get_triggerdef(oid) INTO trigger_definition
      FROM pg_trigger
     WHERE tgrelid='autopilot.role_dispatch_outbox'::regclass
       AND tgname='role_dispatch_provider_success'
       AND NOT tgisinternal;
    IF trigger_definition IS NULL
       OR position('on_role_dispatch_provider_success' in trigger_definition)=0 THEN
        RAISE EXCEPTION 'AUTOPILOT_0356_PROVIDER_RECOVERY_TRIGGER_MISSING';
    END IF;

    UPDATE autopilot.project_work_item
       SET state='DONE',completed_at=COALESCE(completed_at,clock_timestamp());
    INSERT INTO autopilot.project_work_item(
        work_key,mailbox_pr,role,target_pr,state,created_by,source
    ) VALUES(
        'sql-provider-health-356-'||txid_current()::text,
        1685,'AUTOPILOT',1150,'PAUSED','SQL_TEST','SQL_TEST'
    );
    UPDATE autopilot.project_planner_state
       SET last_decision_code='PROJECT_DONE_WITH_PAUSED_BACKLOG',
           last_decision_at=clock_timestamp()
     WHERE singleton;

    SELECT * INTO signal
      FROM public.autopilot_operational_health_signal
     WHERE signal_key='autopilot_planner_backlog';
    IF signal.severity IS DISTINCT FROM 'warning'
       OR (signal.details->>'paused_count')::integer<>1
       OR (signal.details->>'open_actionable_count')::integer<>0 THEN
        RAISE EXCEPTION 'AUTOPILOT_0356_PAUSED_BACKLOG_HEALTH_INVALID: %',to_jsonb(signal);
    END IF;
END $test$;

SELECT 3 AS cases,0 AS failures;
ROLLBACK;
