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
    assert "SLAVIK_CODEX_DISPATCH_V1" in source
    assert "AUTOPILOT_CODEX_RESULT_V1" in source


def test_codex_callback_workflow_has_no_repository_or_production_write():
    source = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "contents: write" not in source
    assert "issues: write" not in source
    assert "pull-requests: write" not in source
    assert "persist-credentials: false" in source
    assert "AUTOPILOT_CALLBACK_DATABASE_URL" in source
    assert "oracle_autopilot.github_codex_callback ack" in source
    assert "oracle_autopilot.github_codex_callback terminal" in source
