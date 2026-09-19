\set ON_ERROR_STOP on
BEGIN;
DO $pre$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.schema_migration WHERE migration_key='0351_autopilot_paused_evidence_reconcile')
 THEN RAISE EXCEPTION 'AUTOPILOT_OPERATIONAL_SAFETY_REQUIRES_0351'; END IF;
END $pre$;

CREATE TABLE autopilot.migration_0352_function_backup (
 function_key text PRIMARY KEY,
 function_definition text NOT NULL CHECK(length(function_definition) BETWEEN 100 AND 100000)
);
INSERT INTO autopilot.migration_0352_function_backup(function_key,function_definition)
VALUES
 ('claim_project_work_probe',pg_get_functiondef('autopilot.claim_project_work_probe(text,integer)'::regprocedure)),
 ('blocker_remediation_action',pg_get_functiondef('autopilot.blocker_remediation_action(text)'::regprocedure)),
 ('enforce_role_dispatch_mailbox_capacity',pg_get_functiondef('autopilot.enforce_role_dispatch_mailbox_capacity()'::regprocedure));

CREATE TABLE autopilot.mailbox_rotation_signal (
 mailbox_pr integer PRIMARY KEY REFERENCES autopilot.role_dispatch_mailbox_registry(mailbox_pr),
 status text NOT NULL CHECK(status IN ('PREPARE_ROTATION')),
 used_dispatches integer NOT NULL CHECK(used_dispatches>=0),
 max_dispatches integer NOT NULL CHECK(max_dispatches BETWEEN 1 AND 100),
 observed_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION autopilot.mailbox_rotation_readiness()
RETURNS TABLE(mailbox_pr integer,used_dispatches integer,max_dispatches integer,remaining integer,readiness text)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path TO 'pg_catalog','autopilot'
AS $f$
 SELECT r.mailbox_pr,
        count(o.dispatch_id)::integer AS used_dispatches,
        r.max_dispatches::integer,
        (r.max_dispatches-count(o.dispatch_id))::integer AS remaining,
        CASE
          WHEN count(o.dispatch_id)>=r.max_dispatches THEN 'ROTATION_REQUIRED'
          WHEN count(o.dispatch_id)>=ceil(r.max_dispatches*0.8)::integer THEN 'PREPARE_ROTATION'
          ELSE 'NORMAL'
        END
 FROM autopilot.role_dispatch_mailbox_registry r
 LEFT JOIN autopilot.role_dispatch_outbox o ON o.mailbox_pr=r.mailbox_pr
 WHERE r.lifecycle='ACTIVE'
 GROUP BY r.mailbox_pr,r.max_dispatches
$f$;
REVOKE ALL ON FUNCTION autopilot.mailbox_rotation_readiness() FROM PUBLIC;

CREATE OR REPLACE FUNCTION autopilot.mailbox_e2e_acceptance()
RETURNS TABLE(mailbox_pr integer,dispatch_count integer,published_count integer,callback_count integer,retained_evidence_count integer,state text,first_dispatch_at timestamptz,last_callback_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path TO 'pg_catalog','autopilot'
AS $f$
 WITH active AS (
   SELECT mailbox_pr FROM autopilot.role_dispatch_mailbox_registry WHERE lifecycle='ACTIVE'
 ), agg AS (
   SELECT a.mailbox_pr,
          count(o.dispatch_id)::integer dispatch_count,
          count(o.dispatch_id) FILTER(WHERE o.published_at IS NOT NULL OR o.sent_at IS NOT NULL)::integer published_count,
          count(o.dispatch_id) FILTER(WHERE o.status='CALLBACK_ACCEPTED')::integer callback_count,
          min(o.created_at) first_dispatch_at,
          max(o.completed_at) FILTER(WHERE o.status='CALLBACK_ACCEPTED') last_callback_at
   FROM active a LEFT JOIN autopilot.role_dispatch_outbox o ON o.mailbox_pr=a.mailbox_pr
   GROUP BY a.mailbox_pr
 ), ev AS (
   SELECT a.mailbox_pr,count(e.evidence_id)::integer retained_evidence_count
   FROM active a
   LEFT JOIN autopilot.role_dispatch_outbox o ON o.mailbox_pr=a.mailbox_pr
   LEFT JOIN autopilot.evidence e ON e.task_id=o.task_id AND e.retained
   GROUP BY a.mailbox_pr
 )
 SELECT agg.mailbox_pr,agg.dispatch_count,agg.published_count,agg.callback_count,ev.retained_evidence_count,
        CASE
          WHEN agg.dispatch_count=0 THEN 'NO_REAL_E2E_OBSERVED'
          WHEN agg.callback_count>0 AND ev.retained_evidence_count>0 THEN 'E2E_ACCEPTED'
          WHEN agg.published_count>0 THEN 'E2E_IN_PROGRESS'
          ELSE 'DISPATCH_OBSERVED'
        END,
        agg.first_dispatch_at,agg.last_callback_at
 FROM agg JOIN ev USING(mailbox_pr)
$f$;
REVOKE ALL ON FUNCTION autopilot.mailbox_e2e_acceptance() FROM PUBLIC;

DO $planner_patch$
DECLARE original text; patched text; anchor text;
BEGIN
 SELECT function_definition INTO original FROM autopilot.migration_0352_function_backup WHERE function_key='claim_project_work_probe';
 anchor := 'WHEN NOT EXISTS ('||E'\n'||'                       SELECT 1 FROM autopilot.project_work_item'||E'\n'||'                        WHERE state NOT IN (''DONE'',''PAUSED'')'||E'\n'||'                   ) THEN ''PROJECT_DONE''';
 patched:=replace(original,anchor,anchor||E'\n'||'                   WHEN EXISTS ('||E'\n'||'                       SELECT 1 FROM autopilot.project_work_item WHERE state=''PAUSED'''||E'\n'||'                   ) THEN ''PROJECT_DONE_WITH_PAUSED_BACKLOG''');
 IF patched=original THEN
   patched:=replace(original,
     ') THEN ''PROJECT_DONE'''||E'\n'||'                   ELSE ''IDLE_NO_ELIGIBLE_TASK''',
     ') THEN CASE WHEN EXISTS (SELECT 1 FROM autopilot.project_work_item WHERE state=''PAUSED'') THEN ''PROJECT_DONE_WITH_PAUSED_BACKLOG'' ELSE ''PROJECT_DONE'' END'||E'\n'||'                   ELSE ''IDLE_NO_ELIGIBLE_TASK''');
 END IF;
 IF patched=original OR position('PROJECT_DONE_WITH_PAUSED_BACKLOG' in patched)=0 THEN RAISE EXCEPTION 'AUTOPILOT_0352_PLANNER_SOURCE_DRIFT'; END IF;
 EXECUTE patched;
END $planner_patch$;

DO $blocker_patch$
DECLARE original text; patched text;
BEGIN
 SELECT function_definition INTO original FROM autopilot.migration_0352_function_backup WHERE function_key='blocker_remediation_action';
 patched:=replace(original,
   'WHEN autopilot.role_blocker_requires_owner(p_result_code) THEN ''OWNER_HOLD''',
   'WHEN autopilot.role_blocker_requires_owner(p_result_code) THEN ''OWNER_HOLD'' WHEN p_result_code=''AUTOPILOT_UNCLASSIFIED_FAILURE'' THEN ''OWNER_HOLD''');
 IF patched=original OR position('AUTOPILOT_UNCLASSIFIED_FAILURE' in patched)=0 THEN RAISE EXCEPTION 'AUTOPILOT_0352_BLOCKER_SOURCE_DRIFT'; END IF;
 EXECUTE patched;
END $blocker_patch$;

DO $capacity_patch$
DECLARE original text; patched text;
BEGIN
 SELECT function_definition INTO original FROM autopilot.migration_0352_function_backup WHERE function_key='enforce_role_dispatch_mailbox_capacity';
 patched:=replace(original,
   'IF used >= mailbox.max_dispatches THEN',
   'IF used + 1 >= ceil(mailbox.max_dispatches*0.8)::integer AND used < mailbox.max_dispatches THEN INSERT INTO autopilot.mailbox_rotation_signal(mailbox_pr,status,used_dispatches,max_dispatches,observed_at) VALUES(mailbox.mailbox_pr,''PREPARE_ROTATION'',used+1,mailbox.max_dispatches,now()) ON CONFLICT(mailbox_pr) DO UPDATE SET status=EXCLUDED.status,used_dispatches=EXCLUDED.used_dispatches,max_dispatches=EXCLUDED.max_dispatches,observed_at=EXCLUDED.observed_at; END IF; IF used >= mailbox.max_dispatches THEN');
 IF patched=original OR position('mailbox_rotation_signal' in patched)=0 THEN RAISE EXCEPTION 'AUTOPILOT_0352_CAPACITY_SOURCE_DRIFT'; END IF;
 EXECUTE patched;
END $capacity_patch$;

INSERT INTO public.schema_migration(migration_key) VALUES('0352_autopilot_operational_safety_signals');
COMMIT;
