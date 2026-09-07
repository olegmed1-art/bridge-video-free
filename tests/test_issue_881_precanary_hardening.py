from __future__ import annotations

import copy
import datetime as dt
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
    attest = (ROOT / "ops/oracle_universal_video_precanary_attest.sh").read_text(
        encoding="utf-8"
    )
    installer = (ROOT / "ops/oracle_universal_video_container_install.sh").read_text(
        encoding="utf-8"
    )

    assert "approval_receipt_id:" in workflow and "approval_nonce:" in workflow
    assert "run-name: issue881-precanary/" in workflow
    assert workflow.count("issue_881_precanary_one_shot.py verify") == 2
    assert "'ops/oracle_universal_video_run_command.sh'" in workflow
    assert "'ops/oracle_known_hosts_from_scan.sh'" in workflow
    assert "'.github/workflows/oracle-universal-video-container-promote.yml'" in workflow
    assert "'ops/oracle_universal_video_container_promote.sh'" in workflow
    assert "'universal_video'" in workflow and "':(glob)bridge_*.py'" in workflow
    assert "GITHUB_RUN_ATTEMPT" in workflow
    assert "verify_no_competing_infrastructure_runs" in workflow
    assert "UNIVERSAL_VIDEO_PRECANARY_INFRASTRUCTURE_EXCLUSIVE" in workflow
    infrastructure_gate = workflow[
        workflow.index("verify_no_competing_infrastructure_runs(){") :
        workflow.index("# Initial reconciliation rejects historical")
    ]
    assert infrastructure_gate.count("actions/runs?per_page=100") == 1
    assert "status=in_progress" not in infrastructure_gate
    assert "status=queued" not in infrastructure_gate
    assert 'select(.status != "completed")' in infrastructure_gate
    assert "reported_total" in infrastructure_gate
    assert "loaded_total" in infrastructure_gate
    assert "unique_total" in infrastructure_gate
    assert "snapshot is incomplete or changed while paginating" in infrastructure_gate
    assert "UNIVERSAL_VIDEO_RECLAIM_ROOT_CACHE=1" not in workflow
    assert "UNIVERSAL_VIDEO_CONTAINER_ALLOW_CACHE_RECLAIM=0" in attest
    assert 'find "$root_cache" -xdev -mindepth 1 -delete' not in attest
    assert "verify_postrestore_runtime_queue" in attest
    assert "UNIVERSAL_VIDEO_QUEUE_PROOF_SHA256" in attest
    assert "'root:root:600:1'" in attest
    assert 'sha256sum "$QUEUE_PROOF_SCRIPT"' in attest
    assert "UNIVERSAL_VIDEO_PRECANARY_POSTRESTORE_RUNTIME" in attest
    assert "UNIVERSAL_VIDEO_PRECANARY_FENCED_START" in attest
    final_window = workflow.index("# Staging can outlive the evidence")
    final_live_gate = workflow.index("verify_live_gate", final_window)
    final_receipt = workflow.index("verify_one_shot_gate", final_live_gate)
    final_boundary = workflow.index("verify_final_mutation_boundary", final_receipt)
    host_attest = workflow.index('"${s[@]}" "sudo -n env', final_boundary)
    assert final_live_gate < final_receipt < final_boundary < host_attest
    boundary_definition = workflow.index("verify_final_mutation_boundary(){")
    boundary_infrastructure = workflow.index(
        'current_infrastructure_marker="$(verify_no_competing_infrastructure_runs)"',
        boundary_definition,
    )
    boundary_main = workflow.index("verify_exact_current_main", boundary_infrastructure)
    assert boundary_infrastructure < boundary_main
    fenced_start = attest.index("if start_container_under_fence; then", attest.index("cleanup(){"))
    runtime_proof = attest.index("verify_postrestore_runtime_queue", fenced_start)
    workload_unlock = attest.index("flock --unlock 9", runtime_proof)
    assert fenced_start < runtime_proof < workload_unlock
    unsafe_restore = attest.index(
        'if [[ "$runtime_release_safe" != 1 && "$lock_held" == 1 ]]',
        workload_unlock,
    )
    remask = attest.index(
        'bounded_systemctl mask --runtime "$SOURCE_SERVICE" "$CONTAINER_SERVICE"',
        unsafe_restore,
    )
    stop = attest.index(
        'bounded_systemctl stop "$SOURCE_SERVICE" "$CONTAINER_SERVICE"', remask
    )
    unsafe_unlock = attest.index("flock --unlock 9", stop)
    assert unsafe_restore < remask < stop < unsafe_unlock
    assert "declare -a inherited_failure_runtime_masks=()" in attest
    recovery_branch = attest.index(
        'if [[ -n "$RECOVER_CONTAINER_FROM_RUN" && "$container_state_before" != active ]]'
    )
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
    workflow = (
        ROOT / ".github/workflows/issue-881-authoritative-external-evidence.yml"
    ).read_text(encoding="utf-8")
    start = workflow.index("          verify_no_competing_infrastructure_runs(){")
    end = workflow.index("\n\n          # Initial reconciliation rejects historical", start)
    function = textwrap.dedent(workflow[start:end])
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
