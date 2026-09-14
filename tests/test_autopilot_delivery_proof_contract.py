from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_worker_records_github_as_published_not_sent() -> None:
    source = _read("oracle_autopilot/worker.py")
    assert "autopilot.mark_role_dispatch_published" in source
    assert "autopilot.mark_role_dispatch_sent" not in source
    assert "role_dispatch_published" in source


def test_migration_requires_three_part_delivery_proof() -> None:
    sql = _read("database/migrations/0327_autopilot_delivery_proof.sql")
    for marker in (
        "'PUBLISHED'",
        "'DELIVERY_FAILED'",
        "AUTOPILOT_GITHUB_IS_NOT_CHATGPT_DELIVERY",
        "role_dispatch_delivery_proof",
        "target_chat_id",
        "message_id",
        "run_id",
        "executor_id",
        "p_body->'ui_visible' IS DISTINCT FROM 'true'::jsonb",
        "p_body->>'run_state' <> 'RUNNING'",
        "AUTOPILOT_TERMINAL_EXECUTOR_MISMATCH",
        "terminal-receipt-next-task",
        "QUEUE_EMPTY_DECOMPOSITION_REQUIRED",
    ):
        assert marker in sql


def test_callback_workflow_accepts_only_proof_or_terminal_protocols() -> None:
    workflow = _read(".github/workflows/autopilot-chatgpt-role-callback.yml")
    assert (
        "startsWith(github.event.comment.body, 'AUTOPILOT_DELIVERY_PROOF_V1')"
        in workflow
    )
    assert "startsWith(github.event.comment.body, 'AUTOPILOT_RESULT_V1')" in workflow
    assert "github.event.comment.performed_via_github_app.id == 1144995" in workflow


def test_sql_e2e_covers_required_negative_and_next_task_cases() -> None:
    sql = _read("database/tests/327_autopilot_delivery_proof.sql")
    for code in (
        "AUTOPILOT_FALSE_GITHUB_SENT_ACCEPTED",
        "AUTOPILOT_TERMINAL_WITHOUT_UI_PROOF_ACCEPTED",
        "AUTOPILOT_MISSING_UI_PROOF_NOT_DELIVERY_FAILED",
        "AUTOPILOT_STALE_EXECUTOR_CALLBACK_ACCEPTED",
        "AUTOPILOT_HEAD_CHANGE_CALLBACK_ACCEPTED",
        "AUTOPILOT_TERMINAL_DID_NOT_WAKE_NEXT_TASK",
        "AUTOPILOT_TERMINAL_RECEIPT_NOT_EXACTLY_ONCE",
    ):
        assert code in sql


def test_project_planner_requires_an_existing_chat_target() -> None:
    migration = _read("database/migrations/0328_autopilot_chat_target_gate.sql")
    sql_test = _read("database/tests/328_autopilot_chat_target_gate.sql")
    assert "JOIN autopilot.role_chat_registry AS chat" in migration
    assert "chat.role_id = item.role AND chat.enabled" in migration
    assert "WAITING_FOR_CHAT_TARGET" in migration
    assert "AUTOPILOT_UNMAPPED_ROLE_WAS_CLAIMED" in sql_test
    assert "AUTOPILOT_CHAT_TARGET_WAIT_REASON_MISSING" in sql_test
