\set ON_ERROR_STOP on
BEGIN;
DO $t$
BEGIN
 IF autopilot.blocker_remediation_action('QA_GATES_FAILED')<>'REPOSITORY_REPAIR'
 OR NOT autopilot.blocker_repository_repair_allowed('QA_GATES_FAILED') THEN
  RAISE EXCEPTION 'AUTOPILOT_0348_CODE_REPAIR_CLASS_INVALID';
 END IF;
 IF autopilot.blocker_remediation_action('SERVER_EVIDENCE_INCOMPLETE')<>'EVIDENCE_REMEDIATION'
 OR autopilot.blocker_repository_repair_allowed('SERVER_EVIDENCE_INCOMPLETE') THEN
  RAISE EXCEPTION 'AUTOPILOT_0348_EVIDENCE_CLASS_INVALID';
 END IF;
 IF autopilot.blocker_remediation_action('CODEX_ACK_DEADLINE_EXCEEDED')<>'PROVIDER_HOLD'
 OR autopilot.blocker_repository_repair_allowed('CODEX_ACK_DEADLINE_EXCEEDED') THEN
  RAISE EXCEPTION 'AUTOPILOT_0348_PROVIDER_CLASS_INVALID';
 END IF;
 IF autopilot.blocker_repository_repair_allowed('OWNER_APPROVAL_REQUIRED') THEN
  RAISE EXCEPTION 'AUTOPILOT_0348_OWNER_REPAIR_INVALID';
 END IF;
 IF autopilot.blocker_remediation_action('SOMETHING_NEW_AND_UNKNOWN')<>'HOLD_UNKNOWN'
 OR autopilot.blocker_repository_repair_allowed('SOMETHING_NEW_AND_UNKNOWN') THEN
  RAISE EXCEPTION 'AUTOPILOT_0348_UNKNOWN_NOT_FAIL_CLOSED';
 END IF;
 IF strpos(pg_get_functiondef('autopilot.materialize_role_repair(uuid,text,text)'::regprocedure),'BLOCKER_REMEDIATION_ADMISSION_V1')=0 THEN
  RAISE EXCEPTION 'AUTOPILOT_0348_REPAIR_GATE_NOT_INSTALLED';
 END IF;
END $t$;
ROLLBACK;
