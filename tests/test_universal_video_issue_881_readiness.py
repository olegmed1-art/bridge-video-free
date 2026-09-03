from __future__ import annotations

import fcntl
import hashlib
import json
import subprocess
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
    assert "freeze_residents_for_idle_snapshot" in script
    assert "stop_frozen_residents" in script
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
    assert 'bounded_systemctl stop "$SOURCE_SERVICE" "$CONTAINER_SERVICE"' in script
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
    assert '[[ "${#matches[@]}" -eq 1 ]] || return 1' in script
    assert "restored_service_ready" in script
    assert "resident_status_ready" in script
    assert "universal-video-resident-status-v2" in script
    assert 'observed_at >= int(os.environ["STARTED_UNIX"])' in script
    assert 'value.get("installed_runtime_commit") == os.environ["EXPECTED_COMMIT"]' in script
    assert 'value.get("resident_id") == os.environ["EXPECTED_RESIDENT"]' in script
    assert 'expected_process_id="$worker_pid"' in script
    assert 'awk \'$1 == "NSpid:" {print $NF}\' "/proc/$worker_pid/status"' in script
    assert 'EXPECTED_PROCESS_ID="$expected_process_id"' in script
    assert 'value["process_id"] == int(os.environ["EXPECTED_PROCESS_ID"])' in script
    assert 'expected_process_start_ticks="$(process_start_ticks "$worker_pid"' in script
    assert 'EXPECTED_PROCESS_START_TICKS="$expected_process_start_ticks"' in script
    assert 'value["process_start_ticks"] == int(os.environ["EXPECTED_PROCESS_START_TICKS"])' in script
    assert 'process_started_at >= int(os.environ["STARTED_UNIX"])' in script
    assert 're.fullmatch(r"[0-9a-f]{32}", value["process_nonce"])' in script
    assert "identity_fields = {" in script
    assert 'transitional = identity_fields - {"process_start_ticks"}' in script
    assert "if not present:" in script
    assert "elif present == transitional or present == strong:" in script
    assert 'os.environ["LEGACY_PEER_SAME_COMMIT"] != "0"' in script
    assert "observed_at <= time.time() + 5" in script
    assert 'value.get("active_jobs") == []' in script
    assert 'if ! clear_restore_status; then' in script
    assert script.count('if ! clear_restore_status; then') == 2
    assert "RESTORE_STABLE_SECONDS" in script
    assert "stable_seconds=%s result=PASS" in script
    assert "services_stop_attempted=1" in script
    assert "verify_prior_recovery_evidence" in script
    assert "immutable prior-run recovery evidence digest mismatch" in script

    lock_index = script.index("flock --exclusive --nonblock 9")
    freeze_index = script.index("freeze_residents_for_idle_snapshot", lock_index)
    prestop_index = script.index("assert_pre_stop_idle", lock_index)
    stop_index = script.index("stop_frozen_residents", prestop_index)
    prepare_index = script.index('bash "$PREPARE_SCRIPT"', stop_index)
    cleanup_index = script.index('find "$root_cache" -xdev -mindepth 1 -delete', stop_index)
    installer_index = script.index(
        'bash "$SOURCE_DIR/ops/oracle_universal_video_container_install.sh"',
        prepare_index,
    )
    run_index = script.index("run_image python", installer_index)
    assert (
        lock_index
        < freeze_index
        < prestop_index
        < stop_index
        < prepare_index
        < installer_index
        < run_index
    )
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
        '[[ "$source_after" == "$source_state_before" ]]',
        restore_container_index,
    )
    container_recheck_index = script.index(
        '[[ "$container_after" == "$container_target_state" ]]'
    )
    readiness_recheck_index = script.index(
        'restored_service_ready "$SOURCE_SERVICE" "$source_state_before"',
        container_recheck_index,
    )
    unlock_index = script.index("flock --unlock 9", script.index("cleanup(){"))
    restore_body = script[
        script.index("restore_service(){") : script.index("restore_source_checkout(){")
    ]
    assert 'verified_start_ticks="$(resident_status_ready "$service" "$started_unix" "$worker_pid"' in restore_body
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


def test_same_revision_legacy_restore_quiesces_only_the_peer_status_writer():
    script = (ROOT / "ops/oracle_universal_video_precanary_attest.sh").read_text(
        encoding="utf-8"
    )
    isolation = script[
        script.index("isolate_ambiguous_legacy_peer(){") :
        script.index("resident_status_ready(){")
    ]
    resume = script[
        script.index("resume_isolated_peer(){") :
        script.index("isolate_ambiguous_legacy_peer(){")
    ]
    quiesced_check = script[
        script.index("isolated_peer_still_quiesced(){") :
        script.index("resident_status_ready(){")
    ]
    receipt = script[
        script.index("resident_status_ready(){") : script.index("record_restore_failure(){")
    ]
    restore = script[
        script.index("restore_service(){") : script.index("restore_source_checkout(){")
    ]
    cleanup = script[script.index("cleanup(){") : script.index("assert_known_state(){")]

    assert 'target_commit="$(resident_expected_commit "$service")"' in isolation
    assert 'peer_commit="$(resident_expected_commit "$peer_service")"' in isolation
    assert '"$target_commit" != "$peer_commit"' in isolation
    assert 'peer_pid="$(resident_worker_pid "$peer_service"' in isolation
    assert 'peer_ticks="$(process_start_ticks "$peer_pid"' in isolation
    assert 'exact_process_signal "$peer_pid" "$peer_ticks" STOP' in isolation
    assert '"$(resident_worker_pid "$peer_service"' in isolation
    assert '"$(process_start_ticks "$peer_pid"' in isolation
    assert 'exact_process_signal "$isolated_peer_pid" "$isolated_peer_start_ticks" CONT' in resume
    assert '"$worker_pid" == "$isolated_peer_pid"' in resume
    assert '"$isolated_peer_start_ticks"' in resume
    assert '"$isolated_peer_service" == "$peer_service"' in quiesced_check
    assert '"$state" == T || "$state" == t' in quiesced_check
    assert '"$(resident_worker_pid "$peer_service"' in quiesced_check
    assert '"$(process_start_ticks "$isolated_peer_pid"' in quiesced_check
    assert receipt.count('isolated_peer_still_quiesced "$peer_service"') == 2

    isolate_index = restore.index('isolate_ambiguous_legacy_peer "$service"')
    clear_index = restore.index("if ! clear_restore_status; then", isolate_index)
    start_index = restore.index('bounded_systemctl start --no-block "$service"', clear_index)
    receipt_index = restore.index(
        'verified_start_ticks="$(resident_status_ready "$service" "$started_unix" "$worker_pid" '
        '"$legacy_peer_quiesced"',
        start_index,
    )
    resume_index = restore.index("if ! resume_isolated_peer; then", receipt_index)
    identity_recheck_index = restore.index(
        '"$(resident_worker_pid "$service"', resume_index
    )
    assert isolate_index < clear_index < start_index < receipt_index
    assert receipt_index < resume_index < identity_recheck_index

    assert 'LEGACY_PEER_QUIESCED="$legacy_peer_quiesced"' in script
    assert 'os.environ["LEGACY_PEER_QUIESCED"] != "1"' in script
    assert 'restored_source_pid="$worker_pid"' in restore
    assert 'restored_container_pid="$worker_pid"' in restore
    assert '"$restored_source_pid" "$restored_source_start_ticks"' in cleanup
    assert '"$restored_container_pid" "$restored_container_start_ticks"' in cleanup

    first_identity_check = receipt.index(
        'exact_process_signal "$worker_pid" "$expected_process_start_ticks" CHECK'
    )
    status_check = receipt.index("STATUS_PATH=", first_identity_check)
    second_identity_check = receipt.index(
        'exact_process_signal "$worker_pid" "$expected_process_start_ticks" CHECK',
        status_check,
    )
    bound_ticks = receipt.index("printf '%s\\n' \"$expected_process_start_ticks\"")
    assert first_identity_check < status_check < second_identity_check < bound_ticks
    assert 'verified_start_ticks="$(process_start_ticks' not in restore


def test_status_receipt_returns_exact_identity_binding_and_rejects_same_pid_replacement(
    tmp_path: Path,
) -> None:
    script = (ROOT / "ops/oracle_universal_video_precanary_attest.sh").read_text(
        encoding="utf-8"
    )
    receipt = script[
        script.index("resident_status_ready(){") : script.index("record_restore_failure(){")
    ]
    status_file = tmp_path / "resident-status.json"
    status_file.write_text(
        '{"schema":"universal-video-resident-status-v2",'
        '"instance_state":"RUNNING","active_jobs":[],'
        '"observed_at_unix":1001,"installed_runtime_commit":"' + "a" * 40 + '",'
        '"resident_id":"source","process_id":111,'
        '"process_started_at_unix":1000,"process_start_ticks":222,'
        '"process_nonce":"' + "b" * 32 + '"}',
        encoding="utf-8",
    )
    probe = receipt + rf'''
set -euo pipefail
SOURCE_SERVICE=source.service
CONTAINER_SERVICE=container.service
STATUS_FILE={status_file!s}
current_ticks=222
process_start_ticks(){{ printf '%s\n' "$current_ticks"; }}
exact_process_signal(){{ [[ "$2" == "$current_ticks" ]]; }}
resident_expected_commit(){{ printf '%040d\n' 0 | tr 0 a; }}
service_state(){{ printf '%s\n' inactive; }}
resident_worker_pid(){{ printf '%s\n' 111; }}
isolated_peer_still_quiesced(){{ return 1; }}
bound="$(resident_status_ready source.service 999 111 0)"
[[ "$bound" == 222 ]]
current_ticks=333
! exact_process_signal 111 "$bound" CHECK
'''
    completed = subprocess.run(
        ["bash"],
        input=probe,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


def test_prestop_freeze_closes_the_legacy_claim_race_before_shutdown():
    script = (ROOT / "ops/oracle_universal_video_precanary_attest.sh").read_text(
        encoding="utf-8"
    )
    freeze = script[
        script.index("freeze_residents_for_idle_snapshot(){") :
        script.index("stop_frozen_residents(){")
    ]
    stop = script[
        script.index("stop_frozen_residents(){") :
        script.index("resume_isolated_peer(){")
    ]
    resume_frozen = script[
        script.index("resume_prestop_frozen(){") :
        script.index("freeze_residents_for_idle_snapshot(){")
    ]
    execution = script[script.index("lock_held=1") :]
    cleanup = script[script.index("cleanup(){") : script.index("assert_known_state(){")]

    assert 'process_id="$(resident_worker_pid "$service"' in freeze
    assert 'start_ticks="$(process_start_ticks "$process_id"' in freeze
    assert 'exact_process_signal "$process_id" "$start_ticks" STOP' in freeze
    assert '"$(resident_worker_pid "$service"' in freeze
    assert '"$(process_start_ticks "$process_id"' in freeze
    assert 'bounded_systemctl stop --no-block "$SOURCE_SERVICE" "$CONTAINER_SERVICE"' in resume_frozen
    assert 'exact_process_signal "$process_id" "$expected_ticks" TERM' in resume_frozen
    assert 'resume_prestop_frozen stopping' in stop
    assert resume_frozen.index("bounded_systemctl stop --no-block") < resume_frozen.index(
        'exact_process_signal "$process_id" "$expected_ticks" TERM'
    )
    assert resume_frozen.index(
        'exact_process_signal "$process_id" "$expected_ticks" TERM'
    ) < resume_frozen.index(
        '(( rc == 0 )) || return "$rc"'
    ) < resume_frozen.index(
        'exact_process_signal "$process_id" "$expected_ticks" CONT'
    )
    assert 'remaining_pids+=("$process_id")' in resume_frozen
    assert 'prestop_frozen_pids=("${remaining_pids[@]}")' in resume_frozen
    assert 'if [[ "$mode" == stopping ]]; then' in resume_frozen
    assert "not proof that it exited" in resume_frozen
    assert '[[ "$mode" == stopping ]] || rc=1' in resume_frozen
    assert 'exact_process_signal "$process_id" "$expected_ticks" CONT || true' not in resume_frozen
    assert stop.index("resume_prestop_frozen stopping") < stop.index(
        'bounded_systemctl stop "$SOURCE_SERVICE" "$CONTAINER_SERVICE"'
    )
    assert stop.index(
        'bounded_systemctl stop "$SOURCE_SERVICE" "$CONTAINER_SERVICE"'
    ) < stop.index("confirm_prestop_identities_exited") < stop.index(
        "residents_are_quiescent"
    )

    freeze_index = execution.index("freeze_residents_for_idle_snapshot")
    idle_index = execution.index("assert_pre_stop_idle", freeze_index)
    attempted_index = execution.index("services_stop_attempted=1", idle_index)
    stop_index = execution.index("stop_frozen_residents", attempted_index)
    assert freeze_index < idle_index < attempted_index < stop_index
    assert "trap '' INT TERM" in cleanup
    assert "PRESTOP_ABORT_RESTORE_PASS" in cleanup
    assert "prestop_preserve_requires_restart" in cleanup
    preserve_failure_start = cleanup.index("if ! resume_prestop_frozen preserve; then")
    preserve_failure = cleanup[
        preserve_failure_start : cleanup.index(
            'if [[ "${#prestop_frozen_pids[@]}" -gt 0 ]]',
            preserve_failure_start,
        )
    ]
    assert "record_restore_failure prestop_resume" in preserve_failure
    assert "services_stop_attempted=1" in preserve_failure
    stop_completion = cleanup.index("stop_frozen_residents")
    quiescence_gate = cleanup.index("! residents_are_quiescent", stop_completion)
    unmask_after_stop = cleanup.index("bounded_systemctl unmask --runtime", quiescence_gate)
    unlock_after_stop = cleanup.index("flock --unlock 9", unmask_after_stop)
    first_restore_start = cleanup.index("restore_service", unlock_after_stop)
    assert stop_completion < quiescence_gate < unmask_after_stop < unlock_after_stop < first_restore_start


def test_freeze_failure_forces_bounded_restart_even_after_preserve_clears_identities() -> None:
    script = (ROOT / "ops/oracle_universal_video_precanary_attest.sh").read_text(
        encoding="utf-8"
    )
    freeze = script[
        script.index("freeze_residents_for_idle_snapshot(){") :
        script.index("stop_frozen_residents(){")
    ]
    assert freeze.count("if ! resume_prestop_frozen preserve") == 3
    assert freeze.count("services_stop_attempted=1") == 3
    probe = freeze + r'''
set -euo pipefail
SOURCE_SERVICE=source.service
CONTAINER_SERVICE=container.service
source_state_before=active
container_state_before=inactive
restored_source_pid=
restored_source_start_ticks=
restored_container_pid=
restored_container_start_ticks=
sleep(){ :; }
resume_prestop_frozen(){
  prestop_frozen_services=()
  prestop_frozen_pids=()
  prestop_frozen_start_ticks=()
  return 1
}
resident_worker_pid(){ [[ "$scenario" != invalid ]] && printf '%s\n' 111; }
process_start_ticks(){ [[ "$scenario" != invalid ]] && printf '%s\n' 222; }
exact_process_signal(){ [[ "$scenario" != stop_failure ]]; }
process_state(){ printf '%s\n' S; }
run_failure(){
  prestop_frozen_services=()
  prestop_frozen_pids=()
  prestop_frozen_start_ticks=()
  services_stop_attempted=0
  ! freeze_residents_for_idle_snapshot
  [[ "$services_stop_attempted" == 1 ]]
  [[ "${#prestop_frozen_pids[@]}" -eq 0 ]]
}
scenario=invalid
run_failure
scenario=stop_failure
run_failure
scenario=timeout
run_failure
'''
    completed = subprocess.run(
        ["bash"],
        input=probe,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


def test_signal_ignored_cleanup_bounds_exact_source_tree_restore() -> None:
    script = (ROOT / "ops/oracle_universal_video_precanary_attest.sh").read_text(
        encoding="utf-8"
    )
    restore = script[
        script.index("restore_source_checkout(){") : script.index("cleanup(){")
    ]
    wrapper = script[
        script.index("bounded_filesystem(){") : script.index("service_state(){")
    ]

    assert "timeout --foreground --signal=TERM --kill-after=5s 30s" in wrapper
    assert 'bounded_filesystem rm -rf --one-file-system -- "$SOURCE_DIR"' in restore
    assert 'bounded_filesystem mv -T -- "$SOURCE_DIR" "$source_quarantine_dir"' in restore
    assert 'bounded_filesystem mv -T -- "$source_backup_dir" "$SOURCE_DIR"' in restore
    assert "source_candidate_quarantined" in restore
    assert "source_backup_device_inode" in restore
    assert "result=DEGRADED quarantine=created" in restore
    assert "result=DEGRADED quarantine=%s" not in restore
    assert '\n    rm -rf --one-file-system -- "$SOURCE_DIR"' not in restore
    assert '\n    mv -- "$source_backup_dir" "$SOURCE_DIR"' not in restore
    cleanup = script[script.index("cleanup(){") : script.index("assert_known_state(){")]
    restore_call = cleanup.index("if ! restore_source_checkout; then")
    source_failure_exit = cleanup.index("exit 1", restore_call)
    unmask = cleanup.index("bounded_systemctl unmask --runtime", restore_call)
    assert restore_call < source_failure_exit < unmask
    assert unmask < cleanup.index('restore_service "$SOURCE_SERVICE"', unmask)

    signal_guard = script[
        script.index("exact_process_signal(){") :
        script.index("resume_prestop_frozen(){")
    ]
    assert "pidfd = os.pidfd_open(pid, 0)" in signal_guard
    assert "signal.pidfd_send_signal(pidfd, signal_value, None, 0)" in signal_guard
    assert signal_guard.index("os.pidfd_open") < signal_guard.index(
        'Path(f"/proc/{pid}/stat")'
    ) < signal_guard.index("signal.pidfd_send_signal")
    assert "kill -STOP" not in script
    assert "kill -TERM" not in script
    assert "kill -CONT" not in script
    assert 'state" != Z' in script and 'state" != X' in script
    assert "timeout --foreground --signal=TERM --kill-after=5s" in script
    assert "while (( SECONDS < deadline ))" in script
    assert 'bounded_systemctl start --no-block "$service"' in script
    assert 'exact_process_signal "$worker_pid" "$expected_start_ticks" CHECK' in script


def test_source_scope_guard_rejects_aliases_and_accepts_missing_canonical_target(
    tmp_path: Path,
) -> None:
    script = (ROOT / "ops/oracle_universal_video_precanary_attest.sh").read_text(
        encoding="utf-8"
    )
    validation = script[
        script.index("validate_source_dir_scope(){") : script.index('[[ "$(id -u)"')
    ]
    scope = tmp_path / "bridge-school"
    outside = tmp_path / "outside"
    scope.mkdir()
    outside.mkdir()
    protected = scope / "universal-video"
    protected.mkdir()
    (protected / "spool").mkdir()
    (protected / "secrets").mkdir()
    (scope / "nested").mkdir()
    (scope / "linked-parent").symlink_to(outside, target_is_directory=True)
    mutation_log = tmp_path / "mutation.log"
    valid = scope / "universal-video-src"
    dotdot_escape = scope / "nested" / ".." / ".." / "outside" / "source"
    symlink_escape = scope / "linked-parent" / "source"
    duplicate_separator = f"{scope}//universal-video-src"
    dot_alias = f"{scope}/./universal-video-src"

    probe = validation + rf'''
set -u
scope={json.dumps(str(scope))}
protected={json.dumps(str(protected))}
mutation_log={json.dumps(str(mutation_log))}
attempt(){{
  if validate_source_dir_scope "$1" "$scope" "$protected"; then
    printf 'mutation:%s\n' "$1" >> "$mutation_log"
    return 0
  fi
  return 1
}}
! attempt {json.dumps(str(dotdot_escape))}
! attempt {json.dumps(str(symlink_escape))}
! attempt {json.dumps(duplicate_separator)}
! attempt {json.dumps(dot_alias)}
! attempt {json.dumps(str(protected))}
! attempt {json.dumps(str(protected / "spool"))}
! attempt {json.dumps(str(protected / "secrets"))}
! attempt {json.dumps(str(scope))}
attempt {json.dumps(str(valid))}
[[ "$canonical_source_dir" == {json.dumps(str(valid))} ]]
[[ "$canonical_source_parent" == "$scope" ]]
'''
    completed = subprocess.run(
        ["bash"], input=probe, text=True, capture_output=True, timeout=10
    )
    assert completed.returncode == 0, completed.stderr
    assert mutation_log.read_text(encoding="utf-8").splitlines() == [
        f"mutation:{valid}"
    ]
    assert 'realpath -m -- "$requested"' in validation
    assert 'realpath -e -- "$requested_parent"' in validation
    assert 'validate_source_dir_scope "$SOURCE_DIR" /opt/bridge-school "$BASE_DIR"' in script

    execution = script[script.index('validate_source_dir_scope "$SOURCE_DIR"') :]
    validation_index = execution.index('validate_source_dir_scope "$SOURCE_DIR"')
    lock_create_index = execution.index(
        'install -o root -g universal-video -m 0640 /dev/null "$WORKLOAD_LOCK"'
    )
    source_move_index = execution.index('mv -- "$SOURCE_DIR" "$source_backup_dir"')
    assert validation_index < lock_create_index < source_move_index
    assert 'chown root:universal-video "$WORKLOAD_LOCK"' in execution
    assert "root:universal-video:640:1" in execution
    assert "unsafe workload lock link count" in execution
    assert 'runuser -u universal-video -- test -r "$WORKLOAD_LOCK"' in execution
    assert 'exec 9<"$WORKLOAD_LOCK"' in execution
    assert "rm -rf" not in validation
    assert "mv --" not in validation


def test_candidate_delete_timeout_quarantines_candidate_and_restores_services(
    tmp_path: Path,
) -> None:
    script = (ROOT / "ops/oracle_universal_video_precanary_attest.sh").read_text(
        encoding="utf-8"
    )
    restore = script[
        script.index("restore_source_checkout(){") : script.index("cleanup(){")
    ]
    cleanup = script[script.index("cleanup(){") : script.index("assert_known_state(){")]
    source_dir = tmp_path / "source"
    backup_dir = tmp_path / f"source.precanary-backup.{'a' * 12}.123"
    service_log = tmp_path / "services.log"
    source_dir.mkdir()
    (source_dir / "candidate").write_text("partial candidate", encoding="utf-8")
    backup_dir.mkdir()
    (backup_dir / "original").write_text("exact original", encoding="utf-8")
    original_device_inode = (backup_dir.stat().st_dev, backup_dir.stat().st_ino)

    probe = restore + cleanup + rf'''
set -u
BUILD_IMAGE=1
EXPECTED_SHA={'a' * 40}
SOURCE_DIR={json.dumps(str(source_dir))}
SOURCE_PARENT={json.dumps(str(tmp_path))}
source_candidate_path_owned=1
source_had_original=1
source_backup_dir={json.dumps(str(backup_dir))}
source_backup_device_inode="$(stat -Lc '%d:%i' -- "$source_backup_dir")"
source_quarantine_dir=
restore_failures=()
prestop_frozen_pids=()
window_started=1
services_stop_attempted=1
BASE_DIR={json.dumps(str(tmp_path))}
ENV_FILE="$BASE_DIR/universal-video-container-candidate.env"
added_runtime_masks=(source.service container.service)
lock_held=1
container_was_active=0
resident_image_id=
SOURCE_SERVICE=source.service
CONTAINER_SERVICE=container.service
source_state_before=active
container_target_state=inactive
restored_source_pid=111
restored_source_start_ticks=222
restored_container_pid=
restored_container_start_ticks=
service_log={json.dumps(str(service_log))}
record_restore_failure(){{ restore_failures+=("$1"); }}
bounded_filesystem(){{
  if [[ "$1" == rm ]]; then return 124; fi
  command "$@"
}}
stop_frozen_residents(){{ return 0; }}
residents_are_quiescent(){{ return 0; }}
bounded_systemctl(){{ printf 'systemctl:%s\n' "$*" >> "$service_log"; }}
bounded_docker(){{ return 0; }}
flock(){{ return 0; }}
restore_service(){{
  [[ -f "$SOURCE_DIR/original" && ! -e "$SOURCE_DIR/candidate" ]] || exit 88
  printf '%s\n' "$1" >> "$service_log"
}}
resume_isolated_peer(){{ return 0; }}
service_state(){{
  [[ "$1" == "$SOURCE_SERVICE" ]] && printf 'active\n' || printf 'inactive\n'
}}
restored_service_ready(){{ return 0; }}
cleanup
'''
    completed = subprocess.run(
        ["bash"], input=probe, text=True, capture_output=True, timeout=10
    )
    assert completed.returncode == 1, completed.stderr
    assert (source_dir / "original").read_text(encoding="utf-8") == "exact original"
    assert (source_dir.stat().st_dev, source_dir.stat().st_ino) == original_device_inode
    assert not (source_dir / "candidate").exists()
    quarantines = list(tmp_path.glob("source.precanary-quarantine.*"))
    assert len(quarantines) == 1
    assert not quarantines[0].is_symlink()
    assert (quarantines[0] / "candidate").read_text(encoding="utf-8") == "partial candidate"
    assert service_log.read_text(encoding="utf-8").splitlines() == [
        "systemctl:unmask --runtime source.service",
        "systemctl:unmask --runtime container.service",
        "systemctl:daemon-reload",
        "source.service",
        "container.service",
    ]
    assert "source_candidate_remove,source_candidate_quarantined" in completed.stderr
    assert "result=DEGRADED" in completed.stderr
    assert "UNIVERSAL_VIDEO_PRECANARY_RESTORE_PASS" not in completed.stdout


def test_candidate_delete_timeout_after_success_still_restores_and_fails_run(
    tmp_path: Path,
) -> None:
    script = (ROOT / "ops/oracle_universal_video_precanary_attest.sh").read_text(
        encoding="utf-8"
    )
    restore = script[
        script.index("restore_source_checkout(){") : script.index("cleanup(){")
    ]
    source_dir = tmp_path / "source"
    backup_dir = tmp_path / f"source.precanary-backup.{'c' * 12}.123"
    action_log = tmp_path / "actions.log"
    source_dir.mkdir()
    (source_dir / "candidate").write_text("candidate", encoding="utf-8")
    backup_dir.mkdir()
    (backup_dir / "original").write_text("original", encoding="utf-8")
    original_device_inode = (backup_dir.stat().st_dev, backup_dir.stat().st_ino)

    probe = restore + rf'''
set -u
BUILD_IMAGE=1
EXPECTED_SHA={'c' * 40}
SOURCE_DIR={json.dumps(str(source_dir))}
SOURCE_PARENT={json.dumps(str(tmp_path))}
source_candidate_path_owned=1
source_had_original=1
source_backup_dir={json.dumps(str(backup_dir))}
source_backup_device_inode="$(stat -Lc '%d:%i' -- "$source_backup_dir")"
source_quarantine_dir=
restore_failures=()
action_log={json.dumps(str(action_log))}
record_restore_failure(){{ restore_failures+=("$1"); }}
bounded_filesystem(){{
  if [[ "$1" == rm ]]; then
    command "$@"
    return 124
  fi
  command "$@"
}}
bounded_systemctl(){{ printf 'unmask\n' >> "$action_log"; }}
restore_service(){{
  [[ -f "$SOURCE_DIR/original" && ! -e "$SOURCE_DIR/candidate" ]] || exit 88
  printf 'start:%s\n' "$1" >> "$action_log"
}}
restore_source_checkout
bounded_systemctl unmask --runtime source.service
restore_service source.service active
restore_service container.service inactive
[[ "${{restore_failures[*]}}" == source_candidate_remove ]]
[[ "${{#restore_failures[@]}}" -gt 0 ]] && exit 1
'''
    completed = subprocess.run(
        ["bash"], input=probe, text=True, capture_output=True, timeout=10
    )
    assert completed.returncode == 1, completed.stderr
    assert (source_dir / "original").read_text(encoding="utf-8") == "original"
    assert (source_dir.stat().st_dev, source_dir.stat().st_ino) == original_device_inode
    assert list(tmp_path.glob("source.precanary-quarantine.*")) == []
    assert action_log.read_text(encoding="utf-8").splitlines() == [
        "unmask",
        "start:source.service",
        "start:container.service",
    ]
    assert "result=DEGRADED quarantine=none" in completed.stderr


def test_candidate_quarantine_failure_keeps_services_fail_closed(tmp_path: Path) -> None:
    script = (ROOT / "ops/oracle_universal_video_precanary_attest.sh").read_text(
        encoding="utf-8"
    )
    restore = script[
        script.index("restore_source_checkout(){") : script.index("cleanup(){")
    ]
    cleanup = script[script.index("cleanup(){") : script.index("assert_known_state(){")]
    source_dir = tmp_path / "source"
    backup_dir = tmp_path / f"source.precanary-backup.{'b' * 12}.123"
    service_log = tmp_path / "services.log"
    unmask_log = tmp_path / "unmask.log"
    source_dir.mkdir()
    (source_dir / "candidate").write_text("partial candidate", encoding="utf-8")
    backup_dir.mkdir()
    (backup_dir / "original").write_text("exact original", encoding="utf-8")

    probe = restore + cleanup + rf'''
set -u
BUILD_IMAGE=1
EXPECTED_SHA={'b' * 40}
SOURCE_DIR={json.dumps(str(source_dir))}
SOURCE_PARENT={json.dumps(str(tmp_path))}
source_candidate_path_owned=1
source_had_original=1
source_backup_dir={json.dumps(str(backup_dir))}
source_backup_device_inode="$(stat -Lc '%d:%i' -- "$source_backup_dir")"
source_quarantine_dir=
restore_failures=()
prestop_frozen_pids=()
window_started=1
services_stop_attempted=1
BASE_DIR={json.dumps(str(tmp_path))}
ENV_FILE="$BASE_DIR/universal-video-container-candidate.env"
added_runtime_masks=(source.service container.service)
lock_held=1
container_was_active=0
resident_image_id=
SOURCE_SERVICE=source.service
CONTAINER_SERVICE=container.service
source_state_before=active
container_target_state=inactive
restored_source_pid=
restored_source_start_ticks=
restored_container_pid=
restored_container_start_ticks=
service_log={json.dumps(str(service_log))}
unmask_log={json.dumps(str(unmask_log))}
record_restore_failure(){{ restore_failures+=("$1"); }}
bounded_filesystem(){{ return 124; }}
stop_frozen_residents(){{ return 0; }}
residents_are_quiescent(){{ return 0; }}
bounded_systemctl(){{ printf '%s\n' "$*" >> "$unmask_log"; }}
bounded_docker(){{ return 0; }}
flock(){{ return 0; }}
restore_service(){{ printf '%s\n' "$1" >> "$service_log"; }}
resume_isolated_peer(){{ return 0; }}
service_state(){{ printf 'inactive\n'; }}
restored_service_ready(){{ return 0; }}
cleanup
'''
    completed = subprocess.run(
        ["bash"], input=probe, text=True, capture_output=True, timeout=10
    )
    assert completed.returncode == 1
    assert (source_dir / "candidate").exists()
    assert (backup_dir / "original").exists()
    assert not service_log.exists()
    assert not unmask_log.exists()
    assert "source_checkout" in completed.stderr


def test_pidfd_guard_behavior_rejects_stopped_and_stale_process_identity():
    script = (ROOT / "ops/oracle_universal_video_precanary_attest.sh").read_text(
        encoding="utf-8"
    )
    definitions = script[
        script.index("process_start_ticks(){") :
        script.index("resume_prestop_frozen(){")
    ]
    probe = definitions + r'''
set -euo pipefail
sleep 30 & child=$!
trap 'kill "$child" 2>/dev/null || true; wait "$child" 2>/dev/null || true' EXIT
[[ -r "/proc/$child/stat" ]] || exit 77
ticks="$(process_start_ticks "$child")"
exact_process_signal "$child" "$ticks" STOP
for _ in {1..50}; do
  state="$(process_state "$child")"
  [[ "$state" == T || "$state" == t ]] && break
  sleep 0.02
done
[[ "$state" == T || "$state" == t ]]
! exact_process_signal "$child" "$ticks" CHECK
! exact_process_signal "$child" "$((ticks + 1))" TERM
kill -0 "$child"
exact_process_signal "$child" "$ticks" CONT
for _ in {1..50}; do
  process_is_runnable "$child" && break
  sleep 0.02
done
exact_process_signal "$child" "$ticks" CHECK
exact_process_signal "$child" "$ticks" TERM
wait "$child" || true
trap - EXIT
'''
    completed = subprocess.run(
        ["bash"],
        input=probe,
        text=True,
        capture_output=True,
        timeout=10,
    )
    if completed.returncode == 77:
        pytest.skip("test runner PID namespace is not mounted at /proc")
    assert completed.returncode == 0, completed.stderr


def test_stopping_identity_is_retained_until_exact_process_exits() -> None:
    script = (ROOT / "ops/oracle_universal_video_precanary_attest.sh").read_text(
        encoding="utf-8"
    )
    definitions = script[
        script.index("confirm_prestop_identities_exited(){") :
        script.index("residents_are_quiescent(){")
    ]
    probe = definitions + r'''
set -euo pipefail
live=1
process_start_ticks(){ [[ "$live" == 1 ]] && printf '%s\n' 222; }
child=111
ticks=222
prestop_frozen_services=(universal-video.service)
prestop_frozen_pids=("$child")
prestop_frozen_start_ticks=("$ticks")
! confirm_prestop_identities_exited
[[ "${#prestop_frozen_pids[@]}" -eq 1 ]]
[[ "${prestop_frozen_pids[0]}" == "$child" ]]
live=0
confirm_prestop_identities_exited
[[ "${#prestop_frozen_pids[@]}" -eq 0 ]]
'''
    completed = subprocess.run(
        ["bash"],
        input=probe,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


def test_stopping_retry_terms_runnable_identity_without_redundant_cont() -> None:
    script = (ROOT / "ops/oracle_universal_video_precanary_attest.sh").read_text(
        encoding="utf-8"
    )
    resume = script[
        script.index("resume_prestop_frozen(){") :
        script.index("confirm_prestop_identities_exited(){")
    ]
    probe = resume + r'''
set -euo pipefail
SOURCE_SERVICE=source.service
CONTAINER_SERVICE=container.service
mock_state=S
signals=()
bounded_systemctl(){ return 0; }
process_start_ticks(){ printf '%s\n' 222; }
process_state(){ printf '%s\n' "$mock_state"; }
exact_process_signal(){ signals+=("$3"); }
prestop_frozen_services=(source.service)
prestop_frozen_pids=(111)
prestop_frozen_start_ticks=(222)
resume_prestop_frozen stopping
[[ "${signals[*]}" == TERM ]]
[[ "${#prestop_frozen_pids[@]}" -eq 1 ]]
mock_state=T
signals=()
resume_prestop_frozen stopping
[[ "${signals[*]}" == "TERM CONT" ]]
[[ "${#prestop_frozen_pids[@]}" -eq 1 ]]
'''
    completed = subprocess.run(
        ["bash"],
        input=probe,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


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


def test_external_precanary_is_pr_only_exact_head_validation():
    workflow = (ROOT / ".github/workflows/issue-881-precanary-evidence.yml").read_text(
        encoding="utf-8"
    )
    assert "workflow_dispatch:" not in workflow
    assert "inputs.git_ref" not in workflow
    assert "${{ secrets." not in workflow
    assert "ssh " not in workflow and "scp " not in workflow
    assert "oci " not in workflow
    assert "UNIVERSAL_VIDEO_PRECANARY_BUILD_IMAGE=1" not in workflow
    assert "EXACT_SHA: ${{ github.event.pull_request.head.sha }}" in workflow
    assert "if: github.event.pull_request.head.repo.full_name == github.repository" in workflow
    assert "ref: ${{ env.EXACT_SHA }}" in workflow
    assert 'test "$(git rev-parse HEAD)" = "$EXACT_SHA"' in workflow
    assert "Prove the only external entrypoint is Director-gated" in workflow
    assert "oracle-universal-video-container-evidence.yml" in workflow
    assert "UNIVERSAL_VIDEO_LEGACY_CONTAINER_EVIDENCE_RETIRED=true" in workflow
    retired = (
        ROOT / ".github/workflows/oracle-universal-video-container-evidence.yml"
    ).read_text(encoding="utf-8")
    assert "pull_request:" in retired
    assert "workflow_dispatch:" not in retired
    assert "push:" not in retired
    assert "UNIVERSAL_VIDEO_LEGACY_CONTAINER_EVIDENCE_RETIRED=true" in retired
    assert "secrets." not in retired and "ssh " not in retired and "oci " not in retired


def test_authoritative_external_evidence_binds_live_reviewed_head_and_recovery():
    workflow = (
        ROOT / ".github/workflows/issue-881-authoritative-external-evidence.yml"
    ).read_text(encoding="utf-8")
    assert "exact_sha:" in workflow
    assert "director_go:" in workflow
    assert "if: ${{ inputs.director_go && github.actor == github.repository_owner && github.triggering_actor == github.repository_owner && github.repository == 'olegmed1-art/bridge-video-free' }}" in workflow
    assert "actions: read" in workflow
    assert "pull-requests: read" in workflow
    assert "root_pr_number=991" in workflow
    assert "pr_number=1070" in workflow
    assert "Root Autopilot PR #991 is not merged" in workflow
    assert 'git show -s --format=%P "$EXACT_SHA"' in workflow
    assert '"${exact_parents[0]}" == "$root_merge_sha"' in workflow
    assert '"${exact_parents[1]}" == "$reviewed_sha"' in workflow
    assert "Exact main is not the direct reviewed merge of PR #1070 onto root PR #991" in workflow
    assert 'main_sha="$(gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/main" --jq' in workflow
    assert "Reviewed head has no independent approval" in workflow
    assert "root_required_workflows=(" in workflow
    assert "Oracle idle STOP guard CI" in workflow
    assert "Retired Oracle Universal Video Container Evidence Contract" in workflow
    assert "root_reviewed_sha" in workflow
    assert "group_by(.user.login) | map(max_by(.submitted_at))" in workflow
    assert "Main changed while live review and CI gates were evaluated" in workflow
    assert workflow.count('git/ref/heads/main" --jq') >= 2
    assert '[[ "$live_state" == \'closed\'' in workflow
    assert 'git/ref/heads/main" --jq \'.object.sha\'' in workflow
    assert '"$main_sha" == "$EXACT_SHA"' in workflow
    assert 'git merge-base --is-ancestor "$reviewed_sha" "$EXACT_SHA"' in workflow
    assert ".commit_id ==" in workflow and "$reviewed_sha" in workflow
    assert "required_workflows=(" in workflow
    assert "verify_live_gate(){" in workflow
    # One gate before preparation and one fresh gate after all SSH/SCP staging.
    # Keeping exactly these two calls avoids spending another API-heavy review
    # pass while still binding the mutating attestation to current evidence.
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
    initial_check = workflow.index("          verify_live_gate")
    first_remote_mutation = workflow.index(
        '"${s[@]}" "umask 077; rm -rf', initial_check
    )
    final_head_check = workflow.rindex("          verify_live_gate")
    attestation_call = workflow.index("          set +e", final_head_check)
    assert initial_check < first_remote_mutation < final_head_check < attestation_call


def test_canary_sql_and_rollback_remain_null_safe_and_fail_closed():
    migration = (
        ROOT / "database/migrations/0057_universal_video_canary_review_gate.sql"
    ).read_text(encoding="utf-8")
    rollback = (
        ROOT / "database/rollbacks/0057_universal_video_canary_review_gate.sql"
    ).read_text(encoding="utf-8")
    rollback_test = (
        ROOT / "database/rollback_tests/0057_universal_video_canary_review_gate.sql"
    ).read_text(encoding="utf-8")

    assert migration.count("publication_state' IS DISTINCT FROM 'NOT_PUBLISHED'") == 3
    assert "publication_state' <> 'NOT_PUBLISHED'" not in migration
    assert "p_output->>'result_mode' IS DISTINCT FROM 'SHADOW_REVIEW_ONLY'" in migration
    assert "p_output->>'source_file_id' IS DISTINCT FROM v_job.source_file_id" in migration
    assert "publication_state' IS DISTINCT FROM 'NOT_PUBLISHED'" in rollback
    rollback_finish = rollback[rollback.index("CREATE OR REPLACE FUNCTION video_queue.finish_job") :]
    assert "SET status = 'QUEUED'" not in rollback_finish
    assert "pg_notify('video_queue_ready'" not in rollback_finish
    assert "RETURN QUERY SELECT p_outcome, v_batch_status, 0;" in rollback_finish
    assert "rollback restored automatic canary release" in rollback_test
    assert "rollback released pending jobs" in rollback_test
