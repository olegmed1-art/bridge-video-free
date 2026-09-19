\set ON_ERROR_STOP on
BEGIN;
DO $t$
BEGIN
 IF autopilot.blocker_remediation_action('SERVER_EVIDENCE_INCOMPLETE')<>'EVIDENCE_REMEDIATION'
 OR autopilot.blocker_remediation_action('TARGET_SUPERSEDED_BY_CURRENT_MAIN')<>'RECONCILE_TARGET'
 OR autopilot.blocker_remediation_action('CODEX_ACK_DEADLINE_EXCEEDED')<>'PROVIDER_HOLD'
 THEN RAISE EXCEPTION 'AUTOPILOT_0349_CLASSIFIER_PREREQUISITE_INVALID'; END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='autopilot.role_dispatch_followup'::regclass
   AND conname='role_dispatch_followup_followup_kind_check'
   AND pg_get_constraintdef(oid) LIKE '%EVIDENCE%' AND pg_get_constraintdef(oid) LIKE '%RECONCILE%')
 THEN RAISE EXCEPTION 'AUTOPILOT_0349_FOLLOWUP_KINDS_INVALID'; END IF;
 IF to_regprocedure('autopilot.materialize_blocker_remediation(uuid,text,text)') IS NULL
 THEN RAISE EXCEPTION 'AUTOPILOT_0349_MATERIALIZER_MISSING'; END IF;
END $t$;
ROLLBACK;
