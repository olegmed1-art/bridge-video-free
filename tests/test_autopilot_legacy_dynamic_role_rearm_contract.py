from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database/migrations/0326_autopilot_legacy_dynamic_role_rearm.sql"
ROLLBACK = ROOT / "database/rollbacks/0326_autopilot_legacy_dynamic_role_rearm.sql"


def test_legacy_dynamic_role_http_retry_is_bounded_to_pre_release_outbox():
    sql = MIGRATION.read_text()

    assert "outbox.task_id = item.last_task_id" in sql
    assert "outbox.status = 'FAILED_CLOSED'" in sql
    assert "outbox.last_error_code = 'ROLE_DISPATCH_HTTP_ERROR'" in sql
    assert "outbox.completed_at <" in sql
    assert "TIMESTAMPTZ '2026-09-12 15:00:38+00'" in sql
    assert "registry.role_id = item.role" in sql
    assert "registry.enabled" in sql
    assert "AND NOT legacy_dynamic_role_http_failure" in sql


def test_rearm_does_not_expand_roles_or_weaken_semantic_blockers():
    sql = MIGRATION.read_text()

    assert "item.role NOT IN ('RECOGNIZER', 'VIDEO', 'BOOKS', 'KNOWLEDGE')" in sql
    assert "project_work_transport_retryable(item.result_code)" in sql
    assert "CAPABILITY_RELEASE_RETRY_MATERIALIZED" in sql
    assert "generation + 1" in sql
    assert "ON CONFLICT ON CONSTRAINT project_work_task_task_id_key DO NOTHING" in sql
    assert "UPDATE autopilot.role_registry" not in sql
    assert "can_repair" not in sql


def test_rollback_restores_the_0325_gate_without_mutating_work_rows():
    sql = ROLLBACK.read_text()

    assert "AUTOPILOT_LEGACY_ROLE_REARM_ROLLBACK_ACTIVE" in sql
    assert "legacy_dynamic_role_http_failure" not in sql
    assert "UPDATE autopilot.project_work_item" in sql
    assert "DELETE FROM public.schema_migration" in sql
