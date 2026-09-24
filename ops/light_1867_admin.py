"""Bounded owner recovery for the existing dispatch; never a sender or service start.

The reviewed rehearsal candidate remains untouched. This module builds a separate
administrative namespace with fresh external evidence and exact-state CAS guards.
Run the emitted installer + APPLY in ONE transaction. Lost responses mean readback.
"""
import base64
import hashlib
import json
from pathlib import Path

CANDIDATE_SHA = 'df95e1bcf3c38b186b6dd4bdbc053dff505b76548f811ad5140e08bd1a44d1f4'
PRODUCTION = 'br-wispy-lab-b1rq54of'
CHILD = 'br-holy-term-b1k8s9qe'
DECISION = 'https://github.com/olegmed1-art/bridge-video-free/pull/1769#issuecomment-5806406211'
INSTALLED = '5eb0e1bb2c2932bd8d02ff187b9cf24f6bc09c7c'
HEAD = '2586929313ab40326d64353b513ff86e5ae3350c'
SCOPE = 'LIGHT_1867_ONE_SHOT_V1'


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('CANDIDATE_STRUCTURE_DRIFT')
    return source.replace(old, new, 1)


def installer():
    source = (Path(__file__).with_name('light_dispatch_1867_journal_candidate.sql')).read_text()
    if hashlib.sha256(source.encode()).hexdigest() != CANDIDATE_SHA:
        raise ValueError('CANDIDATE_SOURCE_DRIFT')
    source = source.replace('light_dispatch_1867_', 'light_1867_admin_')
    source = source.replace('light-dispatch-1867-journal', 'light-1867-admin-journal')
    source = source.replace('journal-recovery-rehearsal', 'light-1867-admin')
    source = source.replace('isolated-recovery-rehearsal', 'light-1867-admin')
    source = source.replace('RECOVERY_CANDIDATE_', 'LIGHT_1867_ADMIN_')
    source = replace_once(source, "IF current_setting('neon.branch_id',true) IS DISTINCT FROM '"+CHILD+"' THEN",
        "IF current_setting('neon.branch_id',true) NOT IN ('"+PRODUCTION+"','"+CHILD+"')\n"
        " OR current_setting('neon.branch_id',true) IS NULL\n"
        " OR current_user <> 'neondb_owner' OR current_database() <> 'neondb' THEN")
    source = replace_once(source, "IF current_setting('neon.branch_id',true) IS DISTINCT FROM '"+CHILD+"'\n OR current_user",
        "IF current_setting('neon.branch_id',true) NOT IN ('"+PRODUCTION+"','"+CHILD+"')\n"
        " OR current_setting('neon.branch_id',true) IS NULL\n"
        " OR current_user <> 'neondb_owner' OR current_database() <> 'neondb'\n OR current_user")
    source = replace_once(source, " OR p_evidence->>'scope' IS DISTINCT FROM 'ISOLATED_REHEARSAL'",
        " OR p_evidence->>'scope' IS DISTINCT FROM '"+SCOPE+"'")
    guard = """
 -- External facts are supplied by the reviewed coordinator, not inferred by SQL.
 -- Their complete evidence is retained in the append-only journal. A timestamp
 -- or this validator alone is not permission to send.
 IF NOT COALESCE(p_evidence @> '{"controller_ready":true,
   "decision_url":"DECISION", "installed_revision":"INSTALLED",
   "target_head":"HEAD", "dispatch_pr":1867,
   "dispatch_pr_head":"cfee158e83b0bcd76a676d356c39ab2cb349678a",
   "dispatch_body_sha256":"331fcde86d72d5195064dd1e9263596147419980b7ef9035a7ebcf27da09cc5f",
   "diagnostics_match":true,"complete_comments":true,
   "host_hold":true,"host_pid":0,"mailbox_pr":1703,"route":"neon_epoch_0"}'::jsonb,false)
 OR p_evidence->>'branch' IS DISTINCT FROM current_setting('neon.branch_id',true)
 OR p_evidence->>'mode' IS DISTINCT FROM (CASE WHEN current_setting('neon.branch_id',true)='{{PRODUCTION}}'
    THEN 'PRODUCTION' ELSE 'REHEARSAL' END)
 OR NOT COALESCE((p_evidence->>'snapshot_sha256') ~ '^[0-9a-f]{64}$',false)
 OR NOT COALESCE((p_evidence->>'comments_sha256') ~ '^[0-9a-f]{64}$',false)
 OR NOT COALESCE((p_evidence->>'admin_revision') ~ '^[0-9a-f]{40}$',false)
 OR NOT COALESCE((p_evidence->>'host_run_id') ~ '^[1-9][0-9]+$',false)
 OR NOT COALESCE((p_evidence->>'captured_at')::timestamptz
       BETWEEN clock_timestamp()-interval '120 seconds' AND clock_timestamp()+interval '5 seconds',false)
 OR NOT COALESCE((p_evidence->>'host_attested_at')::timestamptz
       BETWEEN clock_timestamp()-interval '5 minutes' AND clock_timestamp()+interval '5 seconds',false)
 THEN RAISE EXCEPTION 'LIGHT_1867_ADMIN_EVIDENCE_INVALID'; END IF;
""".replace('DECISION',DECISION).replace('INSTALLED',INSTALLED).replace('HEAD',HEAD).replace('{{PRODUCTION}}',PRODUCTION)
    source = replace_once(source, " PERFORM pg_advisory_xact_lock(hashtextextended('light-1867-admin-journal',0));",
        guard+" PERFORM pg_advisory_xact_lock(hashtextextended('light-1867-admin-journal',0));")
    source = replace_once(source, " IF p_action='APPLY' THEN", """
 IF encode(sha256(convert_to(current_image::text,'UTF8')),'hex')
     IS DISTINCT FROM p_evidence->>'snapshot_sha256' THEN
  RAISE EXCEPTION 'LIGHT_1867_ADMIN_SNAPSHOT_DRIFT'; END IF;
 IF p_action='APPLY' THEN
  IF EXISTS(SELECT FROM autopilot.task WHERE task_id<>tid AND status IN
    ('NEW','VALIDATING','READY','RUNNING','WAITING_EXTERNAL','EVALUATING')) THEN
   RAISE EXCEPTION 'LIGHT_1867_ADMIN_UNEXPECTED_ACTIVE_TASK'; END IF;""")
    source = source[source.index('DO $$ BEGIN'):]
    return '-- Separate approved one-shot admin wrapper; candidate is unchanged.\n'+source


def call_sql(action, evidence):
    if action not in ('APPLY', 'ROLLBACK'):
        raise ValueError('ACTION_INVALID')
    encoded = base64.b64encode(json.dumps(evidence, sort_keys=True, separators=(',', ':'),
                                         allow_nan=False).encode()).decode()
    return ("SELECT autopilot.light_1867_admin_recover('"+action+"',convert_from(decode('"+
            encoded+"','base64'),'UTF8')::jsonb)")


def snapshot_sql():
    # Works before the installer exists; exactly the same full row snapshot.
    source = installer()
    body = source.split('CREATE FUNCTION autopilot.light_1867_admin_snapshot()',1)[1]
    query = body.split('AS $$',1)[1].split('$$;',1)[0]
    return ("WITH s AS ("+query+") SELECT current_setting('neon.branch_id',true) AS branch, "
            "clock_timestamp() AS captured_at, encode(sha256(convert_to(s.jsonb_build_object::text,'UTF8')),'hex') "
            "AS snapshot_sha256, s.jsonb_build_object AS snapshot FROM s")


def rehearsal():
    """Exercise the exact generated installer in the existing child; rollback ALL."""
    quoted = "'"+installer().replace("'", "''")+"'"
    fixture = dict(scope=SCOPE, mode='REHEARSAL', branch=CHILD, controller_ready=True,
        decision_url=DECISION, installed_revision=INSTALLED, target_head=HEAD,
        dispatch_pr=1867, dispatch_pr_head='cfee158e83b0bcd76a676d356c39ab2cb349678a',
        dispatch_body_sha256='331fcde86d72d5195064dd1e9263596147419980b7ef9035a7ebcf27da09cc5f',
        diagnostics_match=True, complete_comments=True, host_hold=True, host_pid=0,
        mailbox_pr=1703, route='neon_epoch_0', admin_revision='0'*40,
        comments_sha256='0'*64, host_run_id='12345')
    f = "'"+json.dumps(fixture,separators=(',',':'))+"'::jsonb"
    return """DO $rehearsal$
DECLARE before_image jsonb; after_image jsonb; ev jsonb; bad jsonb; result jsonb; binding jsonb; i int;
BEGIN
 IF current_setting('neon.branch_id',true) IS DISTINCT FROM 'CHILD' THEN
  RAISE EXCEPTION 'REHEARSAL_CHILD_ONLY'; END IF;
 before_image := autopilot.light_dispatch_1867_snapshot();
 BEGIN
  EXECUTE INSTALLER;
  ev := FIXTURE || jsonb_build_object('captured_at',clock_timestamp(),'host_attested_at',clock_timestamp(),
    'snapshot_sha256',encode(sha256(convert_to(before_image::text,'UTF8')),'hex'));
  FOR i IN 1..7 LOOP
   bad := CASE i
    WHEN 1 THEN ev - 'controller_ready'
    WHEN 2 THEN ev || '{"host_pid":1}'::jsonb
    WHEN 3 THEN ev || '{"snapshot_sha256":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"}'::jsonb
    WHEN 4 THEN ev || '{"mode":"PRODUCTION"}'::jsonb
    WHEN 5 THEN ev || '{"captured_at":"2000-01-01T00:00:00Z"}'::jsonb
    WHEN 6 THEN ev || '{"target_head":"0000000000000000000000000000000000000000"}'::jsonb
    WHEN 7 THEN ev || '{"host_attested_at":"2000-01-01T00:00:00Z"}'::jsonb END;
   BEGIN
    PERFORM autopilot.light_1867_admin_recover('APPLY',bad);
    RAISE EXCEPTION 'NEGATIVE_CASE_ACCEPTED';
   EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT IN ('LIGHT_1867_ADMIN_EVIDENCE_INVALID','LIGHT_1867_ADMIN_SNAPSHOT_DRIFT') THEN RAISE; END IF;
   END;
  END LOOP;
  result := autopilot.light_1867_admin_recover('APPLY',ev);
  IF result->>'task_state'<>'WAITING_EXTERNAL' OR result->>'outbox_state'<>'PUBLISHED'
     OR result->>'attempts'<>'5' THEN RAISE EXCEPTION 'APPLY_STATE_INVALID'; END IF;
  BEGIN
   PERFORM autopilot.light_1867_admin_recover('APPLY',ev);
   RAISE EXCEPTION 'DUPLICATE_APPLY_ACCEPTED';
  EXCEPTION WHEN raise_exception THEN
   IF SQLERRM<>'LIGHT_1867_ADMIN_ACTION_ALREADY_USED' THEN RAISE; END IF;
  END;
  ev := ev || jsonb_build_object('snapshot_sha256',encode(sha256(convert_to(
     autopilot.light_1867_admin_snapshot()::text,'UTF8')),'hex'));
  BEGIN
   binding := autopilot.codex_command_send_binding('322dd440-30b9-49d2-8e1a-f5ecc1d2b99b');
   IF NOT autopilot.claim_codex_command_send('322dd440-30b9-49d2-8e1a-f5ecc1d2b99b',
     '0f000000-0000-4000-8000-000000001874',binding,repeat('0',64)) THEN RAISE EXCEPTION 'FIRST_CLAIM_FAILED'; END IF;
   IF autopilot.claim_codex_command_send('322dd440-30b9-49d2-8e1a-f5ecc1d2b99b',
     '0f000000-0000-4000-8000-000000001874',binding,repeat('0',64)) THEN RAISE EXCEPTION 'SECOND_CLAIM_ACCEPTED'; END IF;
   PERFORM autopilot.light_1867_admin_recover('ROLLBACK',ev);
   RAISE EXCEPTION 'POST_INTENT_ROLLBACK_ACCEPTED';
  EXCEPTION WHEN raise_exception THEN
   IF SQLERRM<>'LIGHT_1867_ADMIN_RECEIPT_EXISTS' THEN RAISE; END IF;
  END;
  PERFORM autopilot.light_1867_admin_recover('ROLLBACK',ev);
  after_image := autopilot.light_1867_admin_snapshot();
  IF (before_image #- '{task,updated_at}' #- '{outbox,updated_at}' #- '{work,updated_at}') IS DISTINCT FROM
     (after_image #- '{task,updated_at}' #- '{outbox,updated_at}' #- '{work,updated_at}')
    OR (SELECT count(*) FROM autopilot.light_1867_admin_journal)<>2 THEN
   RAISE EXCEPTION 'ROLLBACK_IMAGE_INVALID'; END IF;
  BEGIN
   UPDATE autopilot.light_1867_admin_journal SET evidence='{}';
   RAISE EXCEPTION 'JOURNAL_MUTABLE';
  EXCEPTION WHEN raise_exception THEN
   IF SQLERRM<>'AUTOPILOT_APPEND_ONLY' THEN RAISE; END IF;
  END;
  IF has_function_privilege('autopilot_runtime','autopilot.light_1867_admin_recover(text,jsonb)','EXECUTE')
   OR has_function_privilege('autopilot_callback','autopilot.light_1867_admin_recover(text,jsonb)','EXECUTE') THEN
   RAISE EXCEPTION 'RUNTIME_ADMIN_ACCESS'; END IF;
  RAISE EXCEPTION USING ERRCODE='Z1867',MESSAGE='SUCCESS_OUTER_ROLLBACK';
 EXCEPTION WHEN SQLSTATE 'Z1867' THEN NULL;
 END;
 IF autopilot.light_dispatch_1867_snapshot() IS DISTINCT FROM before_image
  OR to_regclass('autopilot.light_1867_admin_journal') IS NOT NULL
  OR EXISTS(SELECT FROM autopilot.codex_command_send_intent WHERE dispatch_id='322dd440-30b9-49d2-8e1a-f5ecc1d2b99b') THEN
  RAISE EXCEPTION 'OUTER_ROLLBACK_INVALID'; END IF;
 RAISE NOTICE 'LIGHT_1867_ADMIN_REHEARSAL_PASS';
END $rehearsal$;""".replace('CHILD',CHILD).replace('INSTALLER',quoted).replace('FIXTURE',f)


if __name__ == '__main__':
    import sys
    commands = {'installer':installer, 'rehearsal':rehearsal, 'snapshot':snapshot_sql}
    if len(sys.argv)!=2 or sys.argv[1] not in commands:
        raise SystemExit('usage: light_1867_admin.py installer|rehearsal|snapshot')
    print(commands[sys.argv[1]]())
