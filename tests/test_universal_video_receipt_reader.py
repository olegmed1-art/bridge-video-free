from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


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
                "error": "DO_NOT_PRINT_RAW_STDERR\n/private/source-name.mp4",
            }
        ),
    )
    result = subprocess.run(
        ["python3", str(READER), "inspect-failed", str(receipt), f"{JOB_ID}.json"],
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
            }
        ),
    )
    result = subprocess.run(
        ["python3", str(READER), "inspect-failed", str(receipt), f"{JOB_ID}.json"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=2,
        check=False,
    )
    assert result.returncode != 0
    assert "INJECTED" not in result.stdout + result.stderr
