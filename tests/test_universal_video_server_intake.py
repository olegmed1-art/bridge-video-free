import json
from pathlib import Path

import pytest

from universal_video.server_intake import IntakeError, submit


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
