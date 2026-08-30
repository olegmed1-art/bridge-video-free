import json
import os
import stat
from pathlib import Path

import pytest

from universal_video.contract import VideoContractError
from universal_video.server_intake import IntakeError, _error_code, submit


def payload(job_id="lesson-173"):
    return {
        "job_id": job_id,
        "profile": "bridge_lesson",
        "source": {
            "kind": "google_drive",
            "file_id": "1AbCdEfGhIjKlMnOpQrStUvWxYz",
            "name": "any video",
        },
    }


def test_accepts_any_explicit_drive_video(tmp_path: Path):
    spool, staging = tmp_path / "spool", tmp_path / "staging"
    (spool / "inbox").mkdir(parents=True)
    staging.mkdir()
    assert submit(payload(), spool_root=spool, staging_root=staging) == "lesson-173"
    assert json.loads((spool / "inbox" / "lesson-173.json").read_text()) == payload()


def test_published_job_is_readable_only_by_inbox_group_under_restrictive_umask(tmp_path: Path):
    spool, staging = tmp_path / "spool", tmp_path / "staging"
    inbox = spool / "inbox"
    inbox.mkdir(parents=True)
    staging.mkdir()
    previous_umask = os.umask(0o077)
    try:
        submit(payload(), spool_root=spool, staging_root=staging)
    finally:
        os.umask(previous_umask)

    published = inbox / "lesson-173.json"
    assert stat.S_IMODE(published.stat().st_mode) == 0o640
    assert published.stat().st_gid == inbox.stat().st_gid


def test_worker_group_assignment_failure_does_not_publish_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    spool, staging = tmp_path / "spool", tmp_path / "staging"
    inbox = spool / "inbox"
    inbox.mkdir(parents=True)
    staging.mkdir()

    def fail_group_assignment(_fd: int, _uid: int, _gid: int) -> None:
        raise PermissionError("group assignment refused")

    monkeypatch.setattr(os, "fchown", fail_group_assignment)
    with pytest.raises(PermissionError, match="group assignment refused"):
        submit(payload(), spool_root=spool, staging_root=staging)
    assert list(inbox.iterdir()) == []
    assert list(staging.iterdir()) == []


def test_rejects_duplicate_or_local_source(tmp_path: Path):
    spool, staging = tmp_path / "spool", tmp_path / "staging"
    (spool / "inbox").mkdir(parents=True)
    staging.mkdir()
    submit(payload(), spool_root=spool, staging_root=staging)
    with pytest.raises(IntakeError):
        submit(payload(), spool_root=spool, staging_root=staging)
    bad = payload("local")
    bad["source"] = {"kind": "local_path", "path": "/media/x.mp4"}
    with pytest.raises(IntakeError):
        submit(bad, spool_root=spool, staging_root=staging)


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (PermissionError(13, "private path"), "UV_INTAKE_PERMISSION_DENIED"),
        (OSError(18, "private path"), "UV_INTAKE_CROSS_DEVICE"),
        (OSError(28, "private path"), "UV_INTAKE_DISK_FULL"),
        (OSError(30, "private path"), "UV_INTAKE_READ_ONLY"),
        (FileExistsError(17, "private path"), "UV_INTAKE_COLLISION"),
        (OSError(5, "private path"), "UV_INTAKE_IO_FAILED"),
        (VideoContractError("private contract detail"), "UV_INTAKE_CONTRACT_INVALID"),
    ],
)
def test_intake_failure_codes_do_not_expose_exception_text(exc: BaseException, code: str):
    assert _error_code(exc) == code
    assert "private" not in _error_code(exc)


def test_error_code_rejects_unregistered_intake_code():
    exc = IntakeError("private detail", error_code="UV_INTAKE_PRIVATE_PATH_LEAK")

    assert _error_code(exc) == "UV_INTAKE_EXECUTION_FAILED"


@pytest.mark.parametrize("state", ["running", "done", "failed", "progress"])
def test_rejects_job_id_present_in_any_terminal_or_active_state(tmp_path: Path, state: str):
    spool, staging = tmp_path / "spool", tmp_path / "staging"
    (spool / "inbox").mkdir(parents=True)
    (spool / state).mkdir()
    staging.mkdir()
    (spool / state / "lesson-173.json").write_text("{}", encoding="utf-8")
    with pytest.raises(IntakeError, match="already exists"):
        submit(payload(), spool_root=spool, staging_root=staging)


def test_rejects_job_id_with_existing_result_directory(tmp_path: Path):
    spool, staging = tmp_path / "spool", tmp_path / "staging"
    (spool / "inbox").mkdir(parents=True)
    (spool / "results" / "lesson-173").mkdir(parents=True)
    staging.mkdir()
    with pytest.raises(IntakeError, match="already exists"):
        submit(payload(), spool_root=spool, staging_root=staging)
