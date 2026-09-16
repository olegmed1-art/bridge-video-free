\set ON_ERROR_STOP on
BEGIN;
SET LOCAL statement_timeout='15s';
SET LOCAL lock_timeout='2s';

-- Focused function-level test in an isolated test database after migration 0337.
-- Instrument a copy of the installed production function, never its policy:
-- real task/registry reads and followup writes are redirected to TEMP fixtures.
-- This does not claim full callback/trigger or concurrent runtime integration.
CREATE TEMP TABLE repair_source_snapshot ON COMMIT DROP AS
SELECT pg_get_functiondef('autopilot.materialize_role_repair(uuid,text,text)'::regprocedure) AS definition;
CREATE TEMP TABLE task (
    task_id uuid PRIMARY KEY,goal_type text NOT NULL,
    goal_json jsonb NOT NULL,priority integer NOT NULL
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
INSERT INTO pg_temp.role_registry VALUES
    ('TEST_REPO',true,'REPOSITORY',true),
    ('TEST_OWNER',true,'OWNER_GATED',true),
    ('TEST_READONLY',true,'READ_ONLY_EXTERNAL',true),
    ('TEST_DISABLED',false,'REPOSITORY',true),
    ('TEST_NO_REPAIR',true,'REPOSITORY',false);
CREATE TEMP TABLE repair_cases (
    id integer PRIMARY KEY,role_id text,result_code text,expected_allowed boolean
) ON COMMIT DROP;
INSERT INTO pg_temp.repair_cases VALUES
    (1,'TEST_REPO','BOUNDED_REPOSITORY_DEFECT',true),
    (2,'TEST_OWNER','BOUNDED_REPOSITORY_DEFECT',false),
    (3,'TEST_READONLY','BOUNDED_REPOSITORY_DEFECT',false),
    (4,'TEST_DISABLED','BOUNDED_REPOSITORY_DEFECT',false),
    (5,'TEST_MISSING','BOUNDED_REPOSITORY_DEFECT',false),
    (6,'TEST_NO_REPAIR','BOUNDED_REPOSITORY_DEFECT',false),
    (7,'TEST_REPO','TARGET_PR_SUPERSEDED_AND_CHECKS_INCOMPLETE',false),
    (8,'TEST_REPO','SUPERSEDED_RECOVERY_FLOW_AND_READ_ONLY_GATE_BYPASS',false),
    (9,'TEST_REPO','TARGET_PR_OBSOLETE',false),
    (10,'TEST_REPO','OWNER_REQUIRED',false),
    (11,'TEST_REPO',NULL,false);
INSERT INTO pg_temp.task
SELECT md5('repair-case-'||id)::uuid,'CHATGPT_ROLE_DISPATCH_V1',
       jsonb_build_object('repository','olegmed1-art/bridge-video-free',
           'mailbox_pr',1150,'role',role_id,'target_pr',1150,
           'expected_head_sha',repeat('a',40),'dispatch_epoch',1),0
FROM pg_temp.repair_cases;

DO $instrument$
DECLARE definition text;
BEGIN
    definition := (SELECT s.definition FROM pg_temp.repair_source_snapshot s);
    IF strpos(definition,'REPAIR_ADMISSION_V1')=0 THEN
        RAISE EXCEPTION 'REPAIR_ADMISSION_NOT_INSTALLED';
    END IF;
    EXECUTE replace(replace(
        pg_get_functiondef('autopilot.role_blocker_requires_owner(text)'::regprocedure),
        'autopilot.','pg_temp.'),'''autopilot''','''pg_temp''');
    definition := replace(replace(definition,
        'autopilot.','pg_temp.'),'''autopilot''','''pg_temp''');
    IF strpos(definition,'autopilot.')>0 THEN
        RAISE EXCEPTION 'REPAIR_ADMISSION_FIXTURE_NOT_ISOLATED';
    END IF;
    EXECUTE definition;
END $instrument$;

CREATE TEMP TABLE repair_test_results ON COMMIT DROP AS
SELECT c.*,pg_temp.materialize_role_repair(
    md5('repair-case-'||id)::uuid,result_code,
    'Task blocked. See execution details above.') IS NOT NULL AS allowed
FROM pg_temp.repair_cases c;
DO $assert$
BEGIN
    IF EXISTS(SELECT 1 FROM pg_temp.repair_test_results
              WHERE allowed IS DISTINCT FROM expected_allowed) THEN
        RAISE EXCEPTION 'REPAIR_ADMISSION_TEST_FAILED';
    END IF;
    PERFORM pg_temp.materialize_role_repair(md5('repair-case-1')::uuid,
        'BOUNDED_REPOSITORY_DEFECT','Task blocked. See execution details above.');
    IF (SELECT count(*) FROM pg_temp.role_dispatch_followup)<>1 THEN
        RAISE EXCEPTION 'REPAIR_ADMISSION_IDEMPOTENCY_FAILED';
    END IF;
    IF (SELECT s.definition FROM pg_temp.repair_source_snapshot s)
       IS DISTINCT FROM pg_get_functiondef(
           'autopilot.materialize_role_repair(uuid,text,text)'::regprocedure) THEN
        RAISE EXCEPTION 'REPAIR_ADMISSION_SOURCE_MUTATED';
    END IF;
END $assert$;
SELECT count(*) AS cases,
       count(*) FILTER(WHERE allowed IS DISTINCT FROM expected_allowed) AS failures
FROM pg_temp.repair_test_results;
ROLLBACK;
