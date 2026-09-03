from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from universal_video.route_receipt_v2 import RouteReceiptV2Error, discover_route_receipt
from universal_video.terminal_evidence_v2 import (
    build_terminal_evidence,
    reverify_terminal_output_live,
    validate_terminal_output,
)


def _fixture():
    pdf = b"%PDF-1.7\nsynthetic\n"
    claim = {
        "stable_job_key": "1" * 32, "source_file_id": "source-file-123456",
        "source_name": "lesson.mp4", "source_mime_type": "video/mp4",
        "source_size_bytes": 42, "source_folder_id": "source-folder-123456",
        "source_checksum": "md5:" + "a" * 32, "output_folder_id": "output-folder-123456",
        "algorithm_revision": "3.1-free-r25.16",
    }
    done = {"status": "AI_DONE", "job_id": claim["stable_job_key"],
            "algorithmRevision": claim["algorithm_revision"],
            "original": {"driveId": claim["source_file_id"]},
            "masterPdf": {"driveId": "master-pdf-123456", "sha256": hashlib.sha256(pdf).hexdigest()}}
    ai = json.dumps(done, ensure_ascii=False).encode()
    items = [
        {"id": "master-pdf-123456", "name": "master.pdf", "mimeType": "application/pdf",
         "size": str(len(pdf)), "parents": [claim["output_folder_id"]],
         "modifiedTime": "2026-09-02T20:00:00Z", "version": "101", "trashed": False},
        {"id": "ai-done-file-123456", "name": f"AI_DONE_{claim['stable_job_key']}.json",
         "mimeType": "application/json", "size": str(len(ai)), "parents": [claim["output_folder_id"]],
         "modifiedTime": "2026-09-02T20:00:01Z", "version": "102", "trashed": False},
    ]

    def folder(_folder, _token): return [dict(item) for item in items]
    def download(file_id, path: Path, _token, **kwargs):
        body = ai if file_id.startswith("ai-done") else pdf
        path.write_bytes(body)
        return {**kwargs.get("metadata", {}), "_download_sha256": hashlib.sha256(body).hexdigest()}
    def metadata(file_id, _token):
        return next(dict(item) for item in items if item["id"] == file_id)
    return claim, done, items, folder, download, metadata


def test_v2_binds_source_pdf_and_routed_ai_done_without_credentials():
    claim, done, _items, folder, download, metadata = _fixture()
    route = discover_route_receipt(claim, done, "mock", folder_lister=folder, metadata_reader=metadata)
    evidence = build_terminal_evidence(
        claim, done, route, "mock", metadata_reader=metadata, downloader=download
    )
    candidate = {"result_mode": "SHADOW_REVIEW_ONLY", "canonical_promotion_allowed": False,
                 "database_persistence_allowed": False, "publication_state": "NOT_PUBLISHED",
                 "source_file_id": claim["source_file_id"], "stable_job_key": claim["stable_job_key"],
                 "algorithm_revision": claim["algorithm_revision"], **evidence}
    validate_terminal_output(claim, candidate)
    artifacts = evidence["artifact_manifest"]["artifacts"]
    assert [a["kind"] for a in artifacts] == ["master_pdf", "ai_done"]
    assert [a["version"] for a in artifacts] == ["101", "102"]
    assert all(a["modified_time"] for a in artifacts)
    assert evidence["terminal_receipt"]["artifact_count"] == 2


def test_v2_preserves_nullable_source_checksum_contract():
    claim, done, _items, folder, download, metadata = _fixture()
    claim["source_checksum"] = None
    route = discover_route_receipt(claim, done, "mock", folder_lister=folder, metadata_reader=metadata)
    evidence = build_terminal_evidence(
        claim, done, route, "mock", metadata_reader=metadata, downloader=download
    )
    candidate = {
        "result_mode": "SHADOW_REVIEW_ONLY",
        "canonical_promotion_allowed": False,
        "database_persistence_allowed": False,
        "publication_state": "NOT_PUBLISHED",
        "source_file_id": claim["source_file_id"],
        "stable_job_key": claim["stable_job_key"],
        "algorithm_revision": claim["algorithm_revision"],
        **evidence,
    }
    assert evidence["artifact_manifest"]["source_identity"]["checksum"] is None
    validate_terminal_output(claim, candidate)


@pytest.mark.parametrize("checksum", ["", "md5:not-hex", 7])
def test_v2_rejects_invalid_non_null_source_checksum(checksum):
    claim, done, _items, folder, download, metadata = _fixture()
    claim["source_checksum"] = checksum
    route = discover_route_receipt(claim, done, "mock", folder_lister=folder, metadata_reader=metadata)
    with pytest.raises(Exception, match="UV_SOURCE_IDENTITY_INVALID"):
        build_terminal_evidence(
            claim, done, route, "mock", metadata_reader=metadata, downloader=download
        )


def test_route_receipt_rejects_missing_duplicate_and_mismatched_content():
    claim, done, items, _folder, download, _metadata = _fixture()
    metadata = lambda file_id, _token: next(dict(item) for item in items if item["id"] == file_id)
    with pytest.raises(RouteReceiptV2Error, match="CARDINALITY"):
        discover_route_receipt(claim, done, "mock", folder_lister=lambda *_: [], metadata_reader=metadata)
    with pytest.raises(RouteReceiptV2Error, match="CARDINALITY"):
        discover_route_receipt(claim, done, "mock", folder_lister=lambda *_: items * 2, metadata_reader=metadata)
    wrong = {**done, "algorithmRevision": "wrong"}
    with pytest.raises(RouteReceiptV2Error, match="IDENTITY_INVALID"):
        discover_route_receipt(claim, wrong, "mock", folder_lister=lambda *_: items, metadata_reader=metadata)


def test_terminal_validation_rejects_missing_null_type_and_mismatch():
    claim, done, _items, folder, download, metadata = _fixture()
    route = discover_route_receipt(claim, done, "mock", folder_lister=folder, metadata_reader=metadata)
    evidence = build_terminal_evidence(claim, done, route, "mock", metadata_reader=metadata, downloader=download)
    base = {"result_mode": "SHADOW_REVIEW_ONLY", "canonical_promotion_allowed": False,
            "database_persistence_allowed": False, "publication_state": "NOT_PUBLISHED",
            "source_file_id": claim["source_file_id"], "stable_job_key": claim["stable_job_key"],
            "algorithm_revision": claim["algorithm_revision"], **evidence}
    for bad in ({k: v for k, v in base.items() if k != "publication_state"},
                {**base, "publication_state": None}, {**base, "publication_state": 7},
                {**base, "source_file_id": "wrong"}):
        with pytest.raises(Exception):
            validate_terminal_output(claim, bad)


def test_terminal_readback_rejects_same_size_drive_version_change():
    claim, done, items, folder, download, _metadata = _fixture()
    reads = {item["id"]: 0 for item in items}

    def changing_metadata(file_id, _token):
        item = next(dict(value) for value in items if value["id"] == file_id)
        reads[file_id] += 1
        if reads[file_id] > 1:
            item["version"] = str(int(item["version"]) + reads[file_id] - 1)
        return item

    route = discover_route_receipt(
        claim, done, "mock", folder_lister=folder, metadata_reader=changing_metadata
    )
    with pytest.raises(Exception, match="UV_TERMINAL_METADATA_CHANGED"):
        build_terminal_evidence(
            claim, done, route, "mock", metadata_reader=changing_metadata, downloader=download
        )


def test_terminal_readback_rejects_artifact_moved_to_trash_after_route():
    claim, done, items, folder, download, _metadata = _fixture()
    reads = {item["id"]: 0 for item in items}

    def trashed_metadata(file_id, _token):
        item = next(dict(value) for value in items if value["id"] == file_id)
        reads[file_id] += 1
        if file_id == "master-pdf-123456" and reads[file_id] >= 2:
            item["trashed"] = True
        return item

    route = discover_route_receipt(
        claim, done, "mock", folder_lister=folder, metadata_reader=trashed_metadata
    )
    with pytest.raises(Exception, match="UV_TERMINAL_ARTIFACT_TRASHED"):
        build_terminal_evidence(
            claim, done, route, "mock", metadata_reader=trashed_metadata, downloader=download
        )


def test_terminal_reverify_rejects_identical_bytes_at_new_drive_revision():
    claim, done, items, folder, download, metadata = _fixture()
    route = discover_route_receipt(
        claim, done, "mock", folder_lister=folder, metadata_reader=metadata
    )
    evidence = build_terminal_evidence(
        claim, done, route, "mock", metadata_reader=metadata, downloader=download
    )
    candidate = {
        "result_mode": "SHADOW_REVIEW_ONLY",
        "canonical_promotion_allowed": False,
        "database_persistence_allowed": False,
        "publication_state": "NOT_PUBLISHED",
        "source_file_id": claim["source_file_id"],
        "stable_job_key": claim["stable_job_key"],
        "algorithm_revision": claim["algorithm_revision"],
        **evidence,
    }

    items[0]["version"] = "103"
    items[0]["modifiedTime"] = "2026-09-02T20:00:02Z"

    with pytest.raises(Exception, match="UV_TERMINAL_LIVE_EVIDENCE_MISMATCH"):
        reverify_terminal_output_live(
            claim, candidate, "mock", metadata_reader=metadata, downloader=download
        )


def test_precanary_synthetic_terminal_v2_cli_passes_without_network():
    completed = subprocess.run(
        [sys.executable, "-m", "universal_video.precanary", "synthetic-result-contract"],
        check=True,
        capture_output=True,
        text=True,
    )
    receipt = json.loads(completed.stdout)
    assert receipt["status"] == "PASS"
    assert receipt["gate"] == "SYNTHETIC_RESULT_CONTRACT"
    assert receipt["terminal_contract_version"] == "v2"
    assert receipt["artifact_count"] == 2
    assert receipt["master_pdf_verified"] is True
    assert receipt["ai_done_verified"] is True
    assert receipt["drive_write_performed"] is False
