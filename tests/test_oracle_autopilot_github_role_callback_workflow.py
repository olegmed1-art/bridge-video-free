from pathlib import Path


WORKFLOW = Path(
    ".github/workflows/autopilot-chatgpt-role-callback.yml"
).read_text(encoding="utf-8")


def test_callback_workflow_is_event_driven_and_fixed_to_mailbox_identity():
    assert "issue_comment:" in WORKFLOW
    assert "types: [created]" in WORKFLOW
    assert "schedule:" not in WORKFLOW
    for fixed_gate in (
        "github.event.issue.number == 1150",
        "github.event.repository.id == 1330085090",
        "github.event.comment.user.id == 315099490",
        "github.event.comment.performed_via_github_app.id == 1144995",
        "AUTOPILOT_RESULT_V1",
    ):
        assert fixed_gate in WORKFLOW


def test_callback_workflow_uses_dedicated_secret_and_immutable_workflow_code():
    assert "secrets.AUTOPILOT_CALLBACK_DATABASE_URL" in WORKFLOW
    assert "secrets.NEON_DATABASE_URL" not in WORKFLOW
    assert "ref: ${{ github.workflow_sha }}" in WORKFLOW
    assert "persist-credentials: false" in WORKFLOW
    assert "python -m oracle_autopilot.github_role_callback" in WORKFLOW
