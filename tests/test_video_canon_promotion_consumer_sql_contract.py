from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / "database/migrations/0323_workflow_video_canon_promotion_consumer.sql").read_text()
ROLLBACK = (ROOT / "database/rollbacks/0323_workflow_video_canon_promotion_consumer.sql").read_text()


def test_authority_and_independent_assurance_are_fail_closed():
    assert "authority_class' IS DISTINCT FROM 'TEACHER_VIDEO'" in MIGRATION
    assert "source_class' IS DISTINCT FROM 'SCHOOL_PRIMARY_EVIDENCE'" in MIGRATION
    assert MIGRATION.count("i2.verifier_family<>i3.verifier_family") == 2
    assert MIGRATION.count("i2.execution_principal<>i3.execution_principal") == 2
    assert "pg_has_role(i2.execution_principal,'bridge_school_canon_i2_verifier','member')" in MIGRATION
    assert "pg_has_role(i3.execution_principal,'bridge_school_canon_i3_verifier','member')" in MIGRATION
    assert "video_canon_assurance_verifier_registry" in MIGRATION
    assert "video_canon_assurance_bound_bundle" in MIGRATION
    assert "video_canon_assurance_set_sha256" in MIGRATION
    assert "reassign_video_canon_assurance" in MIGRATION
    assert "supersession_reason_sha256" in MIGRATION
    assert MIGRATION.count("i2.canon_snapshot_sha256=i3.canon_snapshot_sha256") == 2
    assert "WORLD" not in MIGRATION


def test_delivery_is_leased_fenced_atomic_and_retained():
    assert "FOR UPDATE SKIP LOCKED" in MIGRATION
    assert "j.fencing_token+1" in MIGRATION
    assert "v_job.fencing_token<>p_fencing_token" in MIGRATION
    assert MIGRATION.count("v_job.lease_expires_at<=clock_timestamp()") == 2
    assert "VIDEO_CANON_POST_WRITE_INTEGRITY_FAILED" in MIGRATION
    assert "video_canon_promotion_delivery_receipt_append_only" in MIGRATION
    assert "ATTEMPTS_EXHAUSTED" in MIGRATION
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in MIGRATION
    assert "video_canon_assurance_assignment_id=i2.video_canon_assurance_assignment_id" in MIGRATION
    assert "terminal_error_code='STATE_STALE'" in MIGRATION
    assert "v_existing.fencing_token<>p_fencing_token" in MIGRATION


def test_only_consumer_can_cross_authoritative_boundary():
    assert "REVOKE EXECUTE ON FUNCTION bidding.activate_ai_verified_video_canon" in MIGRATION
    assert "FROM bridge_school_canon_promoter" in MIGRATION
    assert "TO bridge_school_canon_consumer" in MIGRATION
    assert "REVOKE INSERT,UPDATE,DELETE,TRUNCATE" in MIGRATION


def test_rollback_restores_pre_migration_boundary_and_registry():
    assert "GRANT EXECUTE ON FUNCTION bidding.activate_ai_verified_video_canon" in ROLLBACK
    assert "TO bridge_school_canon_promoter" in ROLLBACK
    assert "WHERE migration_key='0323_workflow_video_canon_promotion_consumer'" in ROLLBACK
    assert "DROP ROLE IF EXISTS bridge_school_canon_consumer" in ROLLBACK
