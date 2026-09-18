\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout='15s';
SET LOCAL lock_timeout='2s';

CREATE TEMP TABLE repair_source_snapshot ON COMMIT DROP AS
SELECT pg_get_functiondef(
    'autopilot.materialize_role_repair(uuid,text,text)'::regprocedure
) AS definition;
CREATE TEMP TABLE task (
    task_id uuid PRIMARY KEY,goal_type text NOT NULL,
    goal_json jsonb NOT NULL,priority integer NOT NULL
) ON COMMIT DROP;
CREATE TEMP TABLE project_work_item (
    work_item_id uuid PRIMARY KEY,task_spec_json jsonb NOT NULL
) ON COMMIT DROP;
CREATE TEMP TABLE project_work_task (
    task_id uuid PRIMARY KEY,work_item_id uuid NOT NULL
) ON COMMIT DROP;
CREATE TEMP TABLE role_registry (
    role_id text PRIMARY KEY,enabled boolean,execution_scope text,can_repair boolean
) ON COMMIT DROP;
CREATE TEMP TABLE role_dispatch_followup (
    parent_task_id uuid,followup_kind text,followup_task_id uuid,
    origin_task_id uuid,trigger_result_code text,
    PRIMARY KEY(parent_task_id,followup_kind)
) ON COMMIT DROP;
CREATE FUNCTION pg_temp.create_chatgpt_role_followup_task(
    p_task_key text,p_goal_json jsonb,p_priority integer,p_created_by text,p_source text
) RETURNS TABLE(task_id uuid) LANGUAGE sql
AS $stub$ SELECT md5(p_task_key)::uuid $stub$;

INSERT INTO pg_temp.role_registry
VALUES ('TEST_REPO',true,'REPOSITORY',true);
INSERT INTO pg_temp.task
SELECT md5(label)::uuid,'CHATGPT_ROLE_DISPATCH_V1',
       jsonb_build_object(
           'repository','olegmed1-art/bridge-video-free',
           'mailbox_pr',1150,'role','TEST_REPO','target_pr',1150,
           'expected_head_sha',repeat('a',40),'dispatch_epoch',1
       ),0
FROM (VALUES ('provider-failure'),('repository-defect')) AS fixtures(label);

DO $instrument$
DECLARE definition text;
BEGIN
    definition := (SELECT snapshot.definition FROM pg_temp.repair_source_snapshot snapshot);
    IF strpos(definition,'REPAIR_ADMISSION_PROVIDER_TERMINAL_V1')=0 THEN
        RAISE EXCEPTION 'PROVIDER_FAILURE_NO_REPAIR_NOT_INSTALLED';
    END IF;
    EXECUTE replace(replace(
        pg_get_functiondef('autopilot.role_blocker_requires_owner(text)'::regprocedure),
        'autopilot.','pg_temp.'),'''autopilot''','''pg_temp''');
    IF to_regprocedure('autopilot.blocker_remediation_action(text)') IS NOT NULL THEN
        EXECUTE replace(replace(
            pg_get_functiondef('autopilot.blocker_remediation_action(text)'::regprocedure),
            'autopilot.','pg_temp.'),'''autopilot''','''pg_temp''');
        EXECUTE replace(replace(
            pg_get_functiondef('autopilot.blocker_repository_repair_allowed(text)'::regprocedure),
            'autopilot.','pg_temp.'),'''autopilot''','''pg_temp''');
    END IF;
    definition := replace(replace(
        definition,'autopilot.','pg_temp.'),'''autopilot''','''pg_temp''');
    IF strpos(definition,'autopilot.')>0 THEN
        RAISE EXCEPTION 'PROVIDER_FAILURE_NO_REPAIR_FIXTURE_NOT_ISOLATED';
    END IF;
    EXECUTE definition;
END $instrument$;

DO $assert$
DECLARE provider_followup uuid; repository_followup uuid;
BEGIN
    provider_followup := pg_temp.materialize_role_repair(
        md5('provider-failure')::uuid,
        'CODEX_PROVIDER_GENERIC_FAILURE',
        'Task blocked. See the pinned provider failure comment.'
    );
    IF provider_followup IS NOT NULL
       OR EXISTS (
           SELECT 1 FROM pg_temp.role_dispatch_followup
            WHERE parent_task_id=md5('provider-failure')::uuid
       ) THEN
        RAISE EXCEPTION 'PROVIDER_FAILURE_CREATED_REPAIR';
    END IF;

    repository_followup := pg_temp.materialize_role_repair(
        md5('repository-defect')::uuid,
        'BOUNDED_REPOSITORY_DEFECT',
        'Task blocked. See execution details above.'
    );
    IF repository_followup IS NULL
       OR (SELECT count(*) FROM pg_temp.role_dispatch_followup
            WHERE parent_task_id=md5('repository-defect')::uuid)<>1 THEN
        RAISE EXCEPTION 'ELIGIBLE_REPOSITORY_REPAIR_NOT_CREATED';
    END IF;

    PERFORM pg_temp.materialize_role_repair(
        md5('repository-defect')::uuid,
        'BOUNDED_REPOSITORY_DEFECT',
        'Task blocked. See execution details above.'
    );
    IF (SELECT count(*) FROM pg_temp.role_dispatch_followup)<>1 THEN
        RAISE EXCEPTION 'ELIGIBLE_REPOSITORY_REPAIR_NOT_IDEMPOTENT';
    END IF;

    IF (SELECT snapshot.definition FROM pg_temp.repair_source_snapshot snapshot)
       IS DISTINCT FROM pg_get_functiondef(
           'autopilot.materialize_role_repair(uuid,text,text)'::regprocedure) THEN
        RAISE EXCEPTION 'PRODUCTION_FUNCTION_MUTATED_BY_TEST';
    END IF;
END $assert$;

SELECT 2 AS cases,0 AS failures;
ROLLBACK;
