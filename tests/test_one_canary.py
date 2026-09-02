from __future__ import annotations

import json
import sys
import types

import pytest

from universal_video import one_canary
from universal_video.canary_gate import CanaryGateError

RUNTIME_SHA = "a" * 40
IMAGE_DIGEST = "sha256:" + "b" * 64
JOB_ID = "11111111-1111-1111-1111-111111111111"


def isolated_job():
    return {
        "id": JOB_ID,
        "batch_id": "22222222-2222-2222-2222-222222222222",
        "stable_job_key": "uv-canary-test",
        "source_file_id": "source-video-id",
        "source_name": "Диана 13.mp4",
        "source_mime_type": "video/mp4",
        "source_size_bytes": 696002235,
        "source_folder_id": "source-folder",
        "source_checksum": "md5:" + "1" * 32,
        "output_folder_id": "output-folder",
    }


def arguments(*extra):
    return [
        "--expected-job-id", JOB_ID,
        "--expected-source-file-id", "source-video-id",
        "--processing-profile", "video31-free",
        "--algorithm-revision", "uv-2026-09-02",
        "--runtime-sha", RUNTIME_SHA,
        "--image-digest", IMAGE_DIGEST,
        *extra,
    ]


def test_preflight_only_never_imports_worker_or_processes(monkeypatch, capsys):
    monkeypatch.setattr(one_canary, "_load_isolated_target", lambda **_: isolated_job())
    sys.modules.pop("universal_video.neon_worker", None)
    assert one_canary.main(arguments("--preflight-only")) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["isolated_one_job_batch"] is True
    assert output["media_processing_started"] is False
    assert output["canonical_promotion_allowed"] is False
    assert "universal_video.neon_worker" not in sys.modules


def test_media_run_requires_explicit_director_go(monkeypatch):
    monkeypatch.setattr(one_canary, "_load_isolated_target", lambda **_: isolated_job())
    with pytest.raises(CanaryGateError, match="explicit --director-go"):
        one_canary.main(arguments())


def test_strict_processor_binds_result_to_runtime_image_and_source(monkeypatch):
    monkeypatch.setenv("BRIDGE_CANARY_RUNTIME_SHA", RUNTIME_SHA)
    monkeypatch.setenv("BRIDGE_CANARY_IMAGE_DIGEST", IMAGE_DIGEST)
    fake_module = types.ModuleType("universal_video.neon_worker")
    fake_module.stable_review_processor = lambda claimed: {
        "processor": "stable_review_hardened",
        "publication_state": "NOT_PUBLISHED",
        "canonical_promotion_allowed": False,
        "master_pdf_drive_id": "master-id",
        "master_pdf_sha256": "c" * 64,
    }
    monkeypatch.setitem(sys.modules, "universal_video.neon_worker", fake_module)

    observed = {}

    def fake_contract(**kwargs):
        observed.update(kwargs)
        return {"terminal_receipt": "ok"}

    monkeypatch.setattr(one_canary, "apply_result_contract", fake_contract)
    result = one_canary._strict_processor(isolated_job())
    assert result == {"terminal_receipt": "ok"}
    assert observed["runtime_sha"] == RUNTIME_SHA
    assert observed["image_digest"] == IMAGE_DIGEST
    assert observed["job"]["source_file_id"] == "source-video-id"


def test_strict_processor_rejects_missing_source_checksum_before_worker_import(monkeypatch):
    monkeypatch.setenv("BRIDGE_CANARY_RUNTIME_SHA", RUNTIME_SHA)
    monkeypatch.setenv("BRIDGE_CANARY_IMAGE_DIGEST", IMAGE_DIGEST)
    claimed = isolated_job()
    claimed["source_checksum"] = None
    sys.modules.pop("universal_video.neon_worker", None)
    with pytest.raises(CanaryGateError, match="checksum"):
        one_canary._strict_processor(claimed)
    assert "universal_video.neon_worker" not in sys.modules
