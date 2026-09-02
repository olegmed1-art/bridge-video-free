from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from universal_video.route_receipt_v2 import RouteReceiptV2Error, discover_route_receipt
from universal_video.terminal_evidence_v2 import (
    build_terminal_evidence,
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
         "size": str(len(pdf)), "parents": [claim["output_folder_id"]]},
        {"id": "ai-done-file-123456", "name": f"AI_DONE_{claim['stable_job_key']}.json",
         "mimeType": "application/json", "size": str(len(ai)), "parents": [claim["output_folder_id"]]},
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
    assert [a["kind"] for a in evidence["artifact_manifest"]["artifacts"]] == ["master_pdf", "ai_done"]
    assert evidence["terminal_receipt"]["artifact_count"] == 2


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
