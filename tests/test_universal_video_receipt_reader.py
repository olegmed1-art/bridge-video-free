from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from universal_video.contract import canonical_job_hash, validate_job


ROOT = Path(__file__).resolve().parents[1]
READER = ROOT / "ops/universal_video_receipt_reader.py"
JOB_ID = "exact-video-job-01"
PROFILE = "bridge_lesson"
JOB_HASH = "a" * 64
SOURCE_ID = "source-file-id"


def _done_payload() -> dict:
    return {
        "status": "COMPLETED",
        "job_id": JOB_ID,
        "profile": PROFILE,
        "job_hash": JOB_HASH,
        "source": {"kind": "google_drive", "file_id": SOURCE_ID},
    }


def _run(path: Path, *, timeout: float = 2) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "python3",
            str(READER),
            "inspect-done",
            str(path),
            JOB_ID,
            PROFILE,
            JOB_HASH,
            SOURCE_ID,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o640)


def test_exact_regular_receipt_passes(tmp_path: Path):
    receipt = tmp_path / "done.json"
    _write(receipt, json.dumps(_done_payload()))
    result = _run(receipt)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "COMPLETED"


def test_symlink_receipt_fails_without_disclosing_target(tmp_path: Path):
    target = tmp_path / "secret.json"
    _write(target, json.dumps({**_done_payload(), "secret": "DO_NOT_PRINT_ME"}))
    receipt = tmp_path / "done.json"
    receipt.symlink_to(target)
    result = _run(receipt)
    assert result.returncode != 0
    assert "DO_NOT_PRINT_ME" not in result.stdout + result.stderr


def test_fifo_receipt_fails_without_blocking(tmp_path: Path):
    receipt = tmp_path / "done.fifo"
    os.mkfifo(receipt, 0o640)
    result = _run(receipt, timeout=2)
    assert result.returncode != 0


def test_wrong_mode_and_oversized_receipts_fail(tmp_path: Path):
    wrong_mode = tmp_path / "wrong-mode.json"
    _write(wrong_mode, json.dumps(_done_payload()))
    wrong_mode.chmod(0o644)
    assert _run(wrong_mode).returncode != 0

    oversized = tmp_path / "oversized.json"
    with oversized.open("wb") as handle:
        handle.truncate(8 * 1024 * 1024 + 1)
    oversized.chmod(0o640)
    assert _run(oversized).returncode != 0


def test_duplicate_member_and_nonfinite_number_fail(tmp_path: Path):
    duplicate = tmp_path / "duplicate.json"
    payload = json.dumps(_done_payload())
    _write(duplicate, payload[:-1] + ',"status":"COMPLETED"}')
    assert _run(duplicate).returncode != 0

    nonfinite = tmp_path / "nonfinite.json"
    _write(nonfinite, payload[:-1] + ',"score":NaN}')
    assert _run(nonfinite).returncode != 0


def test_failed_receipt_exposes_only_allowlisted_identity(tmp_path: Path):
    receipt = tmp_path / "failed.json"
    _write(
        receipt,
        json.dumps(
            {
                "status": "FAILED",
                "job_file": f"{JOB_ID}.json",
                "error_type": "VideoSubprocessError",
                "error_code": "UV_MEDIA_PROBE_FAILED",
                "job_id": JOB_ID,
                "profile": "balanced",
                "job_hash": "a" * 64,
                "source": {"kind": "google_drive", "file_id": "drive-file-123"},
                "error": "DO_NOT_PRINT_RAW_STDERR\n/private/source-name.mp4",
            }
        ),
    )
    result = subprocess.run(
        ["python3", str(READER), "inspect-failed", str(receipt), f"{JOB_ID}.json", "balanced", "a" * 64, "drive-file-123"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=2,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "UV_ERROR_TYPE=VideoSubprocessError",
        "UV_ERROR_CODE=UV_MEDIA_PROBE_FAILED",
    ]
    assert "DO_NOT_PRINT_RAW_STDERR" not in result.stdout + result.stderr
    assert "source-name.mp4" not in result.stdout + result.stderr


def test_failed_receipt_rejects_unbounded_error_identity(tmp_path: Path):
    receipt = tmp_path / "failed.json"
    _write(
        receipt,
        json.dumps(
            {
                "status": "FAILED",
                "job_file": f"{JOB_ID}.json",
                "error_type": "RuntimeError\nINJECTED",
                "error_code": "NOT_ALLOWLISTED",
                "job_id": JOB_ID,
                "profile": "balanced",
                "job_hash": "a" * 64,
                "source": {"kind": "google_drive", "file_id": "drive-file-123"},
            }
        ),
    )
    result = subprocess.run(
        ["python3", str(READER), "inspect-failed", str(receipt), f"{JOB_ID}.json", "balanced", "a" * 64, "drive-file-123"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=2,
        check=False,
    )
    assert result.returncode != 0
    assert "INJECTED" not in result.stdout + result.stderr


def test_failed_receipt_rejects_different_source_identity(tmp_path: Path):
    receipt = tmp_path / "failed.json"
    _write(
        receipt,
        json.dumps(
            {
                "status": "FAILED",
                "job_file": f"{JOB_ID}.json",
                "job_id": JOB_ID,
                "profile": "balanced",
                "job_hash": "a" * 64,
                "source": {"kind": "google_drive", "file_id": "other-drive-file"},
                "error_type": "VideoSubprocessError",
                "error_code": "UV_MEDIA_PROBE_FAILED",
            }
        ),
    )
    result = subprocess.run(
        ["python3", str(READER), "inspect-failed", str(receipt), f"{JOB_ID}.json", "balanced", "a" * 64, "drive-file-123"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=2,
        check=False,
    )
    assert result.returncode != 0
    assert "other-drive-file" not in result.stdout + result.stderr


def test_legacy_failed_summary_keeps_bounded_operator_error_contract(tmp_path: Path):
    receipt = tmp_path / "failed.json"
    _write(
        receipt,
        json.dumps(
            {
                "status": "FAILED",
                "job_file": f"{JOB_ID}.json",
                "error_type": "VideoSubprocessError",
                "error_code": "UV_MEDIA_PROBE_FAILED",
                "error": "DO_NOT_PRINT_RAW_EXCEPTION",
            }
        ),
    )
    result = subprocess.run(
        ["python3", str(READER), "inspect-failed-summary", str(receipt), f"{JOB_ID}.json"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=2,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "UV_ERROR_TYPE=VideoSubprocessError",
        "UV_ERROR_CODE=UV_MEDIA_PROBE_FAILED",
    ]
    assert "DO_NOT_PRINT_RAW_EXCEPTION" not in result.stdout + result.stderr


def _pending_payload() -> dict:
    return {
        "job_id": JOB_ID,
        "profile": PROFILE,
        "source": {"kind": "google_drive", "file_id": SOURCE_ID, "name": "lesson"},
        "metadata": {"request_commit": "b" * 40},
    }


def _run_pending(path: Path, *, source_id: str = SOURCE_ID) -> subprocess.CompletedProcess[str]:
    job_hash = canonical_job_hash(validate_job(_pending_payload()))
    return subprocess.run(
        [
            "python3",
            str(READER),
            "inspect-job",
            str(path),
            JOB_ID,
            PROFILE,
            job_hash,
            source_id,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=2,
        check=False,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )


def test_pending_job_identity_is_exact_and_secret_safe(tmp_path: Path):
    receipt = tmp_path / "pending.json"
    _write(receipt, json.dumps(_pending_payload()))
    accepted = _run_pending(receipt)
    assert accepted.returncode == 0, accepted.stderr
    assert accepted.stdout.strip() == "JOB_IDENTITY_PASS"

    rejected = _run_pending(receipt, source_id="different-source-id")
    assert rejected.returncode != 0
    assert "lesson" not in rejected.stdout + rejected.stderr
