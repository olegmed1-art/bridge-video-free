from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pre_rotation_uses_production_callback_database_boundary() -> None:
    workflow = (ROOT / ".github/workflows/autopilot-mailbox-pre-rotation.yml").read_text()
    assert "secrets.AUTOPILOT_CALLBACK_DATABASE_URL" in workflow
    assert "secrets.BRIDGE_WORKER_DATABASE_URL" not in workflow


def test_provider_health_recovery_has_forward_and_guarded_rollback() -> None:
    migration = (
        ROOT / "database/migrations/0356_autopilot_provider_health_recovery.sql"
    ).read_text()
    rollback = (
        ROOT / "database/rollbacks/0356_autopilot_provider_health_recovery.sql"
    ).read_text()
    assert "role_dispatch_provider_success" in migration
    assert "last_success_at=accepted.last_success_at" in migration
    assert "WHEN backlog.paused_count>0 THEN 'warning'" in migration
    assert "GRANT EXECUTE ON FUNCTION autopilot.mailbox_rotation_readiness() TO autopilot_callback" in migration
    assert "AUTOPILOT_0356_ROLLBACK_NEW_PROVIDER_SUCCESS_PRESENT" in rollback
