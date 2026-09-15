from pathlib import Path


WORKFLOW_PATH = Path(".github/workflows/autopilot-codex-event-callback.yml")


def test_codex_callback_workflow_is_event_only_and_identity_pinned():
    source = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "on:\n  issue_comment:\n    types: [created]" in source
    assert "permissions:\n  contents: read\n  issues: read\n  pull-requests: read" in source
    assert "schedule:" not in source
    assert "workflow_dispatch:" not in source
    assert "github.event.repository.id == 1330085090" in source
    assert "github.event.comment.user.id == 315099490" in source
    assert "github.event.comment.user.id == 199175422" in source
    assert "github.event.comment.performed_via_github_app.id == 1144995" in source
    # GitHub expression string literals do not interpret ``\n`` escapes. Keep
    # this job-level router coarse; the Python receiver validates the exact
    # multiline command envelope and every pinned identity before ingestion.
    assert "startsWith(github.event.comment.body, '@codex')" in source
    assert "contains(github.event.comment.body, 'SLAVIK_CODEX_DISPATCH_V1')" in source
    assert "startsWith(github.event.comment.body, '@codex\\n" not in source
    assert "SLAVIK_CODEX_DISPATCH_V1" in source
    assert "AUTOPILOT_CODEX_RESULT_V1" in source


def test_only_guarded_publisher_can_write_repository():
    source = WORKFLOW_PATH.read_text(encoding="utf-8")
    before, publisher = source.split("\n  publish:\n", 1)
    assert "contents: write" not in before
    assert source.count("contents: write") == 1
    assert "permissions:\n      contents: write" in publisher
    assert "vars.AUTOPILOT_BOUNDED_PUBLICATION_ENABLED == 'true'" in publisher
    assert "!contains(github.event.comment.body, 'AUTOPILOT_CODEX_PUBLICATION_V1')" in before
    assert "oracle_autopilot.github_codex_publication" in publisher
    assert "issues: write" not in source
    assert "pull-requests: write" not in source
    assert "actions: write" not in source
    assert source.count("ref: ${{ github.workflow_sha }}") == 3
    assert "github.event.comment.body }}" not in source
    assert "github.event.pull_request.head" not in source
    assert "persist-credentials: false" in source
    assert "AUTOPILOT_CALLBACK_DATABASE_URL" in source
    assert "oracle_autopilot.github_codex_callback ack" in source
    assert "oracle_autopilot.github_codex_callback terminal" in source


def test_sql_ci_covers_publication_dependencies_in_reverse_rollback_order():
    source = Path(".github/workflows/autopilot-role-dispatch-sql-ci.yml").read_text()
    triggers, script = source.split("\njobs:\n", 1)
    for paths in triggers.split("\n  push:\n"):
        assert "database/migrations/0336_autopilot_bounded_publication_permit.sql" in paths
        assert "database/tests/336_autopilot_bounded_publication_permit.sql" in paths
    assert script.count("-f database/tests/336_autopilot_bounded_publication_permit.sql") == 2
    assert script.index("-f database/rollbacks/0336_") < script.index("-f database/rollbacks/0335_")
    assert script.index("-f database/rollbacks/0335_") < script.index("-f database/rollbacks/0334_")
    assert script.index("-f database/migrations/0335_") < script.index("-f database/migrations/0336_")
