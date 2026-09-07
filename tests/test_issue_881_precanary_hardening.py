from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import json
import subprocess
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ONE_SHOT = _load("issue_881_precanary_one_shot", "ops/issue_881_precanary_one_shot.py")
QUEUE = _load("issue_881_precanary_queue_proof", "ops/issue_881_precanary_queue_proof.py")
SHA = "a" * 40
NONCE = "b" * 64
RECEIPT_ID = 5_600_000_001
RUN_ID = 34_100_000_001
NOW = 1_800_000_000.0


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
    status: str = "in_progress",
    conclusion: str | None = None,
) -> dict[str, object]:
    return {
        "id": run_id,
        "path": ONE_SHOT.WORKFLOW_PATH,
        "event": "workflow_dispatch",
        "display_title": ONE_SHOT.expected_run_name(SHA, receipt_id),
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
    with pytest.raises(ONE_SHOT.OneShotValidationError, match="already exists"):
        _validate(runs=[{"workflow_runs": [_run(), second_receipt]}])


def test_recovery_allows_only_the_named_failed_first_attempt() -> None:
    recovery_id = RUN_ID - 1
    recovery = str(recovery_id)
    comment = _comment(recovery=recovery)
    prior = _run(
        run_id=recovery_id,
        receipt_id=RECEIPT_ID - 1,
        status="completed",
        conclusion="failure",
    )
    result = _validate(
        comment=comment,
        runs=[{"workflow_runs": [prior, _run()]}],
        recover_container_from_run=recovery,
    )
    assert result["run_id"] == RUN_ID

    unsafe = copy.deepcopy(prior)
    unsafe["conclusion"] = "success"
    with pytest.raises(ONE_SHOT.OneShotValidationError, match="completed failure"):
        _validate(
            comment=comment,
            runs=[{"workflow_runs": [unsafe, _run()]}],
            recover_container_from_run=recovery,
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
    assert len(workflow) < 21_000
    assert "run: bash ops/issue_881_external_precanary_workflow.sh" in workflow
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
    admin_workflow = (
        ROOT / ".github/workflows/oracle-universal-video-admin.yml"
    ).read_text(encoding="utf-8")
    assert "group: oracle-instance-workload-mutation" in admin_workflow
    assert "group: oracle-universal-video-bounded-admin" not in admin_workflow
    assert "verify_no_active_oracle_admin_commands" in runner
    assert "instance-agent command list" in runner
    assert "instance-agent command-execution get" in runner
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
        runner.index("verify_no_competing_infrastructure_runs(){") :
        runner.index("# Initial reconciliation rejects historical")
    ]
    assert infrastructure_gate.count("actions/runs?per_page=100") == 1
    assert "status=in_progress" not in infrastructure_gate
    assert "status=queued" not in infrastructure_gate
    assert 'select(.status != "completed")' in infrastructure_gate
    assert "reported_total" in infrastructure_gate
    assert "loaded_total" in infrastructure_gate
    assert "unique_total" in infrastructure_gate
    assert "snapshot is incomplete or changed while paginating" in infrastructure_gate
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
    boundary_infrastructure = runner.index(
        'current_infrastructure_marker="$(verify_no_competing_infrastructure_runs)"',
        boundary_definition,
    )
    boundary_main = runner.index("verify_exact_current_main", boundary_infrastructure)
    assert boundary_infrastructure < boundary_main
    fenced_start = attest.index("if start_container_under_fence; then", attest.index("cleanup(){"))
    runtime_proof = attest.index("verify_postrestore_runtime_queue", fenced_start)
    owner_release = attest.index("verify_postrestore_owner_release", runtime_proof)
    workload_unlock = attest.index("flock --unlock 9", owner_release)
    assert fenced_start < runtime_proof < owner_release < workload_unlock
    assert "UNIVERSAL_VIDEO_PRECANARY_OWNER_RELEASE" in attest
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


def test_infrastructure_snapshot_is_single_complete_and_fail_closed(tmp_path: Path) -> None:
    runner = (
        ROOT / "ops/issue_881_external_precanary_workflow.sh"
    ).read_text(encoding="utf-8")
    start = runner.index("verify_no_competing_infrastructure_runs(){")
    end = runner.index("\n\n# Initial reconciliation rejects historical", start)
    function = textwrap.dedent(runner[start:end])
    snapshot = tmp_path / "runs.json"
    harness = f"""\
set -euo pipefail
{function}
gh(){{ command cat "$SNAPSHOT"; }}
GITHUB_REPOSITORY=olegmed1-art/bridge-video-free
GITHUB_RUN_ID=42
verify_no_competing_infrastructure_runs
"""

    def run(payload: list[dict[str, object]]) -> subprocess.CompletedProcess[str]:
        snapshot.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.run(
            ["bash"],
            input=harness,
            text=True,
            capture_output=True,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "SNAPSHOT": str(snapshot)},
            timeout=10,
        )

    safe = run(
        [
            {
                "total_count": 2,
                "workflow_runs": [
                    {
                        "id": 42,
                        "path": ".github/workflows/issue-881-authoritative-external-evidence.yml",
                        "status": "in_progress",
                    },
                    {
                        "id": 7,
                        "path": ".github/workflows/oracle-universal-video-admin.yml",
                        "status": "completed",
                    },
                ],
            }
        ]
    )
    assert safe.returncode == 0, safe.stderr
    assert "other_active=0 other_queued=0 result=PASS" in safe.stdout

    competing = run(
        [
            {
                "total_count": 2,
                "workflow_runs": [
                    {
                        "id": 42,
                        "path": ".github/workflows/issue-881-authoritative-external-evidence.yml",
                        "status": "in_progress",
                    },
                    {
                        "id": 8,
                        "path": ".github/workflows/oracle-universal-video-admin.yml",
                        "status": "in_progress",
                    },
                ],
            }
        ]
    )
    assert competing.returncode != 0
    assert "A competing infrastructure workflow is active or queued" in competing.stderr

    incomplete = run(
        [
            {
                "total_count": 2,
                "workflow_runs": [
                    {
                        "id": 42,
                        "path": ".github/workflows/issue-881-authoritative-external-evidence.yml",
                        "status": "in_progress",
                    }
                ],
            }
        ]
    )
    assert incomplete.returncode != 0
    assert "snapshot is incomplete or changed while paginating" in incomplete.stderr


def test_recent_oracle_admin_command_must_be_terminal(tmp_path: Path) -> None:
    runner = (
        ROOT / "ops/issue_881_external_precanary_workflow.sh"
    ).read_text(encoding="utf-8")
    start = runner.index("verify_no_active_oracle_admin_commands(){")
    end = runner.index("\n\nverify_final_mutation_boundary(){", start)
    function = textwrap.dedent(runner[start:end])
    config_dir = tmp_path / "oci"
    config_dir.mkdir()
    config = config_dir / "config"
    config.write_text("synthetic\n", encoding="utf-8")
    config.chmod(0o600)
    commands = tmp_path / "commands.json"
    execution = tmp_path / "execution.json"
    command_id = "ocid1.instanceagentcommand.oc1.synthetic"
    commands.write_text(
        json.dumps(
            {
                "data": [
                    {
                        "id": command_id,
                        "display-name": "universal-video-productionize-34100000001",
                        "time-created": dt.datetime.now(dt.timezone.utc).isoformat(),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    harness = f"""\
set -euo pipefail
{function}
oci(){{
  case "$*" in
    *" compute instance get "*) printf '%s\\n' 'ocid1.compartment.oc1.synthetic' ;;
    *" instance-agent command list "*) command cat "$COMMANDS" ;;
    *" instance-agent command-execution get "*) command cat "$EXECUTION" ;;
    *) return 64 ;;
  esac
}}
RUNNER_TEMP={json.dumps(str(tmp_path))}
INSTANCE_ID=ocid1.instance.oc1.synthetic
verify_no_active_oracle_admin_commands
"""

    def run(state: str) -> subprocess.CompletedProcess[str]:
        execution.write_text(
            json.dumps({"data": {"lifecycle-state": state}}), encoding="utf-8"
        )
        return subprocess.run(
            ["bash"],
            input=harness,
            text=True,
            capture_output=True,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "COMMANDS": str(commands),
                "EXECUTION": str(execution),
            },
            timeout=10,
        )

    terminal = run("SUCCEEDED")
    assert terminal.returncode == 0, terminal.stderr
    assert "examined_recent=1 active_remote_commands=0 result=PASS" in terminal.stdout
    active = run("IN_PROGRESS")
    assert active.returncode != 0
    assert "not terminal: IN_PROGRESS" in active.stderr


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
