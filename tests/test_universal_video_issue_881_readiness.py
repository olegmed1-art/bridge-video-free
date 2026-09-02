from __future__ import annotations

import fcntl
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

import pytest

from universal_video import neon_worker
from universal_video.result_contract import (
    ResultContractError,
    synthetic_result_contract_self_test,
    verify_drive_result_contract,
    verify_terminal_output_live,
)
from universal_video.workload_lock import LOCK_FILE_NAME, shared_workload_lock


ROOT = Path(__file__).resolve().parents[1]


def fixture():
    payload = b"%PDF-1.7\nissue-881\n"
    digest = hashlib.sha256(payload).hexdigest()
    meta = {
        "id": "result-file-123456",
        "name": "résultat-δ.pdf",
        "mimeType": "application/pdf",
        "size": str(len(payload)),
        "parents": ["output-folder-123456"],
        "md5Checksum": hashlib.md5(payload, usedforsecurity=False).hexdigest(),
    }
    claim = {
        "stable_job_key": "1" * 32,
        "source_file_id": "source-file-123456",
        "output_folder_id": "output-folder-123456",
        "algorithm_revision": neon_worker.APPROVED_REVISION,
    }
    done = {"masterPdf": {"driveId": meta["id"], "sha256": digest}}

    def metadata(_id: str, _token: str) -> Mapping[str, Any]:
        return dict(meta)

    def download(_id: str, dest: Path, _token: str, **_: Any) -> Mapping[str, Any]:
        dest.write_bytes(payload)
        result = dict(meta)
        result["_download_sha256"] = digest
        result["_download_md5"] = meta["md5Checksum"]
        return result

    return digest, meta, claim, done, metadata, download


def terminal_candidate():
    digest, _meta, item, done, metadata, download = fixture()
    evidence = verify_drive_result_contract(
        item,
        done,
        token="test",
        metadata_reader=metadata,
        downloader=download,
    )
    candidate = {
        "result_mode": "SHADOW_REVIEW_ONLY",
        "canonical_promotion_allowed": False,
        "database_persistence_allowed": False,
        "publication_state": "NOT_PUBLISHED",
        "source_file_id": item["source_file_id"],
        "stable_job_key": item["stable_job_key"],
        "algorithm_revision": item["algorithm_revision"],
        **evidence,
    }
    return digest, item, candidate, metadata, download


def test_synthetic_contract_is_readback_bound():
    result = synthetic_result_contract_self_test()
    receipt = result["terminal_receipt"]
    assert receipt["status"] == "PASS" and receipt["drive_readback_verified"] is True
    assert receipt["publication_state"] == "NOT_PUBLISHED"
    assert receipt["canonical_promotion_allowed"] is False
    assert result["artifact_manifest_sha256"] == receipt["artifact_manifest_sha256"]


def test_real_contract_shape_passes_only_after_download():
    digest, _, item, done, metadata, download = fixture()
    result = verify_drive_result_contract(
        item,
        done,
        token="test",
        metadata_reader=metadata,
        downloader=download,
    )
    assert result["artifact_manifest"]["artifacts"][0]["sha256"] == digest
    assert result["terminal_receipt"]["status"] == "PASS"
    canonical = json.dumps(
        result["artifact_manifest"],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == result["artifact_manifest_sha256"]


def test_terminal_output_live_rebuilds_evidence_from_drive():
    digest, item, candidate, metadata, download = terminal_candidate()
    rebuilt = verify_terminal_output_live(
        item,
        candidate,
        token="fresh-token",
        metadata_reader=metadata,
        downloader=download,
    )
    assert rebuilt["master_pdf_sha256"] == digest
    assert rebuilt["terminal_receipt"]["drive_readback_verified"] is True


def test_terminal_output_live_rejects_self_consistent_but_unreadable_mapping():
    _, item, candidate, metadata, _download = terminal_candidate()

    def unreadable(*_args: Any, **_kwargs: Any) -> Mapping[str, Any]:
        raise OSError("synthetic readback failure")

    with pytest.raises(ResultContractError) as caught:
        verify_terminal_output_live(
            item,
            candidate,
            token="fresh-token",
            metadata_reader=metadata,
            downloader=unreadable,
        )
    assert caught.value.error_code == "UV_RESULT_DRIVE_READBACK_FAILED"


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("unreadable", "UV_RESULT_DRIVE_READBACK_FAILED"),
        ("checksum", "UV_RESULT_CHECKSUM_MISMATCH"),
        ("parent", "UV_RESULT_METADATA_MISMATCH"),
        ("changed", "UV_RESULT_METADATA_CHANGED_DURING_READBACK"),
    ],
)
def test_result_contract_fail_closed(mode, code):
    _, _meta, item, done, metadata, download = fixture()
    calls = 0

    def bad_meta(file_id: str, token: str):
        nonlocal calls
        calls += 1
        result = dict(metadata(file_id, token))
        if mode == "parent":
            result["parents"] = ["wrong-folder"]
        if mode == "changed" and calls > 1:
            result["name"] = "changed.pdf"
        return result

    def bad_download(file_id: str, dest: Path, token: str, **kwargs: Any):
        if mode == "unreadable":
            raise OSError("simulated")
        result = dict(download(file_id, dest, token, **kwargs))
        if mode == "checksum":
            result["_download_sha256"] = "f" * 64
        return result

    with pytest.raises(ResultContractError) as exc:
        verify_drive_result_contract(
            item,
            done,
            token="test",
            metadata_reader=bad_meta,
            downloader=bad_download,
        )
    assert exc.value.error_code == code


def claim():
    return {
        "job_id": "job-123",
        "batch_id": "batch-123",
        "lease_token": "lease-123",
        "sequence": 1,
        "source_folder_id": "source-folder-123",
        "output_folder_id": "output-folder-123",
        "work_folder_id": "work-folder-123",
        "processing_profile": neon_worker.APPROVED_PROFILE,
        "algorithm_revision": neon_worker.APPROVED_REVISION,
        "source_file_id": "source-file-123",
        "source_name": "source.mp4",
        "source_mime_type": "video/mp4",
        "source_size_bytes": 12345678,
        "source_checksum": "md5:" + "a" * 32,
        "stable_job_key": "1" * 32,
        "is_canary": True,
        "attempt_count": 1,
    }


def patch_source_gate(monkeypatch):
    observed = {
        "id": "source-file-123",
        "name": "source.mp4",
        "mime_type": "video/mp4",
        "size_bytes": 12345678,
        "parents": ["source-folder-123"],
        "checksum": "md5:" + "a" * 32,
    }
    monkeypatch.setattr(neon_worker, "access_token", lambda: "token")
    monkeypatch.setattr(neon_worker, "verify_claimed_source", lambda *_args: dict(observed))
    return observed


@pytest.mark.parametrize(
    "failure",
    [
        ResultContractError("UV_RESULT_DRIVE_READBACK_FAILED"),
        neon_worker.NeonVideoTimeoutError("VIDEO_QUEUE_PROCESSING_TIMEOUT"),
    ],
)
def test_timeout_or_readback_failure_retries_without_finish(monkeypatch, failure):
    patch_source_gate(monkeypatch)
    events = []
    monkeypatch.setattr(
        neon_worker,
        "retry_job",
        lambda *_a, **k: (events.append("retry:" + k["error_code"]) or {"job_status": "QUEUED"}),
    )
    monkeypatch.setattr(
        neon_worker,
        "finish_job",
        lambda *_a, **_k: (events.append("finish") or {"job_status": "REVIEW_READY"}),
    )
    result = neon_worker.process_claim(
        "postgres://unused",
        claim(),
        "worker-1",
        processor=lambda _c: (_ for _ in ()).throw(failure),
    )
    assert result["job_status"] == "QUEUED"
    assert events[0].startswith("retry:UV_")
    assert "finish" not in events


def test_custom_processor_cannot_bypass_live_artifact_readback(monkeypatch):
    patch_source_gate(monkeypatch)
    events = []
    monkeypatch.setattr(
        neon_worker,
        "verify_terminal_output_live",
        lambda *_a, **_k: (_ for _ in ()).throw(
            ResultContractError("UV_RESULT_DRIVE_READBACK_FAILED")
        ),
    )
    monkeypatch.setattr(
        neon_worker,
        "retry_job",
        lambda *_a, **k: (events.append("retry:" + k["error_code"]) or {"job_status": "QUEUED"}),
    )
    monkeypatch.setattr(
        neon_worker,
        "finish_job",
        lambda *_a, **_k: (events.append("finish") or {"job_status": "REVIEW_READY"}),
    )
    result = neon_worker.process_claim(
        "postgres://unused",
        claim(),
        "worker-1",
        processor=lambda _c: {
            "artifact_manifest": {"forged": True},
            "artifact_manifest_sha256": "f" * 64,
            "terminal_receipt": {"status": "PASS"},
        },
    )
    assert result["job_status"] == "QUEUED"
    assert events == ["retry:UV_RESULT_DRIVE_READBACK_FAILED"]


@pytest.mark.parametrize("terminal_failure", [False, True])
def test_terminal_gate_and_queue_transition_remain_inside_lease_guards(
    monkeypatch,
    terminal_failure,
):
    patch_source_gate(monkeypatch)
    state = {"heartbeat": 0, "timeout": 0}

    class TrackingHeartbeat:
        def __init__(self, *_args, **_kwargs):
            self.error = None

        def __enter__(self):
            state["heartbeat"] += 1
            return self

        def __exit__(self, *_args):
            state["heartbeat"] -= 1

    @contextmanager
    def tracking_timeout():
        state["timeout"] += 1
        try:
            yield
        finally:
            state["timeout"] -= 1

    def assert_guarded():
        assert state == {"heartbeat": 1, "timeout": 1}

    def live_gate(*_args, **_kwargs):
        assert_guarded()
        if terminal_failure:
            raise ResultContractError("UV_RESULT_DRIVE_READBACK_FAILED")
        return {}

    def finish(_dsn, **kwargs):
        assert_guarded()
        return {"job_status": kwargs["outcome"]}

    def retry(_dsn, **kwargs):
        assert_guarded()
        return {"job_status": "QUEUED", "error_code": kwargs["error_code"]}

    monkeypatch.setattr(neon_worker, "_Heartbeat", TrackingHeartbeat)
    monkeypatch.setattr(neon_worker, "_processing_timeout", tracking_timeout)
    monkeypatch.setattr(neon_worker, "verify_terminal_output_live", live_gate)
    monkeypatch.setattr(neon_worker, "finish_job", finish)
    monkeypatch.setattr(neon_worker, "retry_job", retry)
    result = neon_worker.process_claim(
        "postgres://unused",
        claim(),
        "worker-1",
        processor=lambda _c: {},
    )
    assert result["job_status"] == ("QUEUED" if terminal_failure else "REVIEW_READY")
    assert state == {"heartbeat": 0, "timeout": 0}


def test_interrupted_worker_never_finishes(monkeypatch):
    patch_source_gate(monkeypatch)
    events = []
    monkeypatch.setattr(neon_worker, "retry_job", lambda *_a, **_k: events.append("retry"))
    monkeypatch.setattr(neon_worker, "finish_job", lambda *_a, **_k: events.append("finish"))
    with pytest.raises(KeyboardInterrupt):
        neon_worker.process_claim(
            "postgres://unused",
            claim(),
            "worker-1",
            processor=lambda _c: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
    assert events == []


def test_attestation_exclusive_lock_blocks_worker_claim_path(tmp_path: Path):
    spool = tmp_path / "spool"
    spool.mkdir()
    lock_path = spool / LOCK_FILE_NAME
    lock_path.touch(mode=0o640)
    with lock_path.open("r", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            with shared_workload_lock(spool, blocking=False):
                pass
    with shared_workload_lock(spool, blocking=False):
        pass


def test_startup_recovery_exclusive_lock_serializes_residents(tmp_path: Path):
    spool = tmp_path / "spool"
    spool.mkdir()
    with shared_workload_lock(spool, blocking=False):
        with pytest.raises(BlockingIOError):
            with shared_workload_lock(spool, blocking=False, exclusive=True):
                pass


def test_precanary_fences_quiesces_restores_and_uses_captured_image_id():
    script = (ROOT / "ops/oracle_universal_video_precanary_attest.sh").read_text(encoding="utf-8")
    run_image = script[
        script.index("run_image(){") : script.index("verify_image_identity\nassert_quiescent", script.index("run_image(){"))
    ]
    assert 'mask_service_for_window "$SOURCE_SERVICE"' in script
    assert 'mask_service_for_window "$CONTAINER_SERVICE"' in script
    assert "flock --exclusive --nonblock 9" in script
    assert 'restore_service "$SOURCE_SERVICE" "$source_state_before"' in script
    assert 'restore_service "$CONTAINER_SERVICE" "$container_target_state"' in script
    assert 'source_candidate_path_owned=0' in script
    assert 'source_candidate_path_owned=1' in script
    assert '"$source_candidate_path_owned" == 1' in script
    assert "active container resident image is missing or ambiguous" in script
    assert 'UNIVERSAL_VIDEO_CONTAINER_PRESERVE_IMAGE_ID="$resident_image_id"' in script
    assert 'rm -f -- "$ENV_FILE"' in script
    assert '"$ENV_FILE" == "$BASE_DIR/universal-video-container-candidate.env"' in script
    assert 'systemctl stop "$SOURCE_SERVICE" "$CONTAINER_SERVICE"' in script
    assert '"$image_id" "$@"' in run_image and '"$image" "$@"' not in run_image
    assert "org.opencontainers.image.revision" in script
    assert "UNIVERSAL_VIDEO_PREWARM_MODEL=0" in script
    assert "UNIVERSAL_VIDEO_RUN_SMOKE=0" in script
    assert 'find "$root_cache" -xdev -mindepth 1 -delete' in script
    assert "assert_pre_stop_idle" in script
    assert "video_queue.precanary_idle_snapshot()" in script
    assert "authoritative Neon claimable/LEASED state is busy or unverifiable" in script
    assert "pid_descends_from" in script
    assert "resident_worker_pid" in script
    assert "restored_service_ready" in script
    assert "RESTORE_STABLE_SECONDS" in script
    assert "stable_seconds=%s result=PASS" in script
    assert "services_stop_attempted=1" in script
    assert "verify_prior_recovery_evidence" in script
    assert "immutable prior-run recovery evidence digest mismatch" in script

    lock_index = script.index("flock --exclusive --nonblock 9")
    stop_index = script.index(
        'systemctl stop "$SOURCE_SERVICE" "$CONTAINER_SERVICE"',
        lock_index,
    )
    prestop_index = script.index("assert_pre_stop_idle", lock_index)
    prepare_index = script.index('bash "$PREPARE_SCRIPT"', stop_index)
    cleanup_index = script.index('find "$root_cache" -xdev -mindepth 1 -delete', stop_index)
    installer_index = script.index(
        'bash "$SOURCE_DIR/ops/oracle_universal_video_container_install.sh"',
        prepare_index,
    )
    run_index = script.index("run_image python", installer_index)
    assert lock_index < prestop_index < stop_index < prepare_index < installer_index < run_index
    assert stop_index < cleanup_index < installer_index

    source_worker = (ROOT / "universal_video/spool_worker.py").read_text(encoding="utf-8")
    neon_worker_source = (ROOT / "universal_video/neon_worker.py").read_text(encoding="utf-8")
    assert "with shared_workload_lock(spool_root):" in source_worker
    assert "with shared_workload_lock(spool_root, exclusive=True):" in source_worker
    assert "with shared_workload_lock():" in neon_worker_source

    restore_source_index = script.index(
        'restore_service "$SOURCE_SERVICE" "$source_state_before"'
    )
    restore_container_index = script.index(
        'restore_service "$CONTAINER_SERVICE" "$container_target_state"'
    )
    restore_pass_index = script.index("UNIVERSAL_VIDEO_PRECANARY_RESTORE_PASS")
    source_recheck_index = script.index(
        '[[ "$source_after" == "$source_state_before" ]]'
    )
    container_recheck_index = script.index(
        '[[ "$container_after" == "$container_target_state" ]]'
    )
    readiness_recheck_index = script.index(
        'restored_service_ready "$SOURCE_SERVICE" "$source_state_before"',
        container_recheck_index,
    )
    unlock_index = script.index("flock --unlock 9", script.index("cleanup(){"))
    assert (
        restore_source_index
        > unlock_index
        and restore_source_index
        < restore_container_index
        < source_recheck_index
        < container_recheck_index
        < readiness_recheck_index
        < restore_pass_index
    )


def test_installer_readiness_and_service_env_use_captured_image_id():
    installer = (ROOT / "ops/oracle_universal_video_container_install.sh").read_text(encoding="utf-8")
    assert "UNIVERSAL_VIDEO_IMAGE=$image_id" in installer
    readiness = installer[installer.index("log 'Run container-only readiness gate") :]
    assert '"$image_id" true' in readiness
    assert '"$image" true' not in readiness
    assert "org.opencontainers.image.revision" in readiness
    assert 'CANDIDATE_ENV_FILE="$BASE_DIR/universal-video-container-candidate.env"' in installer
    assert '[[ "$ACTIVATE" == 1 ]] || ENV_FILE="$CANDIDATE_ENV_FILE"' in installer
    assert 'PRESERVE_IMAGE_ID="${UNIVERSAL_VIDEO_CONTAINER_PRESERVE_IMAGE_ID:-}"' in installer
    assert '"$old_image_id" == "$PRESERVE_IMAGE_ID"' in installer
    assert '--env-file "$ENV_FILE"' in installer
    activation = installer[
        installer.index(
            'if [[ "$ACTIVATE" == 1 ]]; then',
            installer.index("log 'Run container-only readiness gate"),
        ) :
    ]
    assert 'install -m 0644 -o root -g root' in activation
    assert 'systemctl daemon-reload' in activation


def test_external_precanary_runs_same_repo_and_compares_install_digest():
    workflow = (ROOT / ".github/workflows/issue-881-precanary-evidence.yml").read_text(
        encoding="utf-8"
    )
    condition = (
        "if: github.event_name == 'workflow_dispatch' || "
        "github.event.pull_request.head.repo.full_name == github.repository"
    )
    assert workflow.count(condition) == 1
    assert workflow.splitlines().count(
        "        if: github.event_name == 'workflow_dispatch'"
    ) == 3
    assert "        if: always()" not in workflow
    assert "if: github.event_name == 'workflow_dispatch' && always()" in workflow
    assert "UNIVERSAL_VIDEO_EXPECTED_SHA='$EXACT_SHA'" in workflow
    assert "UNIVERSAL_VIDEO_PRECANARY_BUILD_IMAGE=1" in workflow
    assert "UNIVERSAL_VIDEO_RECLAIM_ROOT_CACHE=1" in workflow
    assert "UNIVERSAL_VIDEO_PREPARE_SCRIPT='$remote_root/prepare.sh'" in workflow
    assert 'attested_digest="$(sed' in workflow
    assert '"$attested_digest" == "$installed_digest"' in workflow
    assert "198-2v3JBlNQobdsPYQQWzrrCqQ1zBZOI" in workflow
    assert "Диана 13.mp4" in workflow
    assert "696237577" in workflow
    assert "1Fr-H2NgBKEpp3q_H4FzNmQwCV6bj2x6b" in workflow
    stopped_branch = workflow[
        workflow.index("            STOPPED)") : workflow.index("            *)", workflow.index("            STOPPED)"))
    ]
    assert "--action START" not in stopped_branch
    assert "explicit Director lifecycle authorization is required" in stopped_branch


def test_authoritative_external_evidence_binds_live_reviewed_head_and_recovery():
    workflow = (
        ROOT / ".github/workflows/issue-881-authoritative-external-evidence.yml"
    ).read_text(encoding="utf-8")
    assert 'pulls/1062" --jq \'.head.sha\'' in workflow
    assert ".commit_id ==" in workflow and "$EXACT_SHA" in workflow
    assert "required_workflows=(" in workflow
    assert "verify_live_gate(){" in workflow
    assert workflow.count("verify_live_gate") == 3
    assert "reviewThreads(first:100)" in workflow
    assert "unresolved current threads" in workflow
    assert "max_by([.run_number, .run_attempt])" in workflow
    assert "--action START" not in workflow
    assert "Oracle instance is STOPPED" in workflow
    assert "actions/runs/$RECOVER_CONTAINER_FROM_RUN" in workflow
    assert ".github/workflows/issue-881-authoritative-external-evidence.yml" in workflow
    assert "prior-recovery-evidence.txt" in workflow
    assert "UNIVERSAL_VIDEO_RECOVERY_EVIDENCE_SHA256='$recovery_sha'" in workflow
    final_head_check = workflow.rindex("          verify_live_gate")
    first_remote_mutation = workflow.index(
        '"${s[@]}" "umask 077; rm -rf', final_head_check
    )
    assert final_head_check < first_remote_mutation
