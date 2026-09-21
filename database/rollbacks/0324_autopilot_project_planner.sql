\set ON_ERROR_STOP on
BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM autopilot.project_work_item) THEN
        RAISE EXCEPTION 'AUTOPILOT_PROJECT_PLANNER_ROLLBACK_REQUIRES_EMPTY_CATALOG';
    END IF;
END $$;

DROP TRIGGER IF EXISTS zz_autopilot_project_work_task_terminal ON autopilot.task;
DROP TRIGGER IF EXISTS autopilot_project_work_followup
    ON autopilot.role_dispatch_followup;

DROP FUNCTION IF EXISTS autopilot.on_project_work_task_terminal();
DROP FUNCTION IF EXISTS autopilot.on_project_work_followup();
DROP FUNCTION IF EXISTS autopilot.fail_project_work_probe(
    uuid,text,bigint,text,boolean
);
DROP FUNCTION IF EXISTS autopilot.materialize_project_work_probe(
    uuid,text,bigint,boolean,text
);
DROP FUNCTION IF EXISTS autopilot.claim_project_work_probe(text,integer);
DROP FUNCTION IF EXISTS autopilot.adopt_project_work_task(text,uuid,text,text);
DROP FUNCTION IF EXISTS autopilot.register_project_work_item(
    text,text,integer,integer,text,text,text
);

DROP TABLE IF EXISTS autopilot.project_work_task;
DROP TABLE IF EXISTS autopilot.project_planner_state;
DROP TABLE IF EXISTS autopilot.project_work_item;

DELETE FROM public.schema_migration
 WHERE migration_key = '0324_autopilot_project_planner';

COMMIT;
