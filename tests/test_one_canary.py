from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from universal_video import one_canary
from universal_video.canary_gate import CanaryGateError

RUNTIME_SHA = "a" * 40
IMAGE_DIGEST = "sha256:" + "b" * 64
JOB_ID = "11111111-1111-1111-1111-111111111111"


def test_pr_only_workflow_provisions_roles_and_uses_the_dockerfile_runtime_arg() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/uv-ready-one-canary.yml").read_text(
        encoding="utf-8"
    )
    dockerfile = (root / "deploy/oracle-universal-video/Dockerfile").read_text(
        encoding="utf-8"
    )

    first_from = next(line for line in dockerfile.splitlines() if line.startswith("FROM "))
    assert first_from == (
        "FROM python:3.12-slim-bookworm@sha256:"
        "ff054eb6f4094b8d8e0af937ac9108bbb8544d1fc69d0dc34d5713d9ffbc0e9e"
    )
    for role in ("bridge_school_reader", "bridge_school_app", "bridge_school_worker"):
        assert f"'{role}'" in workflow
    assert "CREATE ROLE %I NOLOGIN" in workflow
    assert "CREATE TABLE IF NOT EXISTS public.schema_migration" in workflow
    assert "python -m pytest -q tests/test_canary_gate.py tests/test_one_canary.py" in workflow
    assert '--build-arg "UNIVERSAL_VIDEO_SOURCE_COMMIT=$runtime_sha"' in workflow
    assert "ARG UNIVERSAL_VIDEO_SOURCE_COMMIT" in workflow
    exact_oracle = workflow.split("  exact-oracle-image:", 1)[1]
    assert "if: github.event_name == 'workflow_dispatch'" in exact_oracle.split("    steps:", 1)[0]


def isolated_job():
    return {
        "job_id": JOB_ID,
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
    assert observed["processor_result"]["result_mode"] == "SHADOW_REVIEW_ONLY"
    assert observed["processor_result"]["publication_state"] == "NOT_PUBLISHED"
    assert observed["processor_result"]["canonical_promotion_allowed"] is False
    assert observed["processor_result"]["database_persistence_allowed"] is False
    assert observed["processor_result"]["source_file_id"] == "source-video-id"


def test_canary_queries_match_migration_0056_identifiers() -> None:
    source = Path(one_canary.__file__).read_text(encoding="utf-8")
    assert "j.job_id" in source
    assert "b.batch_id" in source
    assert "j.output AS result_manifest" in source
    assert "j.error_code AS error" in source
    assert "j.id" not in source
    assert "b.id" not in source
    assert "NeonWorker" not in source


def test_director_go_uses_resident_worker_api(monkeypatch, capsys):
    monkeypatch.setattr(one_canary, "_load_isolated_target", lambda **_: isolated_job())
    monkeypatch.setattr(one_canary, "_dsn", lambda: "postgresql://metadata-only")
    monkeypatch.setattr(
        one_canary,
        "_postflight",
        lambda target, runtime_sha, image_digest: {
            "job_id": target.job_id,
            "runtime_sha": runtime_sha,
            "image_digest": image_digest,
        },
    )
    observed = {}
    fake_module = types.ModuleType("universal_video.neon_worker")

    def fake_process_one_neon(**kwargs):
        observed.update(kwargs)
        return True

    fake_module.process_one_neon = fake_process_one_neon
    monkeypatch.setitem(sys.modules, "universal_video.neon_worker", fake_module)

    assert one_canary.main(arguments("--director-go")) == 0
    assert observed["database_url"] == "postgresql://metadata-only"
    assert observed["worker_key"] == "director-one-canary-" + RUNTIME_SHA[:12]
    assert observed["processor"] is one_canary._strict_processor
    assert json.loads(capsys.readouterr().out)["job_id"] == JOB_ID


def test_strict_processor_rejects_missing_source_checksum_before_worker_import(monkeypatch):
    monkeypatch.setenv("BRIDGE_CANARY_RUNTIME_SHA", RUNTIME_SHA)
    monkeypatch.setenv("BRIDGE_CANARY_IMAGE_DIGEST", IMAGE_DIGEST)
    claimed = isolated_job()
    claimed["source_checksum"] = None
    sys.modules.pop("universal_video.neon_worker", None)
    with pytest.raises(CanaryGateError, match="checksum"):
        one_canary._strict_processor(claimed)
    assert "universal_video.neon_worker" not in sys.modules
