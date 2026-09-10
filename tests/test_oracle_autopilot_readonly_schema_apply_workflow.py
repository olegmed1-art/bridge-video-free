from pathlib import Path


WORKFLOW = Path(
    ".github/workflows/autopilot-readonly-github-schema-once.yml"
).read_text(encoding="utf-8")


def test_workflow_is_exactly_scoped_to_readonly_github_migrations():
    assert "APPLY_0301_0302" in WORKFLOW
    assert "expected_main_sha" in WORKFLOW
    assert "environment: database-production" in WORKFLOW
    assert "0301_autopilot_github_pr_read_only.sql" in WORKFLOW
    assert "0302_autopilot_github_ci_read_only.sql" in WORKFLOW
    assert "database/scripts/migrate.sh" not in WORKFLOW
    assert "GITHUB_DRAFT_REPAIR_V1" in WORKFLOW
    assert '[[ "$constraint" != *"GITHUB_DRAFT_REPAIR_V1"* ]]' in WORKFLOW
    assert '[[ "$constraint" != *"IBF_READ_ONLY_ANALYSIS"* ]]' in WORKFLOW


def test_workflow_preserves_runtime_data_and_serializes_schema_change():
    assert "oracle-instance-workload-mutation" in WORKFLOW
    assert "task_count_before" in WORKFLOW
    assert "task_count_after" in WORKFLOW
    assert 'test "$task_count_after" = "$task_count_before"' in WORKFLOW
    assert "pg_advisory_lock" in WORKFLOW
    assert "pg_advisory_unlock" in WORKFLOW
    assert "AUTOPILOT_READONLY_GITHUB_SCHEMA_APPLIED" in WORKFLOW
