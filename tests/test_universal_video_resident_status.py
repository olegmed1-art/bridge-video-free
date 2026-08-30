from __future__ import annotations

import json
from pathlib import Path

from universal_video.spool_worker import _runtime_attestation, write_resident_status


ROOT = Path(__file__).resolve().parents[1]
UNIT = (ROOT / "deploy/oracle-universal-video/universal-video.service").read_text(
    encoding="utf-8"
)


def _payload() -> dict:
    return {
        "job_id": "exact-job",
        "profile": "bridge_lesson",
        "source": {
            "kind": "google_drive",
            "file_id": "1AbCdEfGhIjKlMnOpQrStUvWxYz",
        },
        "metadata": {
            "request_commit": "e" * 40,
            "requested_runtime_commit": "a" * 40,
        },
    }


def _result() -> dict:
    return {
        "job_id": "exact-job",
        "profile": "bridge_lesson",
        "processing_revision": "a" * 40,
    }


def test_runtime_attestation_uses_only_explicit_request_and_observed_runtime(monkeypatch):
    monkeypatch.setenv("UNIVERSAL_VIDEO_SOURCE_COMMIT", "a" * 40)
    attestation = _runtime_attestation(
        payload=_payload(), result=_result(), job_hash="c" * 64
    )
    assert attestation == {
        "schema": "universal-video-runtime-job-attestation-v1",
        "job_id": "exact-job",
        "request_commit": "e" * 40,
        "requested_runtime_commit": "a" * 40,
        "installed_runtime_commit": "a" * 40,
        "observed_job_runtime_commit": "a" * 40,
        "profile": "bridge_lesson",
        "job_hash": "c" * 64,
        "source_file_id": "1AbCdEfGhIjKlMnOpQrStUvWxYz",
        "canonical_output_untouched": True,
        "canonical_promotion_allowed": False,
        "publication_state": "NOT_PUBLISHED",
    }


def test_legacy_job_without_request_commit_remains_unattested(monkeypatch):
    monkeypatch.setenv("UNIVERSAL_VIDEO_SOURCE_COMMIT", "a" * 40)
    payload = _payload()
    payload["metadata"].pop("request_commit")
    assert _runtime_attestation(
        payload=payload, result=_result(), job_hash="c" * 64
    ) is None


def test_resident_status_copies_only_worker_bound_attestations(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("UNIVERSAL_VIDEO_SOURCE_COMMIT", "a" * 40)
    spool = tmp_path / "spool"
    for name in ("inbox", "running", "done", "failed", "results"):
        (spool / name).mkdir(parents=True)
    attestation = _runtime_attestation(
        payload=_payload(), result=_result(), job_hash="c" * 64
    )
    (spool / "done" / "exact-job.json").write_text(
        json.dumps({"runtime_attestation": attestation}), encoding="utf-8"
    )
    (spool / "done" / "legacy.json").write_text("{}", encoding="utf-8")
    status_path = tmp_path / "run" / "universal-video-status.json"
    status = write_resident_status(spool, status_path)
    assert status["schema"] == "universal-video-resident-status-v2"
    assert status["active_jobs"] == []
    assert status["installed_runtime_commit"] == "a" * 40
    assert status["job_attestations"] == [attestation]
    assert json.loads(status_path.read_text(encoding="utf-8")) == status


def test_resident_status_reports_active_job_fail_closed(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("UNIVERSAL_VIDEO_SOURCE_COMMIT", "a" * 40)
    spool = tmp_path / "spool"
    for name in ("inbox", "running", "done", "failed", "results"):
        (spool / name).mkdir(parents=True)
    (spool / "running" / "active-job.json").write_text("{}", encoding="utf-8")
    status = write_resident_status(
        spool, tmp_path / "run" / "universal-video-status.json"
    )
    assert status["active_jobs"] == ["active-job"]


def test_systemd_grants_only_resident_status_runtime_directory():
    assert "RuntimeDirectory=bridge-school" in UNIT
    assert "RuntimeDirectoryMode=0750" in UNIT
    assert "UNIVERSAL_VIDEO_STATUS_PATH=/run/bridge-school/universal-video-status.json" in UNIT
    assert "ReadWritePaths=/run/bridge-school" in UNIT


def test_resident_publishes_status_before_accepting_first_job() -> None:
    worker = (ROOT / "universal_video/spool_worker.py").read_text(encoding="utf-8")
    start = worker.index("def run_forever")
    run_forever = worker[start:worker.index("\ndef main()", start)]
    first_status = run_forever.index("write_resident_status(spool_root, status_path)")
    first_process = run_forever.index("processed = process_one(spool_root)")
    assert first_status < first_process
