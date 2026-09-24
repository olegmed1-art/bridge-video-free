"""Emit one rollback-only rehearsal statement; original branch fence is retained."""
from pathlib import Path


def query():
    source=(Path(__file__).parent/'light_dispatch_1867_journal_candidate.sql').read_text()
    # Isolate the test's object names; do not overwrite the consumed durable journal.
    source=source.replace('light_dispatch_1867_', 'light_dispatch_1867_trial_')
    source=source.replace('light-dispatch-1867-journal', 'light-dispatch-1867-trial')
    literal="'"+source.replace("'","''")+"'"
    return """DO $trial$
DECLARE before_image jsonb; after_image jsonb; r jsonb; b jsonb; journals bigint;
BEGIN
 IF current_setting('neon.branch_id',true) IS DISTINCT FROM 'br-holy-term-b1k8s9qe' THEN
  RAISE EXCEPTION 'TRIAL_REHEARSAL_BRANCH_REQUIRED';
 END IF;
 SELECT count(*) INTO journals FROM autopilot.light_dispatch_1867_journal;
 before_image:=autopilot.light_dispatch_1867_snapshot();
 BEGIN
  EXECUTE """+literal+""";
  r:=autopilot.light_dispatch_1867_trial_recover('APPLY','{"scope":"ISOLATED_REHEARSAL"}');
  IF r->>'task_state'<>'WAITING_EXTERNAL' OR r->>'outbox_state'<>'PUBLISHED' OR r->>'attempts'<>'5' THEN
   RAISE EXCEPTION 'TRIAL_APPLY_FAILED'; END IF;
  BEGIN
   PERFORM autopilot.light_dispatch_1867_trial_recover('APPLY','{"scope":"ISOLATED_REHEARSAL"}');
   RAISE EXCEPTION 'TRIAL_DUPLICATE_NOT_REJECTED';
  EXCEPTION WHEN raise_exception THEN
   IF SQLERRM<>'RECOVERY_CANDIDATE_ACTION_ALREADY_USED' THEN RAISE; END IF;
  END;
  BEGIN
   b:=autopilot.codex_command_send_binding('322dd440-30b9-49d2-8e1a-f5ecc1d2b99b');
   IF NOT autopilot.claim_codex_command_send('322dd440-30b9-49d2-8e1a-f5ecc1d2b99b',
    '0f000000-0000-4000-8000-000000001867',b,repeat('0',64)) THEN RAISE EXCEPTION 'TRIAL_CLAIM_FAILED'; END IF;
   IF autopilot.claim_codex_command_send('322dd440-30b9-49d2-8e1a-f5ecc1d2b99b',
    '0f000000-0000-4000-8000-000000001867',b,repeat('0',64)) THEN RAISE EXCEPTION 'TRIAL_SECOND_CLAIM'; END IF;
   PERFORM autopilot.light_dispatch_1867_trial_recover('ROLLBACK','{"scope":"ISOLATED_REHEARSAL"}');
   RAISE EXCEPTION 'TRIAL_RECEIPT_NOT_REJECTED';
  EXCEPTION WHEN raise_exception THEN
   IF SQLERRM<>'RECOVERY_CANDIDATE_RECEIPT_EXISTS' THEN RAISE; END IF;
  END;
  r:=autopilot.light_dispatch_1867_trial_recover('ROLLBACK','{"scope":"ISOLATED_REHEARSAL"}');
  after_image:=autopilot.light_dispatch_1867_trial_snapshot();
  IF r->>'task_state'<>'FAILED_CLOSED' OR r->>'outbox_state'<>'FAILED_CLOSED'
   OR (before_image #- '{task,updated_at}' #- '{outbox,updated_at}' #- '{work,updated_at}') IS DISTINCT FROM
      (after_image #- '{task,updated_at}' #- '{outbox,updated_at}' #- '{work,updated_at}') THEN RAISE EXCEPTION 'TRIAL_ROLLBACK_FAILED keys=%', (SELECT jsonb_agg(entity||'.'||key) FROM jsonb_object_keys(before_image) entity CROSS JOIN LATERAL jsonb_object_keys(before_image->entity) key WHERE before_image->entity->key IS DISTINCT FROM after_image->entity->key); END IF;
  IF (SELECT count(*) FROM autopilot.light_dispatch_1867_trial_journal)<>2 THEN RAISE EXCEPTION 'TRIAL_JOURNAL_FAILED'; END IF;
  RAISE EXCEPTION USING ERRCODE='Z1867',MESSAGE='TRIAL_SUCCESS_ROLLBACK';
 EXCEPTION WHEN SQLSTATE 'Z1867' THEN NULL;
 END;
 IF autopilot.light_dispatch_1867_snapshot() IS DISTINCT FROM before_image
  OR (SELECT count(*) FROM autopilot.light_dispatch_1867_journal)<>journals
  OR to_regclass('autopilot.light_dispatch_1867_trial_journal') IS NOT NULL
  OR EXISTS(SELECT FROM autopilot.codex_command_send_intent WHERE dispatch_id='322dd440-30b9-49d2-8e1a-f5ecc1d2b99b') THEN
  RAISE EXCEPTION 'TRIAL_OUTER_ROLLBACK_FAILED'; END IF;
 RAISE NOTICE 'TRIAL_PASS_APPLY_DUPLICATE_SEND_FENCE_ROLLBACK_NO_PERSISTENT_CHANGE';
END $trial$;"""


if __name__=='__main__':
    print(query())
