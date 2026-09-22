from pathlib import Path


WORKFLOW_PATH = Path(".github/workflows/autopilot-codex-event-callback.yml")


def test_provider_terminal_sql_ci_covers_trigger_and_ordered_roundtrip():
    source = Path(".github/workflows/autopilot-role-dispatch-sql-ci.yml").read_text()
    triggers, jobs = source.split("\npermissions:\n", 1)
    migration = "0361_autopilot_provider_terminal_classification.sql"
    regression = "361_autopilot_provider_terminal_classification.sql"
    for path in (f"database/migrations/{migration}",
                 f"database/rollbacks/{migration}",
                 f"database/tests/{regression}"):
        assert triggers.count(f"'{path}'") == 2
    test_call = f"-f database/tests/{regression}"
    rollback_call = f"-f database/rollbacks/{migration}"
    apply_call = f"-f database/migrations/{migration}"
    assert jobs.count(test_call) == 2
    assert jobs.index(test_call) < jobs.index(rollback_call)
    assert jobs.index(rollback_call) < jobs.index("-f database/rollbacks/0360_")
    assert jobs.index("-f database/migrations/0360_") < jobs.index(apply_call)
    assert jobs.index(apply_call) < jobs.rindex(test_call)


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
    assert "Codex couldn''t complete this request. Try again later." in source


def test_only_guarded_publisher_can_write_repository():
    source = WORKFLOW_PATH.read_text(encoding="utf-8")
    before, publisher = source.split("\n  publish:\n", 1)
    assert "contents: write" not in before
    assert source.count("contents: write") == 1
    assert "permissions:\n      contents: write" in publisher
    assert "vars.AUTOPILOT_BOUNDED_PUBLICATION_ENABLED == 'true'" in publisher
    assert "!contains(github.event.comment.body, 'AUTOPILOT_CODEX_PUBLICATION_V1')" in before
    assert "ops.github_autopilot_db_route codex-publication" in publisher
    assert "issues: write" not in source
    assert "pull-requests: write" not in source
    assert "actions: write" not in source
    assert source.count("ref: ${{ github.workflow_sha }}") == 3
    assert "github.event.comment.body }}" not in source
    assert "github.event.pull_request.head" not in source
    assert "persist-credentials: false" in source
    assert "AUTOPILOT_CALLBACK_DATABASE_URL" in source
    assert "ops.github_autopilot_db_route codex-ack" in source
    assert "ops.github_autopilot_db_route codex-terminal" in source



def test_focused_publication_ci_covers_lifecycle_without_duplicating_1608_sql_ci():
    source = Path(".github/workflows/autopilot-publication-security-ci.yml").read_text()
    for filename in (
        "0338_autopilot_bounded_publication_permit.sql",
        "0339_autopilot_native_cli_receipts.sql",
        "0340_autopilot_publication_permit_issuer.sql",
        "338_autopilot_bounded_publication_permit.sql",
        "339_autopilot_native_cli_receipts.sql",
        "340_autopilot_publication_permit_issuer.sql",
        "340a_autopilot_publication_permit_concurrency.sh",
    ):
        assert filename in source
    script = source.split("\njobs:\n", 1)[1]
    assert script.index("-f database/rollbacks/0340_") < script.index("-f database/rollbacks/0339_")
    assert script.index("-f database/rollbacks/0339_") < script.index("-f database/rollbacks/0338_")
    assert script.index("-f database/migrations/0338_") < script.index("-f database/migrations/0339_")
    assert script.index("-f database/migrations/0339_") < script.index("-f database/migrations/0340_")
    assert "postgres:18" in source
    assert "github_codex_publication_permit" in source

    inherited = Path(".github/workflows/autopilot-role-dispatch-sql-ci.yml").read_text()
    assert "0337_autopilot_role_repair_admission.sql" in inherited
    assert "337a_autopilot_role_repair_callback.sql" in inherited
    # The merged dependency CI may reference the upper publication chain only
    # to unwind/reapply it around its lower-chain roundtrip. It must not take
    # ownership by triggering on #1546 migration paths.
    triggers = inherited.split("\npermissions:\n", 1)[0]
    for filename in (
        "0338_autopilot_bounded_publication_permit.sql",
        "0339_autopilot_native_cli_receipts.sql",
        "0340_autopilot_publication_permit_issuer.sql",
    ):
        assert filename not in triggers
        assert filename in inherited
    assert "upper_publication_chain_count" in inherited
    assert "partial publication migration chain" in inherited


def test_mailbox_v2_successor_and_retained_evidence_rollback_guards_are_pinned():
    rollback_338 = Path("database/rollbacks/0338_autopilot_bounded_publication_permit.sql").read_text()
    rollback_340 = Path("database/rollbacks/0340_autopilot_publication_permit_issuer.sql").read_text()
    rollback_345 = Path("database/rollbacks/0345_autopilot_mailbox_v2_codex_inbound.sql").read_text()

    assert "PUBLICATION_ROLLBACK_REQUIRES_0345_ROLLBACK_FIRST" in rollback_338
    assert "PUBLICATION_ISSUER_ROLLBACK_REQUIRES_0345_ROLLBACK_FIRST" in rollback_340
    assert "migration_key='0345_autopilot_mailbox_v2_codex_inbound'" in rollback_338
    assert "migration_key='0345_autopilot_mailbox_v2_codex_inbound'" in rollback_340
    for fragment in (
        "outbox.mode='REPAIR'",
        "outbox.status='CALLBACK_ACCEPTED'",
        "proof.command_pr=outbox.mailbox_pr",
        "permit.command_comment_id=proof.command_comment_id",
        "ROLLBACK_RETAINED_PUBLICATION",
    ):
        assert fragment in rollback_345
