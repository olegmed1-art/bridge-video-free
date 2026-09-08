from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import json
import re
import subprocess
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ONE_SHOT = _load("issue_881_precanary_one_shot", "ops/issue_881_precanary_one_shot.py")
REVIEW_GATE = _load(
    "issue_881_codex_review_gate", "ops/issue_881_codex_review_gate.py"
)
QUEUE = _load("issue_881_precanary_queue_proof", "ops/issue_881_precanary_queue_proof.py")
OCI_EXECUTIONS = _load(
    "verify_oci_instance_command_executions",
    "ops/verify_oci_instance_command_executions.py",
)
SHA = "a" * 40
NONCE = "b" * 64
RECEIPT_ID = 5_600_000_001
RUN_ID = 34_100_000_001
NOW = 1_800_000_000.0


def _codex_clean_comment(reviewed_token: str, *, login: str | None = None) -> dict[str, object]:
    return {
        "user": {"login": login or REVIEW_GATE.CODEX_BOT_LOGIN},
        "body": (
            "Codex Review: Didn't find any major issues. :rocket:\n\n"
            f"**Reviewed commit:** `{reviewed_token}`\n"
        ),
    }


@pytest.mark.parametrize("reviewed_token", [SHA[:10], SHA])
def test_codex_clean_receipt_accepts_canonical_exact_head_tokens(
    reviewed_token: str,
) -> None:
    result = REVIEW_GATE.validate_review_evidence(
        [[]],
        [[_codex_clean_comment(reviewed_token)]],
        exact_sha=SHA,
        owner_login="olegmed1-art",
    )
    assert result["codex_clean_count"] == 1
    assert result["approval_count"] == 0
    assert result["assurance_count"] == 1


@pytest.mark.parametrize(
    "reviewed_token",
    [SHA[:9], SHA[:11], "c" * 10, "c" * 40],
)
def test_codex_clean_receipt_rejects_ambiguous_or_wrong_tokens(
    reviewed_token: str,
) -> None:
    with pytest.raises(REVIEW_GATE.ReviewEvidenceError, match="neither a Codex"):
        REVIEW_GATE.validate_review_evidence(
            [[]],
            [[_codex_clean_comment(reviewed_token)]],
            exact_sha=SHA,
            owner_login="olegmed1-art",
        )


def test_codex_clean_receipt_requires_bot_identity_and_one_canonical_commit_line() -> None:
    wrong_login = _codex_clean_comment(SHA[:10], login="olegmed1-art")
    duplicate = _codex_clean_comment(SHA[:10])
    duplicate["body"] = f"{duplicate['body']}**Reviewed commit:** `{SHA}`\n"
    for comment in (wrong_login, duplicate):
        with pytest.raises(REVIEW_GATE.ReviewEvidenceError, match="neither a Codex"):
            REVIEW_GATE.validate_review_evidence(
                [[]],
                [[comment]],
                exact_sha=SHA,
                owner_login="olegmed1-art",
            )


@pytest.mark.parametrize("malformed_token", [SHA[:9], SHA[:10].upper()])
def test_codex_clean_receipt_rejects_malformed_duplicate_commit_lines(
    malformed_token: str,
) -> None:
    comment = _codex_clean_comment(SHA[:10])
    malformed_line = (
        "**Reviewed commit:** "
        + chr(96)
        + malformed_token
        + chr(96)
        + "\\n"
    )
    comment["body"] = f"{comment['body']}{malformed_line}"
    with pytest.raises(REVIEW_GATE.ReviewEvidenceError, match="neither a Codex"):
        REVIEW_GATE.validate_review_evidence(
            [[]],
            [[comment]],
            exact_sha=SHA,
            owner_login="olegmed1-art",
        )


def test_exact_non_owner_approval_remains_valid_after_codex_review_object() -> None:
    reviews = [[
        {
            "user": {"login": REVIEW_GATE.CODEX_BOT_LOGIN},
            "commit_id": SHA,
            "state": "COMMENTED",
            "submitted_at": "2026-09-08T10:00:00Z",
        },
        {
            "user": {"login": "independent-reviewer"},
            "commit_id": SHA,
            "state": "APPROVED",
            "submitted_at": "2026-09-08T10:01:00Z",
        },
    ]]
    result = REVIEW_GATE.validate_review_evidence(
        reviews,
        [[]],
        exact_sha=SHA,
        owner_login="olegmed1-art",
    )
    assert result["codex_review_count"] == 1
    assert result["approval_count"] == 1


def test_codex_comment_without_clean_receipt_is_not_final_assurance() -> None:
    reviews = [[{
        "user": {"login": REVIEW_GATE.CODEX_BOT_LOGIN},
        "commit_id": SHA,
        "state": "COMMENTED",
        "submitted_at": "2026-09-08T10:00:00Z",
    }]]
    with pytest.raises(REVIEW_GATE.ReviewEvidenceError, match="independent approval"):
        REVIEW_GATE.validate_review_evidence(
            reviews,
            [[]],
            exact_sha=SHA,
            owner_login="olegmed1-art",
        )


def _comment(*, recovery: str = "") -> dict[str, object]:
    created = dt.datetime.fromtimestamp(NOW - 60, dt.timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    return {
        "id": RECEIPT_ID,
        "issue_url": "https://api.github.com/repos/olegmed1-art/bridge-video-free/issues/881",
        "user": {"id": 315099490, "login": "olegmed1-art"},
        "author_association": "OWNER",
        "created_at": created,
        "updated_at": created,
        "body": ONE_SHOT.render_receipt(
            exact_sha=SHA,
            approval_nonce=NONCE,
            recover_container_from_run=recovery,
        ),
    }


def _run(
    *,
    run_id: int = RUN_ID,
    receipt_id: int = RECEIPT_ID,
    attempt: int = 1,
    run_number: int = 100,
    recovery: str = "",
    status: str = "in_progress",
    conclusion: str | None = None,
) -> dict[str, object]:
    return {
        "id": run_id,
        "run_number": run_number,
        "path": ONE_SHOT.WORKFLOW_PATH,
        "event": "workflow_dispatch",
        "display_title": ONE_SHOT.expected_run_name(SHA, receipt_id, recovery),
        "run_attempt": attempt,
        "head_sha": SHA,
        "status": status,
        "conclusion": conclusion,
        "repository": {"full_name": ONE_SHOT.REPOSITORY},
        "actor": {"login": ONE_SHOT.DIRECTOR_LOGIN},
        "triggering_actor": {"login": ONE_SHOT.DIRECTOR_LOGIN},
    }


def _validate(comment=None, runs=None, **changes):
    values = {
        "exact_sha": SHA,
        "receipt_id": RECEIPT_ID,
        "approval_nonce": NONCE,
        "recover_container_from_run": "",
        "current_run_id": RUN_ID,
        "current_run_attempt": 1,
        "now": NOW,
    }
    values.update(changes)
    return ONE_SHOT.validate_one_shot(
        _comment() if comment is None else comment,
        [{"workflow_runs": [_run()]}] if runs is None else runs,
        **values,
    )


def test_one_shot_receipt_accepts_only_one_owner_first_attempt() -> None:
    result = _validate()
    assert result == {
        "exact_sha": SHA,
        "receipt_id": RECEIPT_ID,
        "receipt_sha256": result["receipt_sha256"],
        "run_id": RUN_ID,
        "run_attempt": 1,
        "recovery_depth": 0,
    }
    assert len(result["receipt_sha256"]) == 64
    body = _comment()["body"]
    assert '"media_processing":false' in body
    assert '"queue_mutation":false' in body
    assert '"drive_write_performed":false' in body
    assert '"vercel_production_deploy":false' in body
    assert '"one_run":true' in body and '"rerun":false' in body


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda comment: comment.update(updated_at="2030-01-01T00:00:00Z"), "edited"),
        (lambda comment: comment["user"].update(login="someone-else"), "Director-owned"),
        (lambda comment: comment.update(author_association="COLLABORATOR"), "owner"),
        (lambda comment: comment.update(body=str(comment["body"]) + "\n"), "canonical"),
    ],
)
def test_one_shot_receipt_rejects_mutable_or_non_owner_approval(mutation, message) -> None:
    comment = _comment()
    mutation(comment)
    with pytest.raises(ONE_SHOT.OneShotValidationError, match=message):
        _validate(comment=comment)


def test_one_shot_receipt_rejects_stale_approval_and_any_rerun() -> None:
    with pytest.raises(ONE_SHOT.OneShotValidationError, match="stale"):
        _validate(now=NOW + ONE_SHOT.MAX_RECEIPT_AGE_SECONDS)
    with pytest.raises(ONE_SHOT.OneShotValidationError, match="reruns"):
        _validate(current_run_attempt=2)
    rerun = _run(attempt=2)
    with pytest.raises(ONE_SHOT.OneShotValidationError, match="rerun"):
        _validate(runs=[{"workflow_runs": [rerun]}])


def test_one_shot_receipt_rejects_duplicate_receipt_or_second_sha_run() -> None:
    duplicate_receipt = _run(run_id=RUN_ID + 1)
    with pytest.raises(ONE_SHOT.OneShotValidationError, match="exactly once"):
        _validate(runs=[{"workflow_runs": [_run(), duplicate_receipt]}])

    second_receipt = _run(run_id=RUN_ID + 2, receipt_id=RECEIPT_ID + 1)
    with pytest.raises(ONE_SHOT.OneShotValidationError):
        _validate(runs=[{"workflow_runs": [_run(), second_receipt]}])


def test_recovery_retains_initial_state_source_across_failed_attempts() -> None:
    recovery_id = RUN_ID - 1
    recovery = str(recovery_id)
    comment = _comment(recovery=recovery)
    prior = _run(
        run_id=recovery_id,
        receipt_id=RECEIPT_ID - 1,
        run_number=99,
        status="completed",
        conclusion="failure",
    )
    current = _run(recovery=recovery)
    result = _validate(
        comment=comment,
        runs=[{"workflow_runs": [prior, current]}],
        recover_container_from_run=recovery,
    )
    assert result["run_id"] == RUN_ID
    assert result["recovery_depth"] == 1

    unsafe = copy.deepcopy(prior)
    unsafe["conclusion"] = "success"
    with pytest.raises(ONE_SHOT.OneShotValidationError, match="completed failure"):
        _validate(
            comment=comment,
            runs=[{"workflow_runs": [unsafe, current]}],
            recover_container_from_run=recovery,
        )

    initial_id = RUN_ID - 2
    initial = _run(
        run_id=initial_id,
        receipt_id=RECEIPT_ID - 2,
        run_number=98,
        status="completed",
        conclusion="failure",
    )
    prior_recovery = _run(
        run_id=recovery_id,
        receipt_id=RECEIPT_ID - 1,
        run_number=99,
        recovery=str(initial_id),
        status="completed",
        conclusion="failure",
    )
    state_source = str(initial_id)
    current = _run(recovery=state_source)
    comment = _comment(recovery=state_source)
    chained = _validate(
        comment=comment,
        runs=[{"workflow_runs": [initial, prior_recovery, current]}],
        recover_container_from_run=state_source,
    )
    assert chained["recovery_depth"] == 2

    broken = copy.deepcopy(prior_recovery)
    broken["display_title"] = ONE_SHOT.expected_run_name(
        SHA, RECEIPT_ID - 1, str(RUN_ID - 9)
    )
    with pytest.raises(ONE_SHOT.OneShotValidationError, match="state-bearing source"):
        _validate(
            comment=comment,
            runs=[{"workflow_runs": [initial, broken, current]}],
            recover_container_from_run=state_source,
        )


def test_recovery_chain_has_a_hard_total_run_limit() -> None:
    runs = []
    state_source = str(RUN_ID - ONE_SHOT.MAX_RUNS_PER_EXACT_SHA)
    for offset in range(ONE_SHOT.MAX_RUNS_PER_EXACT_SHA + 1):
        run_id = RUN_ID - ONE_SHOT.MAX_RUNS_PER_EXACT_SHA + offset
        runs.append(
            _run(
                run_id=run_id,
                receipt_id=RECEIPT_ID - ONE_SHOT.MAX_RUNS_PER_EXACT_SHA + offset,
                run_number=100 - ONE_SHOT.MAX_RUNS_PER_EXACT_SHA + offset,
                recovery=("" if offset == 0 else state_source),
                status=("in_progress" if offset == ONE_SHOT.MAX_RUNS_PER_EXACT_SHA else "completed"),
                conclusion=(None if offset == ONE_SHOT.MAX_RUNS_PER_EXACT_SHA else "failure"),
            )
        )
    current = runs[-1]
    current_receipt = int(str(current["display_title"]).split("receipt-", 1)[1].split("/", 1)[0])
    created = dt.datetime.fromtimestamp(NOW - 60, dt.timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    comment = _comment(recovery=state_source)
    comment["id"] = current_receipt
    comment["body"] = ONE_SHOT.render_receipt(
        exact_sha=SHA,
        approval_nonce=NONCE,
        recover_container_from_run=state_source,
    )
    comment["created_at"] = created
    comment["updated_at"] = created
    with pytest.raises(ONE_SHOT.OneShotValidationError, match="limit"):
        ONE_SHOT.validate_one_shot(
            comment,
            [{"workflow_runs": runs}],
            exact_sha=SHA,
            receipt_id=current_receipt,
            approval_nonce=NONCE,
            recover_container_from_run=state_source,
            current_run_id=int(current["id"]),
            current_run_attempt=1,
            now=NOW,
        )


def _runtime_snapshot() -> dict[str, object]:
    return {
        "project": QUEUE.PROJECT,
        "branch": QUEUE.BRANCH,
        "database": QUEUE.DATABASE,
        "principal": QUEUE.RUNTIME_PRINCIPAL,
        "schema_exists": True,
        "function_exists": True,
        "claimable": 0,
        "leased": 0,
    }


def _owner_snapshot() -> dict[str, object]:
    return {
        **_runtime_snapshot(),
        "principal": QUEUE.OWNER,
        "batches": 0,
        "jobs": 0,
        "events": 0,
        "max_event_id": None,
        "sequence_last_value": 1,
        "sequence_is_called": False,
    }


def test_queue_proofs_accept_only_exact_pristine_production_snapshots() -> None:
    assert QUEUE.validate_runtime_snapshot(_runtime_snapshot())["branch"] == QUEUE.BRANCH
    assert QUEUE.validate_owner_snapshot(_owner_snapshot())["sequence_is_called"] is False

    for key, value in (
        ("branch", "br-preview"),
        ("principal", "wrong"),
        ("claimable", 1),
        ("leased", 1),
    ):
        candidate = _runtime_snapshot()
        candidate[key] = value
        with pytest.raises(QUEUE.QueueProofError):
            QUEUE.validate_runtime_snapshot(candidate)

    for key, value in (
        ("batches", 1),
        ("jobs", 1),
        ("events", 1),
        ("max_event_id", 1),
        ("sequence_last_value", 2),
        ("sequence_is_called", True),
    ):
        candidate = _owner_snapshot()
        candidate[key] = value
        with pytest.raises(QUEUE.QueueProofError):
            QUEUE.validate_owner_snapshot(candidate)


def test_queue_proofs_require_exact_principals_hosts_and_tls() -> None:
    runtime = (
        f"postgresql://{QUEUE.RUNTIME_PRINCIPAL}:synthetic-password@"
        f"{QUEUE.RUNTIME_HOST}/{QUEUE.DATABASE}?sslmode=require&channel_binding=require"
    )
    owner = (
        f"postgresql://{QUEUE.OWNER}:synthetic-password@"
        f"{QUEUE.RUNTIME_HOST.replace('-pooler.c-5', '.c-5')}/{QUEUE.DATABASE}"
        "?sslmode=require&channel_binding=require"
    )
    assert QUEUE._validated_dsn(
        runtime, principal=QUEUE.RUNTIME_PRINCIPAL, hosts={QUEUE.RUNTIME_HOST}
    ) == runtime
    assert QUEUE._validated_dsn(owner, principal=QUEUE.OWNER, hosts=QUEUE.OWNER_HOSTS) == owner

    for bad in (
        runtime.replace(QUEUE.RUNTIME_PRINCIPAL, QUEUE.OWNER),
        runtime.replace("ep-noisy-pine", "ep-wrong-branch"),
        runtime.replace("/neondb", "/other"),
        runtime.replace("sslmode=require", "sslmode=prefer"),
        runtime.replace("channel_binding=require", "channel_binding=prefer"),
        runtime + "&host=",
        runtime + "#fragment",
    ):
        with pytest.raises(QUEUE.QueueProofError):
            QUEUE._validated_dsn(
                bad, principal=QUEUE.RUNTIME_PRINCIPAL, hosts={QUEUE.RUNTIME_HOST}
            )


def test_owner_release_gate_holds_all_queue_tables_until_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Cursor:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def execute(self, statement: str) -> None:
            self.statements.append(statement)

    class Connection:
        def __init__(self) -> None:
            self.read_only = False
            self.cursor_value = Cursor()
            self.rollbacks = 0

        def cursor(self):
            return self.cursor_value

        def rollback(self) -> None:
            self.rollbacks += 1

    connection = Connection()
    snapshot = _owner_snapshot()
    snapshots: list[dict[str, object]] = []

    def read_snapshot(_connection, *, owner: bool):
        assert owner is True
        snapshots.append(snapshot)
        return dict(snapshot)

    ready = tmp_path / "owner-ready"
    control = tmp_path / "owner-control"

    def release_after_ready(_seconds: float) -> None:
        assert ready.is_file()
        control.write_bytes(QUEUE.OWNER_RELEASE_SIGNAL)
        control.chmod(0o600)

    monkeypatch.setattr(QUEUE, "_database_snapshot_on_connection", read_snapshot)
    monkeypatch.setattr(QUEUE.time, "sleep", release_after_ready)
    marker = QUEUE.hold_owner_release_gate(
        connection,
        baseline=snapshot,
        ready_file=ready,
        control_file=control,
        timeout_seconds=30,
    )

    assert connection.read_only is False
    assert connection.cursor_value.statements[-1] == QUEUE.OWNER_RELEASE_LOCK_SQL
    assert all(
        statement.startswith(("SET LOCAL ", "LOCK TABLE "))
        for statement in connection.cursor_value.statements
    )
    assert QUEUE.OWNER_RELEASE_LOCK_SQL == (
        "LOCK TABLE video_queue.batch, video_queue.job, video_queue.job_event "
        "IN SHARE MODE NOWAIT"
    )
    assert len(snapshots) == 2
    assert connection.rollbacks == 1
    assert ready.read_text(encoding="utf-8").strip() == QUEUE._owner_marker(
        "POSTRESTORE_OWNER", snapshot, unchanged=True
    )
    assert not control.exists()
    assert marker == (
        "UNIVERSAL_VIDEO_PRECANARY_DB_ENQUEUE_FENCE "
        "tables=batch,job,job_event lock=SHARE owner_release=observed "
        "final_snapshot=unchanged result=PASS"
    )

    aborted_connection = Connection()
    aborted_ready = tmp_path / "owner-ready-abort"
    aborted_control = tmp_path / "owner-control-abort"

    def abort_after_ready(_seconds: float) -> None:
        assert aborted_ready.is_file()
        aborted_control.write_bytes(QUEUE.OWNER_ABORT_SIGNAL)
        aborted_control.chmod(0o600)

    monkeypatch.setattr(QUEUE.time, "sleep", abort_after_ready)
    with pytest.raises(QUEUE.QueueProofError, match="aborted"):
        QUEUE.hold_owner_release_gate(
            aborted_connection,
            baseline=snapshot,
            ready_file=aborted_ready,
            control_file=aborted_control,
            timeout_seconds=30,
        )
    assert aborted_connection.rollbacks == 1


def test_database_ci_proves_real_share_lock_and_rollback_contract() -> None:
    workflow = (
        ROOT / ".github/workflows/issue-881-contract-ci.yml"
    ).read_text(encoding="utf-8")
    step = workflow.index("Prove rollback-only owner release lock blocks queue writers")
    read_write = workflow.index("BEGIN READ WRITE;", step)
    share = workflow.index("IN SHARE MODE NOWAIT;", read_write)
    row_exclusive = workflow.index("IN ROW EXCLUSIVE MODE NOWAIT;", share)
    rollback = workflow.index("ROLLBACK;", row_exclusive)
    marker = workflow.index("ISSUE881_OWNER_RELEASE_DB_FENCE_PASS", rollback)
    assert step < read_write < share < row_exclusive < rollback < marker


def test_runtime_dsn_is_read_once_from_exact_protected_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dsn_file = tmp_path / "video-queue-dsn"
    dsn = (
        f"postgresql://{QUEUE.RUNTIME_PRINCIPAL}:synthetic-password@"
        f"{QUEUE.RUNTIME_HOST}/{QUEUE.DATABASE}?sslmode=require&channel_binding=require"
    )
    dsn_file.write_text(dsn, encoding="utf-8")
    dsn_file.chmod(0o640)
    monkeypatch.setattr(QUEUE, "RUNTIME_DSN_FILE", dsn_file)
    monkeypatch.setenv("BRIDGE_VIDEO_QUEUE_DATABASE_URL_FILE", str(dsn_file))
    monkeypatch.delenv("BRIDGE_VIDEO_QUEUE_DATABASE_URL", raising=False)
    monkeypatch.delenv("BRIDGE_WORKER_DATABASE_URL", raising=False)
    real_fstat = QUEUE.os.fstat

    def root_owned_fstat(descriptor: int) -> SimpleNamespace:
        metadata = real_fstat(descriptor)
        return SimpleNamespace(
            st_mode=metadata.st_mode,
            st_uid=0,
            st_nlink=metadata.st_nlink,
            st_size=metadata.st_size,
        )

    # GitHub-hosted test runners are non-root. Preserve the production
    # requirement while injecting only the expected root ownership field.
    monkeypatch.setattr(QUEUE.os, "fstat", root_owned_fstat)
    assert QUEUE._runtime_dsn() == dsn

    link = tmp_path / "linked-dsn"
    link.symlink_to(dsn_file)
    monkeypatch.setattr(QUEUE, "RUNTIME_DSN_FILE", link)
    monkeypatch.setenv("BRIDGE_VIDEO_QUEUE_DATABASE_URL_FILE", str(link))
    with pytest.raises(QUEUE.QueueProofError, match="unreadable"):
        QUEUE._runtime_dsn()


def test_owner_baseline_is_exclusive_strict_and_round_trips(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    expected = _owner_snapshot()
    QUEUE._write_baseline(baseline, expected)
    assert QUEUE._read_baseline(baseline) == expected
    assert baseline.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        QUEUE._write_baseline(baseline, expected)


def test_workflow_hardening_is_machine_enforced_before_host_mutation() -> None:
    workflow = (
        ROOT / ".github/workflows/issue-881-authoritative-external-evidence.yml"
    ).read_text(encoding="utf-8")
    runner = (
        ROOT / "ops/issue_881_external_precanary_workflow.sh"
    ).read_text(encoding="utf-8")
    attest = (ROOT / "ops/oracle_universal_video_precanary_attest.sh").read_text(
        encoding="utf-8"
    )
    installer = (ROOT / "ops/oracle_universal_video_container_install.sh").read_text(
        encoding="utf-8"
    )

    assert "approval_receipt_id:" in workflow and "approval_nonce:" in workflow
    assert "run-name: issue881-precanary/" in workflow
    assert "/recover-${{ inputs.recover_container_from_run || 'none' }}" in workflow
    assert len(workflow) < 21_000
    assert "run: bash ops/issue_881_external_precanary_workflow.sh" in workflow
    workflow_header = workflow.split("\njobs:", 1)[0]
    assert "actions: write" in workflow_header
    assert "${{" not in runner
    assert (
        workflow.count("issue_881_precanary_one_shot.py verify")
        + runner.count("issue_881_precanary_one_shot.py verify")
        == 2
    )
    assert "'ops/issue_881_external_precanary_workflow.sh'" in runner
    assert "'ops/oracle_universal_video_run_command.sh'" in runner
    assert "'ops/oracle_known_hosts_from_scan.sh'" in runner
    assert "'.github/workflows/oracle-universal-video-admin.yml'" in runner
    for protected_creator in (
        "oracle-assistant-lab-oci-diagnostic.yml",
        "oracle-diana11-002-delivery.yml",
        "oracle-diana11-002-job.yml",
        "oracle-diana11-delivery.yml",
        "oracle-instance-power.yml",
        "oracle-universal-video-evidence-export.yml",
    ):
        assert f"'.github/workflows/{protected_creator}'" in runner
    assert "'ops/verify_oci_instance_command_executions.py'" in runner
    admin_workflow = (
        ROOT / ".github/workflows/oracle-universal-video-admin.yml"
    ).read_text(encoding="utf-8")
    assert "oracle-universal-video-admin-pr-{0}" in admin_workflow
    assert "'oracle-instance-workload-mutation'" in admin_workflow
    assert "group: oracle-universal-video-bounded-admin" not in admin_workflow
    for protected_external_mutator in (
        ".github/workflows/database-production.yml",
        ".github/workflows/oracle-operational-safety-gate.yml",
        ".github/workflows/oracle-operator-commands.yml",
        ".github/workflows/oracle-operator-v3.yml",
        ".github/workflows/video-job-monitor.yml",
    ):
        assert f"'{protected_external_mutator}'" in runner
        mutator_workflow = (ROOT / protected_external_mutator).read_text(
            encoding="utf-8"
        )
        header = mutator_workflow.split("\njobs:", 1)[0]
        assert header.count("\nconcurrency:\n") == 1
        assert "oracle-instance-workload-mutation" in header
        assert "  cancel-in-progress: false" in header
    process_video = (ROOT / ".github/workflows/process-video.yml").read_text(
        encoding="utf-8"
    )
    process_header = process_video.split("\njobs:", 1)[0]
    assert "oracle-instance-workload-mutation" not in process_header
    assert "actions: read" in process_header
    assert "precanary-fence:" in process_video
    assert "needs: precanary-fence" in process_video
    assert "run: bash ops/process_video_precanary_fence.sh" in process_video
    assert "'.github/workflows/process-video.yml'" in runner
    assert "'ops/process_video_precanary_fence.sh'" in runner
    assert "'ops/issue_881_process_video_dispatch_gate.sh'" in runner
    assert "verify_no_active_instance_agent_commands" in runner
    assert "instance-agent command-execution list" in runner
    assert '--instance-id "$INSTANCE_ID"' in runner
    assert "universal-video-(?:audit|productionize)" not in runner
    assert "active_remote_commands=0" in runner
    assert "Reconcile exact OCI command termination" in admin_workflow
    assert "instance-agent command cancel --command-id" in admin_workflow
    assert "UNIVERSAL_VIDEO_OCI_ADMIN_RECONCILE" in admin_workflow
    required = runner[
        runner.index("required_workflows=(") : runner.index("root_required_workflows=(")
    ]
    assert "Issue 881 Exact Canary Contract CI" in required
    assert "Issue 881 Exact Pre-Canary Evidence" in required
    assert "Issue 881 Current-Main Authoritative CI" in required
    assert "Secret gate" in required
    assert "Retired Oracle Universal Video Container Evidence Contract" not in required
    for workflow_path in (
        ".github/workflows/issue-881-contract-ci.yml",
        ".github/workflows/issue-881-precanary-evidence.yml",
    ):
        required_workflow = (ROOT / workflow_path).read_text(encoding="utf-8")
        pull_request_block = required_workflow.split("pull_request:", 1)[1].split(
            "permissions:", 1
        )[0]
        assert "paths:" not in pull_request_block
    assert "'.github/workflows/oracle-universal-video-container-promote.yml'" in runner
    assert "'ops/oracle_universal_video_container_promote.sh'" in runner
    assert "'universal_video'" in runner and "':(glob)bridge_*.py'" in runner
    assert "GITHUB_RUN_ATTEMPT" in runner
    assert "verify_no_competing_infrastructure_runs" in runner
    assert "UNIVERSAL_VIDEO_PRECANARY_INFRASTRUCTURE_EXCLUSIVE" in runner
    infrastructure_gate = runner[
        runner.index("collect_active_workflow_run_sweep(){") :
        runner.index("# Initial reconciliation rejects historical")
    ]
    assert "actions/runs?per_page=100" not in infrastructure_gate
    assert infrastructure_gate.count("actions/runs?status=$status&per_page=100") == 1
    assert "--paginate" not in infrastructure_gate
    assert "--slurp" not in infrastructure_gate
    assert "statuses=(requested waiting pending queued in_progress)" in infrastructure_gate
    assert "statuses=(in_progress queued pending waiting requested)" in infrastructure_gate
    assert "collect_active_workflow_run_sweep forward" in infrastructure_gate
    assert "collect_active_workflow_run_sweep reverse" in infrastructure_gate
    assert ".status == $status" in infrastructure_gate
    assert "reported_total" in infrastructure_gate
    assert "loaded_total" in infrastructure_gate
    assert "unique_total" in infrastructure_gate
    assert "Active workflow snapshot is incomplete for status" in infrastructure_gate
    assert "Current pre-canary run is missing from an active workflow sweep" in infrastructure_gate
    assert "UNIVERSAL_VIDEO_RECLAIM_ROOT_CACHE=1" not in runner
    assert "UNIVERSAL_VIDEO_CONTAINER_ALLOW_CACHE_RECLAIM=0" in attest
    assert 'find "$root_cache" -xdev -mindepth 1 -delete' not in attest
    assert "verify_postrestore_runtime_queue" in attest
    assert "UNIVERSAL_VIDEO_QUEUE_PROOF_SHA256" in attest
    assert "'root:root:600:1'" in attest
    assert 'sha256sum "$QUEUE_PROOF_SCRIPT"' in attest
    assert "UNIVERSAL_VIDEO_PRECANARY_POSTRESTORE_RUNTIME" in attest
    assert "UNIVERSAL_VIDEO_PRECANARY_FENCED_START" in attest
    final_window = runner.index("# Staging can outlive the evidence")
    final_live_gate = runner.index("verify_live_gate", final_window)
    final_receipt = runner.index("verify_one_shot_gate", final_live_gate)
    final_boundary = runner.index("verify_final_mutation_boundary", final_receipt)
    host_attest = runner.index('"${s[@]}" "sudo -n env', final_boundary)
    assert final_live_gate < final_receipt < final_boundary < host_attest
    boundary_definition = runner.index("verify_final_mutation_boundary(){")
    boundary_dispatch = runner.index(
        "verify_process_video_dispatch_suspended", boundary_definition
    )
    boundary_infrastructure = runner.index(
        'current_infrastructure_marker="$(verify_no_competing_infrastructure_runs)"',
        boundary_definition,
    )
    boundary_oci_commands = runner.index(
        'current_oci_command_marker="$(verify_no_active_instance_agent_commands)"',
        boundary_infrastructure,
    )
    boundary_main = runner.index("verify_exact_current_main", boundary_oci_commands)
    assert boundary_dispatch < boundary_infrastructure < boundary_oci_commands < boundary_main
    initial_reconciliation = runner.index("# Initial reconciliation rejects historical")
    initial_sweep = runner.index(
        "verify_no_competing_infrastructure_runs", initial_reconciliation
    )
    dispatch_trap = runner.index("trap control_plane_cleanup EXIT", initial_sweep)
    assert runner.count("trap '' HUP INT TERM") == 2
    assert "trap 'exit 129' HUP" in runner
    assert "trap 'exit 130' INT" in runner
    assert "trap 'exit 143' TERM" in runner
    dispatch_suspend = runner.index(
        "bash ops/issue_881_process_video_dispatch_gate.sh", dispatch_trap
    )
    first_host_access = runner.index(
        'ops/oracle_known_hosts_from_scan.sh "$ORACLE_HOST"', dispatch_suspend
    )
    assert initial_sweep < dispatch_trap < dispatch_suspend < first_host_access
    control_plane_cleanup_definition = runner[
        runner.index("control_plane_cleanup(){") : runner.index(
            "verify_final_mutation_boundary(){"
        )
    ]
    assert control_plane_cleanup_definition.index("trap '' HUP INT TERM") < (
        control_plane_cleanup_definition.index("restore_process_video_dispatch || rc=1")
    )
    cleanup_definition = runner[
        runner.index("cleanup_remote(){") : runner.index("bounded_failure(){")
    ]
    assert "restore_process_video_dispatch || rc=1" in cleanup_definition
    assert cleanup_definition.index("trap '' HUP INT TERM") < cleanup_definition.index(
        "abort_remote_attester"
    ) < cleanup_definition.index("restore_process_video_dispatch || rc=1")
    assert "cat \"$process_video_suspend_marker_file\"" in runner
    assert "PROCESS_VIDEO_DISPATCH_RESTORE" in workflow
    fenced_start = attest.index("if start_container_under_fence; then", attest.index("cleanup(){"))
    runtime_proof = attest.index("verify_postrestore_runtime_queue", fenced_start)
    owner_release = attest.index("verify_postrestore_owner_release", runtime_proof)
    workload_unlock = attest.index("flock --unlock 9", owner_release)
    assert fenced_start < runtime_proof < owner_release < workload_unlock
    assert "UNIVERSAL_VIDEO_PRECANARY_OWNER_RELEASE" in attest
    runtime_ready = runner.index("runtime_proof_ready=1", host_attest)
    database_gate_start = runner.index("owner-release-gate", runtime_ready)
    locked_owner = runner.index('postrestore_owner_marker="$(cat', database_gate_start)
    remote_release = runner.index('"$ORACLE_USER@$ORACLE_HOST:$remote_stage/owner-release"', locked_owner)
    attester_wait = runner.index('wait "$attester_pid"', remote_release)
    database_release = runner.index("release_database_gate", attester_wait)
    assert (
        runtime_ready
        < database_gate_start
        < locked_owner
        < remote_release
        < attester_wait
        < database_release
    )
    assert "UNIVERSAL_VIDEO_PRECANARY_DB_ENQUEUE_FENCE" in workflow
    readiness_failure = attest.index(
        "if ! validate_started_container_after_fence", workload_unlock
    )
    readiness_fail_closed = attest.index(
        "runtime_release_safe=0", readiness_failure
    )
    unsafe_restore = attest.index(
        'if [[ "$runtime_release_safe" != 1 ]]', readiness_fail_closed
    )
    remask = attest.index(
        'bounded_systemctl mask --runtime "$SOURCE_SERVICE" "$CONTAINER_SERVICE"',
        unsafe_restore,
    )
    stop = attest.index(
        'bounded_systemctl stop "$SOURCE_SERVICE" "$CONTAINER_SERVICE"', remask
    )
    conditional_unlock = attest.index('if [[ "$lock_held" == 1 ]]', stop)
    unsafe_unlock = attest.index("flock --unlock 9", conditional_unlock)
    assert readiness_failure < readiness_fail_closed < unsafe_restore
    assert unsafe_restore < remask < stop < conditional_unlock < unsafe_unlock
    assert "declare -a inherited_failure_runtime_masks=()" in attest
    recovery_branch = attest.index(
        'if [[ -n "$RECOVER_CONTAINER_FROM_RUN" ]]'
    )
    assert "approved recovery no longer matches a stopped container resident" in attest
    recovery_evidence = attest.index("verify_prior_recovery_evidence", recovery_branch)
    capture_masks = attest.index("capture_inherited_failure_runtime_masks", recovery_evidence)
    recovery_window = attest.index("mask_service_for_window", capture_masks)
    assert recovery_evidence < capture_masks < recovery_window
    cleanup_start = attest.index("cleanup(){")
    cleanup_full_restore = attest.index("restore_source_checkout", cleanup_start)
    inherited_unmask = attest.index(
        '"${inherited_failure_runtime_masks[@]}"', cleanup_full_restore
    )
    verified_unmask = attest.index("bounded_systemctl_query is-enabled", inherited_unmask)
    fenced_restore = attest.index("if start_container_under_fence; then", inherited_unmask)
    assert cleanup_full_restore < inherited_unmask < verified_unmask < fenced_restore
    assert "enabled|disabled|static|indirect" in attest[verified_unmask:fenced_restore]
    assert "UNIVERSAL_VIDEO_PRECANARY_POSTRESTORE_OWNER" in workflow
    assert 'ALLOW_CACHE_RECLAIM="${UNIVERSAL_VIDEO_CONTAINER_ALLOW_CACHE_RECLAIM:-1}"' in installer


def test_infrastructure_snapshot_is_active_race_safe_and_fail_closed(tmp_path: Path) -> None:
    runner = (
        ROOT / "ops/issue_881_external_precanary_workflow.sh"
    ).read_text(encoding="utf-8")
    start = runner.index("collect_active_workflow_run_sweep(){")
    end = runner.index("\n\n# Initial reconciliation rejects historical", start)
    function = textwrap.dedent(runner[start:end])
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    harness = f"""\
set -euo pipefail
{function}
gh(){{
  endpoint="${{!#}}"
  status="${{endpoint#*status=}}"
  status="${{status%%&*}}"
  counter="$SNAPSHOT_DIR/$status.count"
  call=0
  [[ ! -f "$counter" ]] || call="$(cat "$counter")"
  call=$((call + 1))
  printf '%s\n' "$call" > "$counter"
  command cat "$SNAPSHOT_DIR/$status-$call.json"
}}
GITHUB_REPOSITORY=olegmed1-art/bridge-video-free
GITHUB_RUN_ID=42
verify_no_competing_infrastructure_runs
"""

    statuses = ("requested", "waiting", "pending", "queued", "in_progress")
    current = {
        "id": 42,
        "path": ".github/workflows/issue-881-authoritative-external-evidence.yml",
        "status": "in_progress",
    }

    def page(
        runs: list[dict[str, object]], *, total_count: int | None = None
    ) -> dict[str, object]:
        return {
            "total_count": len(runs) if total_count is None else total_count,
            "workflow_runs": runs,
        }

    def run(
        first: dict[str, list[dict[str, object]]] | None = None,
        second: dict[str, list[dict[str, object]]] | None = None,
        *, first_total: dict[str, int] | None = None,
        second_total: dict[str, int] | None = None,
        include_current: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        first = first or {}
        second = second or first
        first_total = first_total or {}
        second_total = second_total or {}
        for counter in snapshots.glob("*.count"):
            counter.unlink()
        for status in statuses:
            first_runs = list(first.get(status, []))
            second_runs = list(second.get(status, []))
            if status == "in_progress" and include_current:
                first_runs.insert(0, current)
                second_runs.insert(0, current)
            (snapshots / f"{status}-1.json").write_text(
                json.dumps(
                    page(first_runs, total_count=first_total.get(status))
                ),
                encoding="utf-8",
            )
            (snapshots / f"{status}-2.json").write_text(
                json.dumps(
                    page(second_runs, total_count=second_total.get(status))
                ),
                encoding="utf-8",
            )
        return subprocess.run(
            ["bash"],
            input=harness,
            text=True,
            capture_output=True,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "RUNNER_TEMP": str(runtime),
                "SNAPSHOT_DIR": str(snapshots),
            },
            timeout=10,
        )

    safe = run()
    assert safe.returncode == 0, safe.stderr
    assert "other_active=0 other_queued=0 result=PASS" in safe.stdout
    assert all(
        (snapshots / f"{status}.count").read_text(encoding="utf-8").strip() == "2"
        for status in statuses
    )

    unrelated_run = {
        "id": 7,
        "path": ".github/workflows/unit-tests.yml",
        "status": "queued",
    }
    unrelated = run({"queued": [unrelated_run]})
    assert unrelated.returncode == 0, unrelated.stderr

    competing_run = {
        "id": 8,
        "path": ".github/workflows/oracle-universal-video-admin.yml",
        "status": "in_progress",
    }
    competing = run({"in_progress": [competing_run]})
    assert competing.returncode != 0
    assert "A competing infrastructure workflow is active or queued" in competing.stderr

    queued_run = {
        "id": 9,
        "path": ".github/workflows/oracle-instance-power.yml",
        "status": "queued",
    }
    transitioned_run = dict(queued_run, status="in_progress")
    transition = run(
        {"queued": [queued_run]},
        {"in_progress": [transitioned_run]},
    )
    assert transition.returncode != 0
    assert "A competing infrastructure workflow is active or queued" in transition.stderr

    late_run = dict(queued_run, id=10, status="pending")
    late = run({}, {"pending": [late_run]})
    assert late.returncode != 0
    assert "A competing infrastructure workflow is active or queued" in late.stderr

    incomplete = run(
        {"queued": [queued_run]},
        first_total={"queued": 2},
    )
    assert incomplete.returncode != 0
    assert "Active workflow snapshot is incomplete for status: queued" in incomplete.stderr

    wrong_status = run(
        {"queued": [dict(queued_run, status="in_progress")]},
    )
    assert wrong_status.returncode != 0
    assert "Active workflow snapshot is incomplete for status: queued" in wrong_status.stderr

    invalid_id = run(
        {"queued": [dict(queued_run, id="9")]},
    )
    assert invalid_id.returncode != 0
    assert "Active workflow snapshot is incomplete for status: queued" in invalid_id.stderr

    missing_current = run(include_current=False)
    assert missing_current.returncode != 0
    assert "Current pre-canary run is missing from an active workflow sweep" in missing_current.stderr


def test_process_video_fence_preserves_dispatch_and_closes_precanary_race(
    tmp_path: Path,
) -> None:
    fence = ROOT / "ops/process_video_precanary_fence.sh"
    workflow = (ROOT / ".github/workflows/process-video.yml").read_text(
        encoding="utf-8"
    )
    script = fence.read_text(encoding="utf-8")
    assert "--paginate" not in script
    assert "PROCESS_VIDEO_PRECANARY_FENCE_PASS" in script
    assert "needs: precanary-fence" in workflow
    assert "timeout-minutes: 130" in workflow
    assert "timeout-minutes: 330" in workflow

    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
endpoint="${!#}"
if [[ "$endpoint" == *"/actions/runs/$GITHUB_RUN_ID" ]]; then
  cat "$SNAPSHOT_DIR/self.json"
  exit 0
fi
status="${endpoint#*status=}"
status="${status%%&*}"
counter="$SNAPSHOT_DIR/$status.count"
call=0
[[ ! -f "$counter" ]] || call="$(cat "$counter")"
call=$((call + 1))
printf '%s\n' "$call" > "$counter"
cat "$SNAPSHOT_DIR/$status-$call.json"
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    statuses = ("requested", "waiting", "pending", "queued", "in_progress")

    def page(
        runs: list[dict[str, object]], *, total_count: int | None = None
    ) -> dict[str, object]:
        return {
            "total_count": len(runs) if total_count is None else total_count,
            "workflow_runs": runs,
        }

    def run(
        first: dict[str, list[dict[str, object]]] | None = None,
        second: dict[str, list[dict[str, object]]] | None = None,
        *, first_total: dict[str, int] | None = None,
        self_status: str = "in_progress",
    ) -> subprocess.CompletedProcess[str]:
        first = first or {}
        second = second or first
        first_total = first_total or {}
        for counter in snapshots.glob("*.count"):
            counter.unlink()
        (snapshots / "self.json").write_text(
            json.dumps(
                {
                    "id": 42,
                    "path": ".github/workflows/process-video.yml",
                    "event": "workflow_dispatch",
                    "status": self_status,
                }
            ),
            encoding="utf-8",
        )
        for status in statuses:
            (snapshots / f"{status}-1.json").write_text(
                json.dumps(
                    page(first.get(status, []), total_count=first_total.get(status))
                ),
                encoding="utf-8",
            )
            (snapshots / f"{status}-2.json").write_text(
                json.dumps(page(second.get(status, []))),
                encoding="utf-8",
            )
        return subprocess.run(
            ["bash", str(fence)],
            text=True,
            capture_output=True,
            env={
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "GH_TOKEN": "synthetic",
                "GITHUB_REPOSITORY": "olegmed1-art/bridge-video-free",
                "GITHUB_RUN_ID": "42",
                "RUNNER_TEMP": str(runtime),
                "SNAPSHOT_DIR": str(snapshots),
                "PROCESS_VIDEO_PRECANARY_MAX_WAIT_SECONDS": "0",
                "PROCESS_VIDEO_PRECANARY_POLL_SECONDS": "1",
            },
            timeout=10,
        )

    safe = run()
    assert safe.returncode == 0, safe.stderr
    assert "request_preserved=true" in safe.stdout
    assert all(
        (snapshots / f"{status}.count").read_text(encoding="utf-8").strip()
        == "2"
        for status in statuses
    )

    blocker = {
        "id": 99,
        "path": ".github/workflows/issue-881-authoritative-external-evidence.yml",
        "status": "queued",
    }
    transitioned = dict(blocker, status="in_progress")
    blocked = run({"queued": [blocker]}, {"in_progress": [transitioned]})
    assert blocked.returncode == 75
    assert "request remains preserved in run 42" in blocked.stderr
    assert "blockers=99" in blocked.stderr

    incomplete = run({"queued": [blocker]}, first_total={"queued": 2})
    assert incomplete.returncode != 0
    assert "snapshot is incomplete for status: queued" in incomplete.stderr

    missing_self = run(self_status="completed")
    assert missing_self.returncode != 0
    assert "not an active workflow witness" in missing_self.stderr


def test_process_video_dispatch_gate_suspends_verifies_and_restores_exact_state(
    tmp_path: Path,
) -> None:
    gate = ROOT / "ops/issue_881_process_video_dispatch_gate.sh"
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    workflow_state = tmp_path / "workflow-state"
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
method=GET
endpoint=''
[[ "${1:-}" == api ]] && shift
while (($#)); do
  case "$1" in
    --method) method="$2"; shift 2 ;;
    *) endpoint="$1"; shift ;;
  esac
done
case "$method:$endpoint" in
  PUT:*/disable) printf '%s\n' disabled_manually > "$WORKFLOW_STATE" ;;
  PUT:*/enable) printf '%s\n' active > "$WORKFLOW_STATE" ;;
  GET:*/actions/workflows/process-video.yml)
    jq -n --arg state "$(cat "$WORKFLOW_STATE")" \
      '{id: 123, path: ".github/workflows/process-video.yml", name: "Process bridge video", state: $state}'
    ;;
  *) printf 'unexpected fake gh call: %s:%s\n' "$method" "$endpoint" >&2; exit 64 ;;
esac
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    state_receipt = runtime / "issue-881-process-video-workflow-state"
    env = {
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "GH_TOKEN": "synthetic",
        "GITHUB_REPOSITORY": "olegmed1-art/bridge-video-free",
        "RUNNER_TEMP": str(runtime),
        "WORKFLOW_STATE": str(workflow_state),
    }

    def invoke(action: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(gate), action, str(state_receipt)],
            text=True,
            capture_output=True,
            env=env,
            timeout=10,
        )

    workflow_state.write_text("active\n", encoding="utf-8")
    suspended = invoke("suspend")
    assert suspended.returncode == 0, suspended.stderr
    assert workflow_state.read_text(encoding="utf-8") == "disabled_manually\n"
    assert state_receipt.read_text(encoding="utf-8") == "active\n"
    assert state_receipt.stat().st_mode & 0o777 == 0o600
    assert "initial_state=active final_state=disabled_manually changed=true" in suspended.stdout
    verified = invoke("verify")
    assert verified.returncode == 0, verified.stderr
    assert verified.stdout.strip() == (
        "PROCESS_VIDEO_DISPATCH_VERIFY state=disabled_manually result=PASS"
    )
    restored = invoke("restore")
    assert restored.returncode == 0, restored.stderr
    assert workflow_state.read_text(encoding="utf-8") == "active\n"
    assert "initial_state=active final_state=active result=PASS" in restored.stdout

    state_receipt.unlink()
    workflow_state.write_text("disabled_manually\n", encoding="utf-8")
    preserved = invoke("suspend")
    assert preserved.returncode == 0, preserved.stderr
    assert "initial_state=disabled_manually" in preserved.stdout
    assert "changed=false" in preserved.stdout
    restored_disabled = invoke("restore")
    assert restored_disabled.returncode == 0, restored_disabled.stderr
    assert workflow_state.read_text(encoding="utf-8") == "disabled_manually\n"

    state_receipt.unlink()
    workflow_state.write_text("active\n", encoding="utf-8")
    assert invoke("suspend").returncode == 0
    workflow_state.write_text("active\n", encoding="utf-8")
    lost_suspension = invoke("restore")
    assert lost_suspension.returncode != 0
    assert "suspension was lost before restoration" in lost_suspension.stderr
    assert workflow_state.read_text(encoding="utf-8") == "active\n"

    state_receipt.unlink()
    workflow_state.write_text("completed\n", encoding="utf-8")
    unsafe = invoke("suspend")
    assert unsafe.returncode != 0
    assert "identity or state is unsafe" in unsafe.stderr
    assert not state_receipt.exists()


def test_every_owner_triggered_oracle_mutator_uses_the_protected_shared_fence() -> None:
    direct_mutation = re.compile(
        r"systemctl (?:restart|start|stop|enable|disable|daemon-reload)|systemd-run|"
        r"oci compute instance action|"
        r"install -o root.*video-queue|VIDEO_QUEUE_DSN",
        re.DOTALL,
    )
    script_reference = re.compile(
        r"(?<![A-Za-z0-9_-])(ops/[A-Za-z0-9_.-]+\.sh)(?![A-Za-z0-9_./-])"
    )

    owner_mutators: set[str] = set()
    mutation_payloads: set[str] = set()
    for path in (ROOT / ".github/workflows").glob("oracle-*.yml"):
        workflow = path.read_text(encoding="utf-8")
        if "  workflow_dispatch:" not in workflow and "  issue_comment:" not in workflow:
            continue
        pending = {
            relative
            for relative in script_reference.findall(workflow)
            if (ROOT / relative).is_file()
        }
        indirect: dict[str, str] = {}
        while pending:
            relative = pending.pop()
            if relative in indirect:
                continue
            payload = (ROOT / relative).read_text(encoding="utf-8")
            indirect[relative] = payload
            pending.update(
                child
                for child in script_reference.findall(payload)
                if child not in indirect and (ROOT / child).is_file()
            )
        if direct_mutation.search(workflow + "\n" + "\n".join(indirect.values())):
            owner_mutators.add(path.relative_to(ROOT).as_posix())
            mutation_payloads.update(
                relative
                for relative, payload in indirect.items()
                if direct_mutation.search(payload)
            )
    assert owner_mutators == {
        ".github/workflows/oracle-ben-dds3-health-monitor.yml",
        ".github/workflows/oracle-dds3-pilot10k-operator.yml",
        ".github/workflows/oracle-instance-power.yml",
        ".github/workflows/oracle-operational-safety-gate.yml",
        ".github/workflows/oracle-operator-commands.yml",
        ".github/workflows/oracle-operator-v2.yml",
        ".github/workflows/oracle-operator-v3.yml",
        ".github/workflows/oracle-universal-video-activation.yml",
        ".github/workflows/oracle-universal-video-job.yml",
        ".github/workflows/oracle-universal-video-queue-credential-install.yml",
        ".github/workflows/oracle-universal-video-sidecar-repair.yml",
    }
    assert mutation_payloads == {
        "ops/install_ben_runtime.sh",
        "ops/install_dds3_runtime.sh",
        "ops/oracle_dds3_mass_install.sh",
        "ops/oracle_dds3_operational_gate.sh",
        "ops/oracle_universal_video_install.sh",
        "ops/oracle_universal_video_run_command.sh",
        "ops/universal_video_sidecar_repair.sh",
    }
    runner = (
        ROOT / "ops/issue_881_external_precanary_workflow.sh"
    ).read_text(encoding="utf-8")
    for relative in owner_mutators:
        workflow = (ROOT / relative).read_text(encoding="utf-8")
        header = workflow.split("\njobs:", 1)[0]
        assert "oracle-instance-workload-mutation" in header, relative
        assert "cancel-in-progress: false" in header, relative
        assert f"'{relative}'" in runner, relative
        if "  pull_request:" in header:
            assert "github.event_name" in header, relative
            assert "format(" in header, relative
    for relative in mutation_payloads:
        assert f"'{relative}'" in runner, relative


def test_every_code_triggered_oracle_host_mutator_uses_shared_fence_and_provenance() -> None:
    script_reference = re.compile(
        r"(?<![A-Za-z0-9_-])(ops/[A-Za-z0-9_.-]+\.sh)(?![A-Za-z0-9_./-])"
    )
    host_marker = re.compile(
        r"\b(?:ORACLE_HOST|OCI_INSTANCE_OCID|INSTANCE_ID)\b|\bssh\b|"
        r"\$\{ssh|oci\s+(?:compute|instance-agent)|158\.180\.47\.161",
        re.IGNORECASE,
    )
    mutation = re.compile(
        r"systemctl\s+(?:restart|start|stop|enable|disable|daemon-reload)\b|"
        r"systemd-run\b|"
        r"oci\s+(?:--config-file\s+\S+\s+)?(?:compute\s+instance\s+action|"
        r"instance-agent\s+command\s+create)\b|"
        r"(?:^|\n)\s*(?:sudo\s+-n\s+)?install\s+|"
        r"docker\s+(?:run|rm|restart|stop|start|pull)\b|"
        r"git\s+-C\s+[^\n]+\s+(?:checkout|reset|pull)\b",
        re.IGNORECASE,
    )
    push_mutators: set[str] = set()
    mutation_payloads: set[str] = set()
    documents: dict[str, dict[str, object]] = {}
    for path in (ROOT / ".github/workflows").glob("oracle-*.yml"):
        workflow = path.read_text(encoding="utf-8")
        document = yaml.safe_load(workflow)
        assert isinstance(document, dict), path
        triggers = document.get("on", document.get(True, {}))
        event_names = {triggers} if isinstance(triggers, str) else set(triggers)
        if not event_names.intersection({"push", "pull_request_target"}) or not host_marker.search(workflow):
            continue
        jobs = document.get("jobs")
        assert isinstance(jobs, dict), path
        run_corpus = "\n".join(
            str(step.get("run", ""))
            for job in jobs.values()
            if isinstance(job, dict)
            for step in job.get("steps", [])
            if isinstance(step, dict)
        )
        pending = {
            relative
            for relative in script_reference.findall(run_corpus)
            if (ROOT / relative).is_file()
        }
        indirect: dict[str, str] = {}
        while pending:
            relative = pending.pop()
            if relative in indirect:
                continue
            payload = (ROOT / relative).read_text(encoding="utf-8")
            indirect[relative] = payload
            pending.update(
                child
                for child in script_reference.findall(payload)
                if child not in indirect and (ROOT / child).is_file()
            )
        mutating_payloads = {
            relative
            for relative, payload in indirect.items()
            if mutation.search(payload)
        }
        if mutation.search(run_corpus) or mutating_payloads:
            relative = path.relative_to(ROOT).as_posix()
            push_mutators.add(relative)
            mutation_payloads.update(mutating_payloads)
            documents[relative] = document

    assert push_mutators == {
        ".github/workflows/oracle-assistant-lab-control-rollout.yml",
        ".github/workflows/oracle-assistant-lab-oci-diagnostic.yml",
        ".github/workflows/oracle-assistant-lab-worker-rollout.yml",
        ".github/workflows/oracle-autopilot-online-observer.yml",
        ".github/workflows/oracle-autopilot-online-resume.yml",
        ".github/workflows/oracle-autopilot-production-canary.yml",
        ".github/workflows/oracle-autopilot-shadow-activation.yml",
        ".github/workflows/oracle-autopilot-staging-finalize.yml",
        ".github/workflows/oracle-autopilot-staging.yml",
        ".github/workflows/oracle-ben-runtime-rollout.yml",
        ".github/workflows/oracle-dds3-pilot10k-launch.yml",
        ".github/workflows/oracle-dds3-tls-renewal.yml",
        ".github/workflows/oracle-diana11-002-delivery.yml",
        ".github/workflows/oracle-diana11-002-job.yml",
        ".github/workflows/oracle-diana11-002-operator-bootstrap.yml",
        ".github/workflows/oracle-diana11-003-bootstrap-diagnostic.yml",
        ".github/workflows/oracle-diana11-003-one-shadow-execution.yml",
        ".github/workflows/oracle-diana11-delivery.yml",
        ".github/workflows/oracle-diana11-oauth-repair.yml",
        ".github/workflows/oracle-diana11-operator-bootstrap.yml",
        ".github/workflows/oracle-diana11-provenance-sync.yml",
        ".github/workflows/oracle-diana11-runtime-pin-repair.yml",
        ".github/workflows/oracle-diana11-ready-before-probe.yml",
        ".github/workflows/oracle-diana11-shadow-preflight-bootstrap.yml",
        ".github/workflows/oracle-idle-guard-exact-install.yml",
        ".github/workflows/oracle-idle-proof-bootstrap.yml",
        ".github/workflows/oracle-universal-video-activation.yml",
        ".github/workflows/oracle-universal-video-admin.yml",
        ".github/workflows/oracle-universal-video-batch-intake.yml",
        ".github/workflows/oracle-universal-video-container-missing-image-recover.yml",
        ".github/workflows/oracle-universal-video-container-promote.yml",
        ".github/workflows/oracle-universal-video-evidence-export.yml",
        ".github/workflows/oracle-universal-video-job.yml",
        ".github/workflows/oracle-universal-video-sidecar-repair.yml",
    }
    assert mutation_payloads == {
        "ops/assistant_lab_oci_admin_entrypoint.sh",
        "ops/install_assistant_lab_ocarun_admin.sh",
        "ops/install_ben_runtime.sh",
        "ops/install_oracle_idle_state_ocarun.sh",
        "ops/install_universal_video_diana11_002_operator.sh",
        "ops/install_universal_video_diana11_003_operator.sh",
        "ops/install_universal_video_diana11_operator.sh",
        "ops/install_universal_video_diana11_shadow_preflight.sh",
        "ops/install_universal_video_ocarun_admin.sh",
        "ops/install_universal_video_operator.sh",
        "ops/oracle_autopilot_online_observer_install.sh",
        "ops/oracle_autopilot_production_canary_install.sh",
        "ops/oracle_autopilot_shadow_install.sh",
        "ops/oracle_assistant_lab_control_bridge_install.sh",
        "ops/oracle_assistant_lab_observer_install.sh",
        "ops/oracle_dds3_mass_install.sh",
        "ops/oracle_universal_video_container_install.sh",
        "ops/oracle_universal_video_container_missing_image_recover.sh",
        "ops/oracle_universal_video_container_promote.sh",
        "ops/oracle_universal_video_drive_secret_install.sh",
        "ops/oracle_universal_video_install.sh",
        "ops/oracle_universal_video_prepromotion_preflight.sh",
        "ops/oracle_universal_video_productionize.sh",
        "ops/oracle_universal_video_run_command.sh",
        "ops/repair_universal_video_runtime_pin.sh",
        "ops/universal_video_diana11_oauth_repair.sh",
        "ops/universal_video_diana11_provenance_sync.sh",
        "ops/universal_video_sidecar_repair.sh",
    }

    # Three legacy workflows place the shared fence directly on their only
    # host-mutating job. Every other push mutator must fence the whole run.
    job_scoped_fences = {
        ".github/workflows/oracle-autopilot-online-observer.yml": "install",
        ".github/workflows/oracle-autopilot-online-resume.yml": "resume",
        ".github/workflows/oracle-autopilot-shadow-activation.yml": "activate",
    }
    runner = (
        ROOT / "ops/issue_881_external_precanary_workflow.sh"
    ).read_text(encoding="utf-8")
    for relative in push_mutators:
        document = documents[relative]
        if relative in job_scoped_fences:
            jobs = document["jobs"]
            assert isinstance(jobs, dict)
            job = jobs[job_scoped_fences[relative]]
            assert isinstance(job, dict)
            concurrency = job.get("concurrency")
        else:
            concurrency = document.get("concurrency")
        assert isinstance(concurrency, dict), relative
        assert concurrency.get("group") == "oracle-instance-workload-mutation" or (
            "oracle-instance-workload-mutation" in str(concurrency.get("group", ""))
            and "github.event_name" in str(concurrency.get("group", ""))
        ), relative
        assert concurrency.get("cancel-in-progress") is False, relative
        assert f"'{relative}'" in runner, relative
    for relative in mutation_payloads:
        assert f"'{relative}'" in runner, relative


def test_every_shared_production_fence_workflow_and_payload_is_provenance_protected() -> None:
    runner = (
        ROOT / "ops/issue_881_external_precanary_workflow.sh"
    ).read_text(encoding="utf-8")
    shell_token = re.compile(
        r"(?<![A-Za-z0-9_.-])([A-Za-z0-9_./${}-]+\.sh)(?![A-Za-z0-9_./-])"
    )
    shell_by_name: dict[str, set[str]] = {}
    for path in ROOT.rglob("*.sh"):
        if ".git" in path.parts:
            continue
        shell_by_name.setdefault(path.name, set()).add(
            path.relative_to(ROOT).as_posix()
        )

    def repository_shell_references(source: str) -> set[str]:
        references: set[str] = set()
        for token in shell_token.findall(source):
            parts = token.replace("${", "").replace("}", "").lstrip("./").split("/")
            matched = False
            for offset in range(len(parts) - 1):
                candidate = "/".join(parts[offset:])
                if (ROOT / candidate).is_file():
                    references.add(candidate)
                    matched = True
                    break
            if not matched:
                references.update(shell_by_name.get(parts[-1], set()))
        return references
    shared_workflows: set[str] = set()
    referenced_payloads: set[str] = set()
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        workflow = path.read_text(encoding="utf-8")
        if "oracle-instance-workload-mutation" not in workflow:
            continue
        relative = path.relative_to(ROOT).as_posix()
        shared_workflows.add(relative)
        pending = repository_shell_references(workflow)
        indirect: dict[str, str] = {}
        while pending:
            reference = pending.pop()
            if reference in indirect:
                continue
            payload = (ROOT / reference).read_text(encoding="utf-8")
            indirect[reference] = payload
            pending.update(repository_shell_references(payload) - set(indirect))
        referenced_payloads.update(indirect)
    assert len(shared_workflows) == 65
    assert len(referenced_payloads) == 55
    assert "ops/universal_video_spool_repair.sh" in referenced_payloads
    assert "ops/universal_video_evidence_export_entrypoint.sh" in referenced_payloads
    for relative in shared_workflows | referenced_payloads:
        assert f"'{relative}'" in runner, relative


def test_every_direct_oracle_rollout_uses_a_trusted_shared_fence() -> None:
    workflows = ROOT / ".github/workflows"
    rollouts = {
        path.relative_to(ROOT).as_posix()
        for path in workflows.glob("oracle-*rollout.yml")
        if "ORACLE_HOST:" in path.read_text(encoding="utf-8")
    }
    assert rollouts == {
        ".github/workflows/oracle-assistant-lab-control-rollout.yml",
        ".github/workflows/oracle-assistant-lab-worker-rollout.yml",
        ".github/workflows/oracle-ben-runtime-rollout.yml",
    }
    runner = (
        ROOT / "ops/issue_881_external_precanary_workflow.sh"
    ).read_text(encoding="utf-8")
    for relative in rollouts:
        workflow = (ROOT / relative).read_text(encoding="utf-8")
        header = workflow.split("\njobs:", 1)[0]
        assert "oracle-instance-workload-mutation" in header, relative
        assert "cancel-in-progress: false" in header, relative
        assert "format(" in header, relative
        assert "github.event_name" in header, relative
        assert f"'{relative}'" in runner, relative
        if "  pull_request_target:" in header:
            for trust_gate in (
                "github.event.pull_request.head.repo.full_name == github.repository",
                "github.event.pull_request.user.login == github.repository_owner",
                "github.event.pull_request.base.ref == 'main'",
                "github.event.pull_request.changed_files == 1",
            ):
                assert workflow.count(trust_gate) >= 2, (relative, trust_gate)


def test_database_production_fence_excludes_rejected_dispatches() -> None:
    workflow = (ROOT / ".github/workflows/database-production.yml").read_text(
        encoding="utf-8"
    )
    header = workflow.split("\njobs:", 1)[0]
    assert "github.ref == 'refs/heads/database-production'" in header
    assert "inputs.confirmation == 'MIGRATE'" in header
    assert "'oracle-instance-workload-mutation'" in header
    assert "database-production-noop-{0}" in header
    assert "cancel-in-progress: false" in header


def test_every_instance_agent_command_must_be_terminal(tmp_path: Path) -> None:
    runner = (
        ROOT / "ops/issue_881_external_precanary_workflow.sh"
    ).read_text(encoding="utf-8")
    start = runner.index("verify_no_active_instance_agent_commands(){")
    end = runner.index("\n\nverify_final_mutation_boundary(){", start)
    function = textwrap.dedent(runner[start:end])
    config_dir = tmp_path / "oci"
    config_dir.mkdir()
    config = config_dir / "config"
    config.write_text("synthetic\n", encoding="utf-8")
    config.chmod(0o600)
    executions = tmp_path / "executions.json"
    command_id = "ocid1.instanceagentcommand.oc1.synthetic"
    harness = f"""\
set -euo pipefail
{function}
oci(){{
  case "$*" in
    *" compute instance get "*) printf '%s\\n' 'ocid1.compartment.oc1.synthetic' ;;
    *" instance-agent command-execution list "*) command cat "$EXECUTIONS" ;;
    *) return 64 ;;
  esac
}}
RUNNER_TEMP={json.dumps(str(tmp_path))}
INSTANCE_ID=ocid1.instance.oc1.synthetic
verify_no_active_instance_agent_commands
"""

    def run(
        state: str, *, instance_id: str = "ocid1.instance.oc1.synthetic"
    ) -> subprocess.CompletedProcess[str]:
        executions.write_text(
            json.dumps(
                {
                    "data": [
                        {
                            "instance-agent-command-id": command_id,
                            "instance-id": instance_id,
                            "display-name": "diana11-delivery-34100000001",
                            "lifecycle-state": state,
                            "time-created": dt.datetime.now(dt.timezone.utc).isoformat(),
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return subprocess.run(
            ["bash"],
            input=harness,
            text=True,
            capture_output=True,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "EXECUTIONS": str(executions),
            },
            timeout=10,
        )

    terminal = run("SUCCEEDED")
    assert terminal.returncode == 0, terminal.stderr
    assert (
        "examined_instance_executions=1 active_remote_commands=0 result=PASS"
        in terminal.stdout
    )
    active = run("IN_PROGRESS")
    assert active.returncode != 0
    assert "Run Command on target instance is not terminal: IN_PROGRESS" in active.stderr
    unknown = run("QUEUED")
    assert unknown.returncode != 0
    assert "unknown OCI Run Command lifecycle" in unknown.stderr
    wrong_instance = run("SUCCEEDED", instance_id="ocid1.instance.oc1.other")
    assert wrong_instance.returncode != 0
    assert "belongs to another instance" in wrong_instance.stderr


def test_every_live_instance_command_creator_uses_the_common_actions_fence() -> None:
    workflows = ROOT / ".github/workflows"
    creator_pattern = re.compile(
        r"(?m)^\s+(?:if ! )?(?:command_id|cid|cmd)=.*"
        r"oci instance-agent command create"
    )
    creators = {
        path.relative_to(ROOT).as_posix()
        for path in workflows.glob("*.yml")
        if creator_pattern.search(path.read_text(encoding="utf-8"))
    }
    assert creators == {
        ".github/workflows/oracle-assistant-lab-oci-diagnostic.yml",
        ".github/workflows/oracle-diana11-002-delivery.yml",
        ".github/workflows/oracle-diana11-002-job.yml",
        ".github/workflows/oracle-diana11-delivery.yml",
        ".github/workflows/oracle-instance-power.yml",
        ".github/workflows/oracle-universal-video-admin.yml",
        ".github/workflows/oracle-universal-video-evidence-export.yml",
    }
    conditional_pr_groups = {
        ".github/workflows/oracle-diana11-002-delivery.yml":
            "oracle-diana11-002-delivery-pr-{0}",
        ".github/workflows/oracle-diana11-002-job.yml":
            "oracle-diana11-002-pr-{0}",
        ".github/workflows/oracle-diana11-delivery.yml":
            "oracle-diana11-delivery-pr-{0}",
        ".github/workflows/oracle-universal-video-evidence-export.yml":
            "oracle-universal-video-evidence-export-pr-{0}",
        ".github/workflows/oracle-universal-video-admin.yml":
            "oracle-universal-video-admin-pr-{0}",
    }
    for relative in creators:
        header = (ROOT / relative).read_text(encoding="utf-8").split("\njobs:", 1)[0]
        assert header.count("\nconcurrency:\n") == 1, relative
        if relative in conditional_pr_groups:
            assert "github.event_name == 'pull_request'" in header, relative
            assert conditional_pr_groups[relative] in header, relative
            assert "|| 'oracle-instance-workload-mutation' }}" in header, relative
        elif relative == ".github/workflows/oracle-instance-power.yml":
            assert "github.event_name == 'workflow_dispatch'" in header
            assert "github.actor == github.repository_owner" in header
            assert "oracle-instance-power-noop-{0}" in header
            assert "&& 'oracle-instance-workload-mutation' ||" in header
        else:
            assert re.search(
                r"(?m)^  group: oracle-instance-workload-mutation$", header
            ), relative


def test_shared_oci_execution_validator_rejects_active_unknown_and_ambiguous() -> None:
    instance = "ocid1.instance.oc1.synthetic"
    timestamp = "2026-09-07T12:00:00+00:00"

    def payload(state: str, *, command: str = "one", target: str = instance):
        return {
            "data": [
                {
                    "instance-agent-command-id": (
                        f"ocid1.instanceagentcommand.oc1.synthetic-{command}"
                    ),
                    "instance-id": target,
                    "lifecycle-state": state,
                    "time-created": timestamp,
                }
            ]
        }

    assert OCI_EXECUTIONS.validate_executions(payload("SUCCEEDED"), instance) == 1
    for state in ("ACCEPTED", "IN_PROGRESS"):
        with pytest.raises(
            OCI_EXECUTIONS.CommandExecutionValidationError, match="not terminal"
        ):
            OCI_EXECUTIONS.validate_executions(payload(state), instance)
    with pytest.raises(
        OCI_EXECUTIONS.CommandExecutionValidationError, match="unknown"
    ):
        OCI_EXECUTIONS.validate_executions(payload("QUEUED"), instance)
    with pytest.raises(
        OCI_EXECUTIONS.CommandExecutionValidationError, match="another instance"
    ):
        OCI_EXECUTIONS.validate_executions(
            payload("SUCCEEDED", target="ocid1.instance.oc1.other"), instance
        )
    duplicate = payload("FAILED")
    duplicate["data"].append(dict(duplicate["data"][0]))
    with pytest.raises(
        OCI_EXECUTIONS.CommandExecutionValidationError, match="duplicate"
    ):
        OCI_EXECUTIONS.validate_executions(duplicate, instance)


def test_owner_snapshot_controls_unlock_while_worker_is_fenced(tmp_path: Path) -> None:
    script = (
        ROOT / "ops/oracle_universal_video_precanary_attest.sh"
    ).read_text(encoding="utf-8")
    start = script.index("verify_postrestore_owner_release(){")
    end = script.index("\n\ncleanup(){", start)
    function = textwrap.dedent(script[start:end])
    token = "c" * 64
    token_sha = hashlib.sha256(token.encode()).hexdigest()
    marker = QUEUE._owner_marker("POSTRESTORE_OWNER", _owner_snapshot(), unchanged=True)
    control = tmp_path / "owner-release"
    abort_control = tmp_path / "owner-abort"

    def run(expected_sha: str, *, abort: bool = False) -> subprocess.CompletedProcess[str]:
        control.write_text(f"{token}\n{marker}\n", encoding="utf-8")
        if abort:
            abort_control.write_text("ABORT\n", encoding="utf-8")
        elif abort_control.exists():
            abort_control.unlink()
        harness = f"""\
set -euo pipefail
{function}
stat(){{
  if [[ "$*" == *"%U:%G:%a:%h"* ]]; then
    printf '%s\\n' root:root:600:1
  else
    command stat "$@"
  fi
}}
lock_held=1
OWNER_RELEASE_FILE={json.dumps(str(control))}
OWNER_ABORT_FILE={json.dumps(str(abort_control))}
OWNER_RELEASE_TOKEN_SHA256={json.dumps(expected_sha)}
OWNER_RELEASE_TIMEOUT_SECONDS=30
verify_postrestore_owner_release
"""
        return subprocess.run(
            ["bash"],
            input=harness,
            text=True,
            capture_output=True,
            timeout=10,
        )

    accepted = run(token_sha)
    assert accepted.returncode == 0, accepted.stderr
    assert marker in accepted.stdout
    assert "worker_fenced=true owner_snapshot=unchanged result=PASS" in accepted.stdout
    assert not control.exists()
    rejected = run("d" * 64)
    assert rejected.returncode != 0
    aborted = run(token_sha, abort=True)
    assert aborted.returncode != 0
    assert not abort_control.exists()
    assert control.exists()


def test_recovery_parses_and_preserves_original_source_target(tmp_path: Path) -> None:
    script = (
        ROOT / "ops/oracle_universal_video_precanary_attest.sh"
    ).read_text(encoding="utf-8")
    start = script.index("verify_prior_recovery_evidence(){")
    end = script.index("\n\nassert_pre_stop_idle(){", start)
    function = textwrap.dedent(script[start:end])
    evidence = tmp_path / "recovery.txt"
    evidence.write_text(
        "\n".join(
            [
                f"runtime_sha={'a' * 40}",
                "UNIVERSAL_VIDEO_PRECANARY_WINDOW source_service_before=active "
                "source_service_observed=active container_service_before=active "
                "container_service_observed=active workload_fence=exclusive "
                "services_quiescent=true restore_on_exit=true",
                "UNIVERSAL_VIDEO_PRECANARY_RESTORE_FAILED codes=container_service "
                "source_service=inactive container_service=inactive",
                "real_media_canary_run=false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
    harness = f"""\
set -euo pipefail
{function}
die(){{ printf '%s\\n' "$*" >&2; exit 1; }}
RECOVERY_EVIDENCE_FILE={json.dumps(str(evidence))}
RECOVERY_EVIDENCE_SHA256={json.dumps(digest)}
RECOVER_CONTAINER_FROM_RUN=34100000001
source_target_state=
verify_prior_recovery_evidence
[[ "$source_target_state" == active ]]
"""
    completed = subprocess.run(
        ["bash"], input=harness, text=True, capture_output=True, timeout=10
    )
    assert completed.returncode == 0, completed.stderr


def test_post_fence_readiness_failure_is_runtime_masked_for_recovery(
    tmp_path: Path,
) -> None:
    script = (
        ROOT / "ops/oracle_universal_video_precanary_attest.sh"
    ).read_text(encoding="utf-8")
    cleanup = script[script.index("cleanup(){") : script.index("assert_known_state(){")]
    action_log = tmp_path / "actions.log"
    probe = cleanup + f'''\
set -u
action_log={json.dumps(str(action_log))}
restore_failures=()
prestop_frozen_pids=()
added_runtime_masks=(source.service container.service)
inherited_failure_runtime_masks=()
window_started=1
services_stop_attempted=1
BUILD_IMAGE=0
lock_held=1
container_was_active=0
resident_image_id=
SOURCE_SERVICE=source.service
CONTAINER_SERVICE=container.service
source_state_before=active
source_target_state=active
container_state_before=active
container_target_state=active
container_recovery_requested=0
restored_source_pid=
restored_source_start_ticks=
restored_container_pid=111
restored_container_start_ticks=222
exec 9</dev/null
record_restore_failure(){{ restore_failures+=("$1"); }}
stop_frozen_residents(){{ return 0; }}
residents_are_quiescent(){{ return 0; }}
bounded_systemctl(){{ printf 'systemctl:%s\n' "$*" >> "$action_log"; }}
bounded_systemctl_query(){{ printf 'enabled\n'; }}
bounded_docker(){{ return 0; }}
start_container_under_fence(){{ printf 'fenced-start\n' >> "$action_log"; }}
verify_postrestore_runtime_queue(){{ printf 'queue-proof\n' >> "$action_log"; }}
verify_postrestore_owner_release(){{ printf 'owner-release\n' >> "$action_log"; }}
flock(){{ printf 'unlock\n' >> "$action_log"; }}
validate_started_container_after_fence(){{
  printf 'post-fence-readiness-failed\n' >> "$action_log"
  return 1
}}
restore_service(){{ printf 'unexpected-restore:%s\n' "$1" >> "$action_log"; }}
resume_isolated_peer(){{ return 0; }}
service_state(){{ printf 'inactive\n'; }}
restored_service_ready(){{ return 1; }}
cleanup
'''
    completed = subprocess.run(
        ["bash"], input=probe, text=True, capture_output=True, timeout=10
    )
    assert completed.returncode == 1
    actions = action_log.read_text(encoding="utf-8").splitlines()
    readiness = actions.index("post-fence-readiness-failed")
    remask = actions.index("systemctl:mask --runtime source.service container.service")
    stop = actions.index("systemctl:stop source.service container.service")
    assert readiness < remask < stop
    assert not any(action.startswith("unexpected-restore:") for action in actions)
    assert "container_service" in completed.stderr
