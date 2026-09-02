import hashlib
import json
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from universal_video.neon_worker import (
    NeonVideoWorkerError,
    _stable_environment,
    process_claim,
    stable_review_processor,
    verify_claimed_source,
)
from universal_video.terminal_evidence import build_terminal_evidence
from universal_video.video_queue import (
    VideoQueueError,
    build_drive_manifest,
    inventory_sha256,
    normalize_drive_inventory,
    validate_intake_request,
)


def request(**overrides):
    value = {
        "request_key": "batch-20260829-001",
        "source_folder_id": "sourceFolder000001",
        "output_folder_id": "outputFolder00001",
        "work_folder_id": "outputFolder00001",
        "processing_profile": "bridge_3_1_free",
        "algorithm_revision": "3.1-free-r25.16",
        "canary_source_file_id": "driveVideo00000014",
    }
    value.update(overrides)
    return value


def drive_item(
    number: int,
    *,
    name: str | None = None,
    mime: str = "video/mp4",
    parent: str = "sourceFolder000001",
):
    return {
        "id": f"driveVideo{number:08d}",
        "name": name or f"Lesson {number}.mp4",
        "mimeType": mime,
        "size": str(2_000_000 + number),
        "parents": [parent],
        "md5Checksum": f"{number:032x}"[-32:],
    }


def test_intake_is_project_neutral_and_review_only():
    validated = validate_intake_request(request())
    assert "project" not in validated
    manifest = build_drive_manifest(request(), [drive_item(14), drive_item(2), drive_item(1)])
    assert [item["name"] for item in manifest["files"]] == [
        "Lesson 1.mp4",
        "Lesson 2.mp4",
        "Lesson 14.mp4",
    ]
    assert manifest["result_mode"] == "SHADOW_REVIEW_ONLY"
    assert manifest["canonical_promotion_allowed"] is False
    assert manifest["database_persistence_allowed"] is False


def test_generic_queue_accepts_another_bounded_adapter_identity():
    validated = validate_intake_request(
        request(
            processing_profile="lecture_transcript",
            algorithm_revision="transcript-r1",
        )
    )
    assert validated["processing_profile"] == "lecture_transcript"
    assert validated["algorithm_revision"] == "transcript-r1"


@pytest.mark.parametrize(
    "change",
    [
        {"project": "forbidden-project"},
        {"source_folder_id": "outputFolder00001"},
        {"processing_profile": "INVALID PROFILE"},
        {"algorithm_revision": "INVALID REVISION"},
    ],
)
def test_intake_rejects_unknown_or_unsafe_fields(change):
    with pytest.raises(VideoQueueError):
        validate_intake_request(request(**change))


def test_inventory_excludes_nonvideo_and_derived_parts_but_rejects_identity_drift():
    files = normalize_drive_inventory(
        "sourceFolder000001",
        [
            drive_item(1),
            drive_item(2, mime="application/pdf"),
            drive_item(3, name="AI_PART_3.mp4"),
        ],
    )
    assert [item["file_id"] for item in files] == ["driveVideo00000001"]
    with pytest.raises(VideoQueueError, match="direct child"):
        normalize_drive_inventory(
            "sourceFolder000001",
            [drive_item(1, parent="differentFolder0001")],
        )


def test_254_item_intake_is_deterministic_and_locally_bounded():
    raw = [drive_item(number) for number in range(254, 0, -1)]
    started = time.monotonic()
    first = normalize_drive_inventory("sourceFolder000001", raw)
    second = normalize_drive_inventory("sourceFolder000001", reversed(raw))
    elapsed = time.monotonic() - started
    assert len(first) == 254
    assert first == second
    assert inventory_sha256(first) == inventory_sha256(second)
    assert elapsed < 1.0
    assert len(json.dumps(first, ensure_ascii=False).encode()) < 256 * 1024


def claim():
    return {
        "job_id": "00000000-0000-0000-0000-000000000001",
        "batch_id": "00000000-0000-0000-0000-000000000002",
        "lease_token": "00000000-0000-0000-0000-000000000003",
        "sequence": 14,
        "source_folder_id": "sourceFolder000001",
        "output_folder_id": "outputFolder00001",
        "work_folder_id": "outputFolder00001",
        "processing_profile": "bridge_3_1_free",
        "algorithm_revision": "3.1-free-r25.16",
        "source_file_id": "driveVideo00000014",
        "source_name": "Lesson 14.mp4",
        "source_mime_type": "video/mp4",
        "source_size_bytes": 2_000_014,
        "source_checksum": "md5:0000000000000000000000000000000e",
        "stable_job_key": __import__("bridge_worker_3_1_free").stable_job_id(
            "drive",
            "driveVideo00000014",
        ),
        "is_canary": True,
        "attempt_count": 1,
    }


def done_contract(item):
    return {
        "status": "AI_DONE",
        "job_id": item["stable_job_key"],
        "algorithmRevision": item["algorithm_revision"],
        "original": {"driveId": item["source_file_id"]},
        "masterPdf": {
            "driveId": "masterPdf000001",
            "sha256": "a" * 64,
            "pages": 1,
        },
    }


def terminal_result(item):
    master_body = b"%PDF-1.4\nsynthetic queue terminal\n%%EOF\n"
    master_id = "masterPdf000001"
    done_id = "aiDoneFile00001"
    done = {
        "status": "AI_DONE",
        "job_id": item["stable_job_key"],
        "algorithmRevision": item["algorithm_revision"],
        "original": {"driveId": item["source_file_id"]},
        "masterPdf": {
            "driveId": master_id,
            "sha256": hashlib.sha256(master_body).hexdigest(),
            "pages": 1,
        },
    }
    done_body = json.dumps(
        done,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    route = {
        "stage": "OUTPUT_ROUTE",
        "status": "ROUTED",
        "job_id": item["stable_job_key"],
        "target_folder_id": item["output_folder_id"],
        "source_untouched": True,
        "results": [
            {"kind": "master_pdf", "file_id": master_id},
            {"kind": "ai_done", "file_id": done_id},
        ],
    }
    objects = {
        master_id: {
            "file_id": master_id,
            "name": "MASTER.pdf",
            "mime_type": "application/pdf",
            "size_bytes": len(master_body),
            "parents": [item["output_folder_id"]],
            "sha256": hashlib.sha256(master_body).hexdigest(),
            "md5": hashlib.md5(master_body, usedforsecurity=False).hexdigest(),
            "body": master_body,
        },
        done_id: {
            "file_id": done_id,
            "name": "AI_DONE.json",
            "mime_type": "application/json",
            "size_bytes": len(done_body),
            "parents": [item["output_folder_id"]],
            "sha256": hashlib.sha256(done_body).hexdigest(),
            "md5": hashlib.md5(done_body, usedforsecurity=False).hexdigest(),
            "body": done_body,
        },
    }

    def readback(file_id, _token, *, max_bytes, retain_body=False):
        assert len(objects[file_id]["body"]) <= max_bytes
        result = dict(objects[file_id])
        if not retain_body:
            result.pop("body", None)
        return result

    return build_terminal_evidence(item, done, route, "token", readback=readback)


@contextmanager
def exact_source(meta=None):
    with patch(
        "universal_video.neon_worker.access_token",
        return_value="drive-token",
    ), patch(
        "universal_video.neon_worker.file_metadata",
        return_value=meta or drive_item(14),
    ):
        yield


def test_live_source_readback_is_exact_and_fail_closed():
    item = claim()
    meta = drive_item(14)
    with patch("universal_video.neon_worker.file_metadata", return_value=meta):
        assert verify_claimed_source(item, "token") == {
            "file_id": item["source_file_id"],
            "name": item["source_name"],
            "mime_type": item["source_mime_type"],
            "size_bytes": item["source_size_bytes"],
            "parent_folder_id": item["source_folder_id"],
            "checksum": item["source_checksum"],
        }
    meta["name"] = "renamed.mp4"
    with patch("universal_video.neon_worker.file_metadata", return_value=meta):
        with pytest.raises(NeonVideoWorkerError, match="READBACK_MISMATCH"):
            verify_claimed_source(item, "token")


def test_source_without_checksum_fails_closed():
    item = claim()
    item["source_checksum"] = None
    with patch("universal_video.neon_worker.file_metadata", return_value=drive_item(14)):
        with pytest.raises(RuntimeError, match="UV_SOURCE_IDENTITY_INVALID"):
            verify_claimed_source(item, "token")


def test_stable_environment_hides_legacy_database_persistence(monkeypatch):
    monkeypatch.setenv("BRIDGE_WORKER_DATABASE_URL", "postgresql://must-not-leak")
    with _stable_environment(claim()):
        assert "BRIDGE_WORKER_DATABASE_URL" not in __import__("os").environ
        assert __import__("os").environ["BRIDGE_PERSIST_DATABASE"] == "false"
        assert (
            __import__("os").environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"]
            == "3.1-free-r25.16"
        )
    assert (
        __import__("os").environ["BRIDGE_WORKER_DATABASE_URL"]
        == "postgresql://must-not-leak"
    )


def test_worker_finishes_success_as_review_only_with_complete_receipt():
    item = claim()
    captured = {}
    terminal = terminal_result(item)
    done = done_contract(item)

    def finish(_dsn, **kwargs):
        captured.update(kwargs)
        return {
            "job_status": kwargs["outcome"],
            "batch_status": "RUNNING",
            "released_jobs": 253,
        }

    with exact_source(), patch(
        "bridge_runtime_hardening_r25_16.run",
        return_value=done,
    ), patch(
        "route_drive_job_outputs.route_outputs",
        return_value={"status": "ROUTED"},
    ), patch(
        "universal_video.neon_worker.build_terminal_evidence",
        return_value=terminal,
    ), patch(
        "universal_video.neon_worker.finish_job",
        side_effect=finish,
    ):
        result = process_claim(
            "postgresql://queue",
            item,
            "worker-1",
            processor=stable_review_processor,
        )
    assert result["job_status"] == "REVIEW_READY"
    assert captured["output"]["result_mode"] == "SHADOW_REVIEW_ONLY"
    assert captured["output"]["canonical_promotion_allowed"] is False
    assert captured["output"]["database_persistence_allowed"] is False
    assert captured["output"]["terminal_receipt"]["result_readback_verified"] is True


def test_custom_processor_cannot_supply_terminal_success_evidence():
    item = claim()
    captured = {}

    def retry(_dsn, **kwargs):
        captured.update(kwargs)
        return {"job_status": "QUEUED", "batch_status": "RUNNING"}

    with exact_source(), patch(
        "universal_video.neon_worker.retry_job",
        side_effect=retry,
    ), patch("universal_video.neon_worker.finish_job") as finish:
        result = process_claim(
            "postgresql://queue",
            item,
            "worker-1",
            processor=lambda _: terminal_result(item),
        )
    assert result["job_status"] == "QUEUED"
    assert captured["error_code"] == "UV_TERMINAL_CUSTOM_PROCESSOR_FORBIDDEN"
    finish.assert_not_called()


def test_legacy_processor_cannot_finish_without_terminal_receipt():
    item = claim()
    captured = {}

    def retry(_dsn, **kwargs):
        captured.update(kwargs)
        return {"job_status": "QUEUED", "batch_status": "RUNNING"}

    with exact_source(), patch(
        "universal_video.neon_worker.retry_job",
        side_effect=retry,
    ), patch("universal_video.neon_worker.finish_job") as finish:
        result = process_claim(
            "postgresql://queue",
            item,
            "worker-1",
            processor=lambda _: {"master_pdf_drive_id": "masterPdf000001"},
        )
    assert result["job_status"] == "QUEUED"
    assert captured["error_code"] == "UV_TERMINAL_CUSTOM_PROCESSOR_FORBIDDEN"
    finish.assert_not_called()


def test_source_is_reread_immediately_before_terminal_transition():
    item = claim()
    changed = drive_item(14, name="renamed.mp4")
    captured = {}
    terminal = terminal_result(item)
    done = done_contract(item)

    def retry(_dsn, **kwargs):
        captured.update(kwargs)
        return {"job_status": "QUEUED", "batch_status": "RUNNING"}

    with patch(
        "universal_video.neon_worker.access_token",
        return_value="fresh-token",
    ), patch(
        "universal_video.neon_worker.file_metadata",
        side_effect=[drive_item(14), drive_item(14), changed],
    ), patch(
        "bridge_runtime_hardening_r25_16.run",
        return_value=done,
    ), patch(
        "route_drive_job_outputs.route_outputs",
        return_value={"status": "ROUTED"},
    ), patch(
        "universal_video.neon_worker.build_terminal_evidence",
        return_value=terminal,
    ), patch(
        "universal_video.neon_worker.retry_job",
        side_effect=retry,
    ), patch("universal_video.neon_worker.finish_job") as finish:
        result = process_claim(
            "postgresql://queue",
            item,
            "worker-1",
            processor=stable_review_processor,
        )
    assert result["job_status"] == "QUEUED"
    finish.assert_not_called()


def test_stable_processor_refreshes_token_before_terminal_readback():
    item = claim()
    done = done_contract(item)
    observed = []
    with patch(
        "universal_video.neon_worker.access_token",
        side_effect=["source-token", "terminal-token"],
    ), patch(
        "universal_video.neon_worker.file_metadata",
        return_value=drive_item(14),
    ), patch(
        "bridge_runtime_hardening_r25_16.run",
        return_value=done,
    ), patch(
        "route_drive_job_outputs.route_outputs",
        return_value={"status": "ROUTED"},
    ), patch(
        "universal_video.neon_worker.build_terminal_evidence",
        side_effect=lambda _claim, _done, _route, token: observed.append(token)
        or {"terminal_receipt": {"synthetic": True}},
    ):
        result = stable_review_processor(item)
    assert observed == ["terminal-token"]
    assert result["terminal_receipt"] == {"synthetic": True}


def test_content_failure_is_ambiguous_and_independent():
    item = claim()
    captured = {}

    def processor(_):
        raise RuntimeError("VISUAL_GAP_CHECK_FAILED")

    def finish(_dsn, **kwargs):
        captured.update(kwargs)
        return {
            "job_status": kwargs["outcome"],
            "batch_status": "CANARY_BLOCKED",
            "released_jobs": 0,
        }

    with exact_source(), patch(
        "universal_video.neon_worker.finish_job",
        side_effect=finish,
    ):
        result = process_claim(
            "postgresql://queue",
            item,
            "worker-1",
            processor=processor,
        )
    assert result["job_status"] == "AMBIGUOUS"
    assert captured["error_code"] == "UV_CONTENT_AMBIGUOUS"
    assert captured["output"]["publication_state"] == "NOT_PUBLISHED"


def test_technical_failure_is_bounded_retry_not_false_terminal_success():
    item = claim()
    captured = {}

    def processor(_):
        raise RuntimeError("network unavailable")

    def retry(_dsn, **kwargs):
        captured.update(kwargs)
        return {
            "job_status": "QUEUED",
            "batch_status": "RUNNING",
            "retry_after": "later",
        }

    with exact_source(), patch(
        "universal_video.neon_worker.retry_job",
        side_effect=retry,
    ), patch("universal_video.neon_worker.finish_job") as finish:
        result = process_claim(
            "postgresql://queue",
            item,
            "worker-1",
            processor=processor,
        )
    assert result["job_status"] == "QUEUED"
    assert captured["max_attempts"] == 3
    assert captured["base_delay_seconds"] == 60
    assert captured["error_code"] == "UV_ITEM_FAILED"
    finish.assert_not_called()


def test_processing_timeout_is_bounded(monkeypatch):
    monkeypatch.setenv("UNIVERSAL_VIDEO_JOB_TIMEOUT_SECONDS", "899")
    with pytest.raises(NeonVideoWorkerError, match="TIMEOUT_INVALID"):
        process_claim(
            "postgresql://queue",
            claim(),
            "worker-1",
            processor=lambda _: {},
        )


def test_sql_serializes_enqueue_and_bounds_retry_contract():
    sql = (
        Path(__file__).parents[1] / "database/migrations/0056_universal_video_queue.sql"
    ).read_text()
    assert "pg_advisory_xact_lock(hashtextextended(p_request_key, 0))" in sql
    assert "CREATE OR REPLACE FUNCTION video_queue.retry_job" in sql
    assert "p_max_attempts NOT BETWEEN 1 AND 10" in sql
    assert "RETRY_SCHEDULED" in sql
    assert "next_attempt_at <= clock_timestamp()" in sql
    assert "UV_WORKER_CRASH_RETRY_EXHAUSTED" in sql
    assert "j.attempt_count < 3" in sql


def test_queue_migration_has_fail_closed_rollback():
    rollback = (
        Path(__file__).parents[1] / "database/rollbacks/0056_universal_video_queue.sql"
    ).read_text()
    assert "VIDEO_QUEUE_ROLLBACK_REFUSES_NONEMPTY_QUEUE" in rollback
    assert "DROP SCHEMA IF EXISTS video_queue CASCADE" in rollback
