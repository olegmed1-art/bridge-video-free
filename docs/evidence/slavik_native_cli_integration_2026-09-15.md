# Native CLI queue transport — unapproved design

Date: 2026-09-15. Status: `NOT_ACTIVATED / LIFECYCLE_REVIEW_FAILED`.

The verified ChatGPT-authenticated CLI runs as `ubuntu`; the resident worker runs as `school-autopilot`. Its current entrypoint is `oracle_autopilot.worker_v17`, whose source was not accessible to the current remote terminal identity. Reading it returned PermissionError; the remote command tool rejected the attempted `sudo -n -u school-autopilot` read with `Command not allowed`. Do not change identity, copy login credentials, weaken permissions, or replace the running service to bypass that boundary.

The standalone client has durable pre-submission intent, duplicate suppression, strict report binding, immutable collected results, and explicit provider failure/unknown states. Sixteen focused offline tests pass. It is not yet an installed queue adapter, an autonomous completion monitor, or a publication issuer. No paid API key is used by the client.

## Required integration before activation

- Review the actual deployed worker and use its approved release path. Keep six shared queue slots; never add an independent six-slot executor.
- Implement a durable bounded `reserve → submit → acknowledge → collect → finish` integration. Submission failures with an unknown external outcome retain the slot and must never blindly resubmit. Known terminal provider failures need a retained BLOCKED result and safe slot release.
- Preserve existing v3 in-flight dispatches. Atomically reserve future eligible dispatches before any CLI creation, so the legacy sender cannot also send `@codex`.
- Lock task and work authority consistently at success transitions. Revalidate immediately before submission. A pause after submission must still allow a proven terminal BLOCKED outcome without authorizing new work.
- REPAIR success requires exact-head atomic publication and independent fresh readback; generated local changes do not count as published success. Keep source validation and untrusted code separate from credentials.
- Verify a real queue event through native task ID, terminal receipt, slot release, and next eligible work. Restart and lost-response cases must not duplicate a provider task.

## Rejected SQL sketch (not an executable migration)

The sketch below is retained for review, not under `database/migrations`. Do not execute it. Independent Red Team found missing NULL deadline rejection, incomplete ACK authority checks, missing row locks, BLOCKED closure prevented after pause, and indefinite v4 slot retention without a completion monitor. The latest standalone client corrects mutable result collection and distinguishes some explicit provider terminal failures, but does not resolve the missing queue integration. No schema or automation was changed.

```sql
\set ON_ERROR_STOP on
BEGIN;

DO $$ BEGIN
 IF NOT EXISTS (SELECT FROM public.schema_migration WHERE migration_key='0335_autopilot_codex_delivery_window') THEN
  RAISE EXCEPTION 'CLI_TRANSPORT_REQUIRES_0335';
 END IF;
END $$;

CREATE TABLE autopilot.codex_cli_config (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 enabled boolean NOT NULL DEFAULT false,
 cutover_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
INSERT INTO autopilot.codex_cli_config DEFAULT VALUES;
CREATE TABLE autopilot.codex_cli_dispatch (
 dispatch_id uuid PRIMARY KEY REFERENCES autopilot.role_dispatch_outbox(dispatch_id),
 reservation_id uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),
 assignment jsonb NOT NULL,
 state text NOT NULL DEFAULT 'RESERVED' CHECK(state IN ('RESERVED','SUBMITTED','TERMINAL')),
 provider_task_id text UNIQUE CHECK(provider_task_id ~ '^task_[A-Za-z0-9_]{1,120}$'),
 prompt_sha256 text CHECK(prompt_sha256 ~ '^[0-9a-f]{64}$'),
 report_sha256 text CHECK(report_sha256 ~ '^[0-9a-f]{64}$'),
 terminal jsonb,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 submitted_at timestamptz,
 completed_at timestamptz
);
CREATE TABLE autopilot.migration_0337_function_backup(function_definition text NOT NULL);
INSERT INTO autopilot.migration_0337_function_backup
 SELECT pg_get_functiondef('autopilot.reconcile_role_dispatch_callbacks()'::regprocedure);

ALTER TABLE autopilot.role_dispatch_outbox DROP CONSTRAINT role_dispatch_delivery_contract_version_check;
ALTER TABLE autopilot.role_dispatch_outbox ADD CONSTRAINT role_dispatch_delivery_contract_version_check
 CHECK(delivery_contract_version IN (1,2,3,4));

-- Unknown native submissions must retain their slot. Never send a second
-- cloud task or release the slot because the bridge's response was lost.
DO $$ DECLARE original text; revised text; BEGIN
 SELECT function_definition INTO original FROM autopilot.migration_0337_function_backup;
 revised := replace(original,
  'WHERE (status=''PUBLISHED'' AND delivery_deadline_at<=now())',
  'WHERE (delivery_contract_version<>4 AND status=''PUBLISHED'' AND delivery_deadline_at<=now())');
 revised := replace(revised,
  'OR (status=''SENT'' AND callback_deadline_at<=now())',
  'OR (delivery_contract_version<>4 AND status=''SENT'' AND callback_deadline_at<=now())');
 IF revised=original OR position('delivery_contract_version<>4 AND status=''SENT''' in revised)=0 THEN
  RAISE EXCEPTION 'CLI_RECONCILER_SHAPE_CHANGED';
 END IF;
 EXECUTE revised;
END $$;

CREATE FUNCTION autopilot.reserve_codex_cli(p_dispatch_id uuid,p_assignment jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,autopilot AS $$
DECLARE o autopilot.role_dispatch_outbox; a jsonb; r autopilot.codex_cli_dispatch;
BEGIN
 SELECT * INTO o FROM autopilot.role_dispatch_outbox WHERE dispatch_id=p_dispatch_id FOR UPDATE;
 SELECT * INTO r FROM autopilot.codex_cli_dispatch WHERE dispatch_id=p_dispatch_id;
 IF FOUND THEN
  IF r.assignment IS DISTINCT FROM p_assignment THEN RAISE EXCEPTION 'CLI_ASSIGNMENT_CONFLICT'; END IF;
  RETURN to_jsonb(r);
 END IF;
 SELECT to_jsonb(x) INTO a FROM autopilot.get_dispatch_assignment(p_dispatch_id) x;
 IF a IS NULL OR a IS DISTINCT FROM p_assignment OR o.dispatch_id IS NULL
    OR o.status IS DISTINCT FROM 'PUBLISHED' OR o.delivery_contract_version IS DISTINCT FROM 3
    OR o.delivery_deadline_at<=clock_timestamp() OR o.github_dispatch_comment_id IS NULL
    OR NOT EXISTS(SELECT FROM autopilot.codex_cli_config WHERE enabled AND o.published_at>=cutover_at)
    OR NOT EXISTS(SELECT FROM autopilot.task WHERE task_id=o.task_id AND status='WAITING_EXTERNAL')
    OR NOT EXISTS(SELECT FROM autopilot.project_work_task m JOIN autopilot.project_work_item w USING(work_item_id)
       WHERE m.task_id=o.task_id AND w.state='ACTIVE' AND w.last_task_id=o.task_id) THEN
  RAISE EXCEPTION 'CLI_RESERVATION_INVALID';
 END IF;
 INSERT INTO autopilot.codex_cli_dispatch(dispatch_id,assignment) VALUES(p_dispatch_id,a) RETURNING * INTO r;
 UPDATE autopilot.role_dispatch_outbox SET delivery_contract_version=4,updated_at=now() WHERE dispatch_id=p_dispatch_id;
 RETURN to_jsonb(r);
END $$;

CREATE FUNCTION autopilot.ack_codex_cli(p_dispatch_id uuid,p_reservation_id uuid,p_task_id text,p_prompt_sha256 text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,autopilot AS $$
DECLARE o autopilot.role_dispatch_outbox; r autopilot.codex_cli_dispatch; a jsonb;
BEGIN
 SELECT * INTO o FROM autopilot.role_dispatch_outbox WHERE dispatch_id=p_dispatch_id FOR UPDATE;
 SELECT * INTO r FROM autopilot.codex_cli_dispatch WHERE dispatch_id=p_dispatch_id FOR UPDATE;
 IF r.dispatch_id IS NULL OR r.reservation_id IS DISTINCT FROM p_reservation_id
    OR COALESCE(p_task_id,'') !~ '^task_[A-Za-z0-9_]{1,120}$'
    OR COALESCE(p_prompt_sha256,'') !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'CLI_ACK_INVALID'; END IF;
 IF r.state<>'RESERVED' THEN
  IF r.provider_task_id IS DISTINCT FROM p_task_id OR r.prompt_sha256 IS DISTINCT FROM p_prompt_sha256 THEN
   RAISE EXCEPTION 'CLI_ACK_CONFLICT';
  END IF;
  RETURN to_jsonb(r);
 END IF;
 SELECT to_jsonb(x) INTO a FROM autopilot.get_dispatch_assignment(p_dispatch_id) x;
 IF a IS DISTINCT FROM r.assignment OR o.delivery_contract_version IS DISTINCT FROM 4
    OR o.status IS DISTINCT FROM 'PUBLISHED' THEN RAISE EXCEPTION 'CLI_ACK_BINDING_INVALID'; END IF;
 UPDATE autopilot.codex_cli_dispatch SET state='SUBMITTED',provider_task_id=p_task_id,
  prompt_sha256=p_prompt_sha256,submitted_at=clock_timestamp() WHERE dispatch_id=p_dispatch_id RETURNING * INTO r;
 UPDATE autopilot.role_dispatch_outbox SET status='SENT',sent_at=r.submitted_at,delivered_at=r.submitted_at,
  callback_deadline_at=r.submitted_at+interval '2 hours',updated_at=now() WHERE dispatch_id=p_dispatch_id;
 RETURN to_jsonb(r);
END $$;

-- The trusted event bridge obtains the report by the task ID retained above.
-- No untrusted bot/comment or runtime principal can call these owner-only RPCs.
CREATE FUNCTION autopilot.finish_codex_cli(p_dispatch_id uuid,p_reservation_id uuid,p_task_id text,
 p_report_sha256 text,p_result jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,autopilot AS $$
DECLARE o autopilot.role_dispatch_outbox; r autopilot.codex_cli_dispatch; a jsonb; new_state text;
BEGIN
 SELECT * INTO o FROM autopilot.role_dispatch_outbox WHERE dispatch_id=p_dispatch_id FOR UPDATE;
 SELECT * INTO r FROM autopilot.codex_cli_dispatch WHERE dispatch_id=p_dispatch_id FOR UPDATE;
 IF r.dispatch_id IS NULL OR r.reservation_id IS DISTINCT FROM p_reservation_id
    OR r.provider_task_id IS DISTINCT FROM p_task_id
    OR COALESCE(p_report_sha256,'') !~ '^[0-9a-f]{64}$'
    OR jsonb_typeof(p_result) IS DISTINCT FROM 'object'
    OR octet_length(p_result::text)>4096
    OR COALESCE(p_result->>'status','') NOT IN ('SUCCEEDED','BLOCKED')
    OR COALESCE(p_result->>'result_code','') !~ '^[A-Z][A-Z0-9_]{0,63}$'
    OR COALESCE(p_result->>'target_head_sha','') !~ '^[0-9a-f]{40}$'
    OR COALESCE(length(p_result->>'summary'),0) NOT BETWEEN 1 AND 160 THEN
  RAISE EXCEPTION 'CLI_TERMINAL_INVALID';
 END IF;
 IF r.state='TERMINAL' THEN
  IF r.report_sha256 IS DISTINCT FROM p_report_sha256 OR r.terminal IS DISTINCT FROM p_result THEN
   RAISE EXCEPTION 'CLI_TERMINAL_CONFLICT';
  END IF;
  RETURN to_jsonb(r);
 END IF;
 SELECT to_jsonb(x) INTO a FROM autopilot.get_dispatch_assignment(p_dispatch_id) x;
 IF a IS DISTINCT FROM r.assignment OR r.state<>'SUBMITTED'
    OR o.status IS DISTINCT FROM 'SENT' OR o.delivery_contract_version IS DISTINCT FROM 4
    OR NOT EXISTS(SELECT FROM autopilot.task WHERE task_id=o.task_id AND status='WAITING_EXTERNAL')
    OR NOT EXISTS(SELECT FROM autopilot.project_work_task m JOIN autopilot.project_work_item w USING(work_item_id)
       WHERE m.task_id=o.task_id AND w.state='ACTIVE' AND w.last_task_id=o.task_id)
    OR (p_result->>'status'='SUCCEEDED' AND o.mode IN ('READ_ONLY','VERIFY')
        AND p_result->>'target_head_sha' IS DISTINCT FROM o.expected_head_sha)
    OR (p_result->>'status'='SUCCEEDED' AND o.mode='REPAIR'
        AND (p_result->>'published_parent_sha' IS DISTINCT FROM o.expected_head_sha
          OR p_result->>'target_head_sha'=o.expected_head_sha
          OR COALESCE(p_result->>'publication_tree_sha','') !~ '^[0-9a-f]{40}$')) THEN
  RAISE EXCEPTION 'CLI_TERMINAL_BINDING_INVALID';
 END IF;
 INSERT INTO autopilot.evidence(task_id,step_attempt_id,evidence_class,provider,external_ref,content_sha256,metadata_json)
 VALUES(o.task_id,o.step_attempt_id,'CHATGPT_ROLE_DISPATCH_RESULT','ORACLE_RESIDENT',
  'codex-cli:'||p_task_id,p_report_sha256,jsonb_build_object('dispatch_id',p_dispatch_id,
  'reservation_id',p_reservation_id,'provider_task_id',p_task_id,'prompt_sha256',r.prompt_sha256,'result',p_result));
 new_state:=CASE WHEN p_result->>'status'='SUCCEEDED' THEN 'DONE' ELSE 'FAILED_CLOSED' END;
 UPDATE autopilot.step_attempt SET status=CASE WHEN new_state='DONE' THEN 'COMPLETED' ELSE 'FAILED_CLOSED' END,
  result_summary_json=p_result,completed_at=now() WHERE step_attempt_id=o.step_attempt_id AND status='WAITING_EXTERNAL';
 UPDATE autopilot.task SET status=new_state,terminal_reason_code=p_result->>'result_code',safe_summary_json=p_result,
  completed_at=now() WHERE task_id=o.task_id;
 UPDATE autopilot.role_dispatch_outbox SET status='CALLBACK_ACCEPTED',completed_at=now(),updated_at=now() WHERE dispatch_id=p_dispatch_id;
 UPDATE autopilot.codex_cli_dispatch SET state='TERMINAL',report_sha256=p_report_sha256,terminal=p_result,
  completed_at=now() WHERE dispatch_id=p_dispatch_id RETURNING * INTO r;
 PERFORM autopilot.record_event(o.task_id,CASE WHEN new_state='DONE' THEN 'TASK_DONE' ELSE 'TASK_FAILED_CLOSED' END,
  'WAITING_EXTERNAL',new_state,jsonb_build_object('dispatch_id',p_dispatch_id,'provider_task_id',p_task_id),
  'EXTERNAL_EVENT','SLAVIK_CLI','codex-cli-terminal:'||p_dispatch_id::text);
 PERFORM pg_notify('autopilot_ready','codex-cli-terminal');
 RETURN to_jsonb(r);
END $$;

REVOKE ALL ON TABLE autopilot.codex_cli_config,autopilot.codex_cli_dispatch,autopilot.migration_0337_function_backup
 FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;
REVOKE ALL ON FUNCTION autopilot.reserve_codex_cli(uuid,jsonb),autopilot.ack_codex_cli(uuid,uuid,text,text),
 autopilot.finish_codex_cli(uuid,uuid,text,text,jsonb) FROM PUBLIC,autopilot_runtime,autopilot_runtime_principal,autopilot_callback;
INSERT INTO public.schema_migration(migration_key) VALUES('0337_autopilot_cli_transport');
COMMIT;
```

