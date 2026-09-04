from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / "database/migrations/0323_workflow_video_canon_promotion_consumer.sql").read_text()
ROLLBACK = (ROOT / "database/rollbacks/0323_workflow_video_canon_promotion_consumer.sql").read_text()


def test_authority_and_independent_assurance_are_fail_closed():
    assert "authority_class' IS DISTINCT FROM 'TEACHER_VIDEO'" in MIGRATION
    assert "source_class' IS DISTINCT FROM 'SCHOOL_PRIMARY_EVIDENCE'" in MIGRATION
    assert MIGRATION.count("i2.verifier_family<>i3.verifier_family") == 3
    assert MIGRATION.count("i2.execution_principal<>i3.execution_principal") == 3
    assert "i2_attestor.rolname=i2.execution_principal" in MIGRATION
    assert "i3_attestor.rolname=i3.execution_principal" in MIGRATION
    assert MIGRATION.count("attestor.rolcanlogin") == 6
    assert "i2_attestor.oid,'bridge_school_canon_i2_verifier','MEMBER'" in MIGRATION
    assert "i3_attestor.oid,'bridge_school_canon_i3_verifier','MEMBER'" in MIGRATION
    assert "video_canon_assurance_verifier_registry" in MIGRATION
    assert "video_canon_assurance_bound_bundle" in MIGRATION
    assert "a.video_canon_assurance_assignment_id,a.assurance_level" in MIGRATION
    assert "video_canon_assurance_set_sha256" in MIGRATION
    assert "reassign_video_canon_assurance" in MIGRATION
    assert "supersession_reason_sha256" in MIGRATION
    assert MIGRATION.count("i2.canon_snapshot_sha256=i3.canon_snapshot_sha256") == 3
    post_activation = MIGRATION.split("v_promotion:=bidding.activate_ai_verified_video_canon(", 1)[1]
    assert "i2_attestor.rolcanlogin" in post_activation
    assert "i3_attestor.rolcanlogin" in post_activation
    assert "VIDEO_CANON_I2_I3_REVOKED_DURING_PROMOTION" in post_activation
    assert "FROM bidding.video_canon_ai_verification v" in post_activation
    assert "LEFT JOIN bidding.video_canon_verifier_registry vr" in post_activation
    assert "v.check_id=ANY(vr.allowed_check_ids)" in post_activation
    assert "capability.rolname=vr.database_role" in post_activation
    assert "pg_has_role(attestor.oid,capability.oid,'MEMBER')" in post_activation
    assert "VIDEO_CANON_BASE_VERIFIERS_REVOKED_DURING_PROMOTION" in post_activation
    assert "VIDEO_CANON_ASSURANCE_SET_CHANGED_DURING_PROMOTION" in post_activation
    assert "WORLD" not in MIGRATION


def test_delivery_is_leased_fenced_atomic_and_retained():
    assert "FOR UPDATE SKIP LOCKED" in MIGRATION
    assert "j.fencing_token+1" in MIGRATION
    assert "heartbeat_video_canon_promotion" in MIGRATION
    assert "lease_expires_at=v_now+make_interval(secs=>p_lease_seconds)" in MIGRATION
    assert "AND lease_owner IS NOT DISTINCT FROM session_user" in MIGRATION
    assert "AND lease_token IS NOT DISTINCT FROM p_lease_token" in MIGRATION
    assert "AND fencing_token IS NOT DISTINCT FROM p_fencing_token" in MIGRATION
    assert "AND lease_expires_at>v_now" in MIGRATION
    assert "v_job.fencing_token IS DISTINCT FROM p_fencing_token" in MIGRATION
    assert MIGRATION.count("v_job.lease_expires_at<=clock_timestamp()") == 2
    assert "VIDEO_CANON_POST_WRITE_INTEGRITY_FAILED" in MIGRATION
    assert "video_canon_promotion_delivery_receipt_append_only" in MIGRATION
    assert "ATTEMPTS_EXHAUSTED" in MIGRATION
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in MIGRATION
    assert "video_canon_assurance_assignment_id=i2.video_canon_assurance_assignment_id" in MIGRATION
    assert "terminal_error_code='STATE_STALE'" in MIGRATION
    assert "PERFORM 1 FROM bidding.video_canon_promotion_job" in MIGRATION
    assert "video_canon_ai_verification_bundle_id=v_old.video_canon_ai_verification_bundle_id\n   FOR UPDATE" in MIGRATION
    assert "v_existing.fencing_token IS DISTINCT FROM p_fencing_token" in MIGRATION
    assert MIGRATION.count("p_lease_token IS NULL OR p_fencing_token IS NULL") == 3
    assert MIGRATION.count("v_job.lease_token IS DISTINCT FROM p_lease_token") == 2
    assert "VIDEO_CANON_DELIVERY_RECEIPT_STALE" in MIGRATION
    assert "video_canon_ai_restore_receipt rr" in MIGRATION
    assert MIGRATION.count("valid_from<=v_now") == 4
    assert MIGRATION.count("valid_to>v_now") == 4
    assert "v_now := clock_timestamp();\n  IF NOT EXISTS (\n    SELECT 1 FROM bidding.video_canon_ai_promotion_receipt" in MIGRATION


def test_only_consumer_can_cross_authoritative_boundary():
    assert "REVOKE EXECUTE ON FUNCTION bidding.activate_ai_verified_video_canon" in MIGRATION
    assert "FROM bridge_school_canon_promoter" in MIGRATION
    assert "TO bridge_school_canon_consumer" in MIGRATION
    assert "bidding.heartbeat_video_canon_promotion(uuid,uuid,bigint,integer)" in MIGRATION
    assert "REVOKE INSERT,UPDATE,DELETE,TRUNCATE" in MIGRATION


def test_rollback_restores_pre_migration_boundary_and_registry():
    assert "VIDEO_CANON_0323_ROLLBACK_STATE_EXISTS" in ROLLBACK
    assert "video_canon_promotion_delivery_receipt" in ROLLBACK.split("REVOKE ALL", 1)[0]
    assert "GRANT EXECUTE ON FUNCTION bidding.activate_ai_verified_video_canon" in ROLLBACK
    assert "TO bridge_school_canon_promoter" in ROLLBACK
    assert "WHERE migration_key='0323_workflow_video_canon_promotion_consumer'" in ROLLBACK
    assert "DROP ROLE IF EXISTS bridge_school_canon_consumer" in ROLLBACK
    assert "DROP FUNCTION IF EXISTS bidding.heartbeat_video_canon_promotion" in ROLLBACK
