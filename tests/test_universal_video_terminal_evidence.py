from __future__ import annotations

import hashlib
import json

import pytest

from universal_video.terminal_evidence import (
    TerminalEvidenceError,
    build_terminal_evidence,
    readback_drive_bytes,
)


SOURCE_ID = "sourcefile0001"
MASTER_ID = "masterpdf00001"
DONE_ID = "aidonefile0001"
TARGET_ID = "targetfolder001"
JOB_ID = "a" * 32
REVISION = "3.1-free-r25.16"


def _fixture():
    master_body = b"%PDF-1.4\nsynthetic terminal evidence\n%%EOF\n"
    done = {
        "status": "AI_DONE",
        "job_id": JOB_ID,
        "algorithmRevision": REVISION,
        "original": {"driveId": SOURCE_ID},
        "masterPdf": {
            "driveId": MASTER_ID,
            "sha256": hashlib.sha256(master_body).hexdigest(),
            "pages": 3,
        },
    }
    done_body = json.dumps(
        done,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    claim = {
        "source_file_id": SOURCE_ID,
        "stable_job_key": JOB_ID,
        "algorithm_revision": REVISION,
        "output_folder_id": TARGET_ID,
    }
    route = {
        "stage": "OUTPUT_ROUTE",
        "status": "ROUTED",
        "job_id": JOB_ID,
        "target_folder_id": TARGET_ID,
        "source_untouched": True,
        "results": [
            {"kind": "master_pdf", "file_id": MASTER_ID, "result": "moved"},
            {"kind": "ai_done", "file_id": DONE_ID, "result": "moved"},
        ],
    }
    objects = {
        MASTER_ID: {
            "file_id": MASTER_ID,
            "name": "MASTER.pdf",
            "mime_type": "application/pdf",
            "size_bytes": len(master_body),
            "parents": [TARGET_ID],
            "sha256": hashlib.sha256(master_body).hexdigest(),
            "md5": hashlib.md5(master_body, usedforsecurity=False).hexdigest(),
            "body": master_body,
        },
        DONE_ID: {
            "file_id": DONE_ID,
            "name": f"AI_DONE_{JOB_ID}.json",
            "mime_type": "application/json",
            "size_bytes": len(done_body),
            "parents": [TARGET_ID],
            "sha256": hashlib.sha256(done_body).hexdigest(),
            "md5": hashlib.md5(done_body, usedforsecurity=False).hexdigest(),
            "body": done_body,
        },
    }

    def readback(file_id, _token, *, max_bytes):
        assert len(objects[file_id]["body"]) <= max_bytes
        return dict(objects[file_id])

    return claim, done, route, objects, readback


def test_build_terminal_evidence_requires_readable_checksums_and_locators():
    claim, done, route, _objects, readback = _fixture()
    result = build_terminal_evidence(claim, done, route, "token", readback=readback)
    assert result["artifact_locators"] == {
        "master_pdf_drive_id": MASTER_ID,
        "ai_done_drive_id": DONE_ID,
    }
    assert result["manifest"]["publication_state"] == "NOT_PUBLISHED"
    assert result["manifest"]["canonical_promotion_allowed"] is False
    assert result["manifest"]["database_persistence_allowed"] is False
    receipt = result["terminal_receipt"]
    assert receipt["result_readback_verified"] is True
    assert receipt["checksum_verified"] is True
    assert receipt["artifact_count"] == 2
    assert len(receipt["manifest_sha256"]) == 64
    assert result["terminal_evidence_sha256"] == receipt["evidence_sha256"]


@pytest.mark.parametrize(
    ("mutation", "error_code"),
    [
        (lambda claim, done, route, objects: route.update(status="AI_DONE_NOT_FOUND"), "UV_TERMINAL_ROUTE_UNVERIFIED"),
        (lambda claim, done, route, objects: route.update(results=[]), "UV_TERMINAL_LOCATOR_MISSING"),
        (lambda claim, done, route, objects: objects[MASTER_ID].update(parents=["wrongfolder0001"]), "UV_TERMINAL_RESULT_PARENT_MISMATCH"),
        (lambda claim, done, route, objects: objects[MASTER_ID].update(sha256="0" * 64), "UV_TERMINAL_MASTER_PDF_CHECKSUM_MISMATCH"),
        (lambda claim, done, route, objects: objects[DONE_ID].update(body=b"{}"), "UV_TERMINAL_AI_DONE_MISMATCH"),
    ],
)
def test_terminal_evidence_fails_closed(mutation, error_code):
    claim, done, route, objects, readback = _fixture()
    mutation(claim, done, route, objects)
    with pytest.raises(TerminalEvidenceError) as caught:
        build_terminal_evidence(claim, done, route, "token", readback=readback)
    assert caught.value.error_code == error_code


class _Response:
    def __init__(self, chunks):
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def raise_for_status(self):
        return None

    def iter_content(self, _size):
        yield from self._chunks


def test_readback_drive_bytes_streams_and_matches_metadata():
    body = b"readback-object"
    metadata = {
        "id": MASTER_ID,
        "name": "MASTER.pdf",
        "mimeType": "application/pdf",
        "size": str(len(body)),
        "parents": [TARGET_ID],
        "md5Checksum": hashlib.md5(body, usedforsecurity=False).hexdigest(),
        "sha256Checksum": hashlib.sha256(body).hexdigest(),
    }

    def metadata_loader(file_id, token):
        assert file_id == MASTER_ID and token == "token"
        return metadata

    def get(*_args, **_kwargs):
        return _Response([body[:4], body[4:]])

    result = readback_drive_bytes(
        MASTER_ID,
        "token",
        max_bytes=1024,
        metadata_loader=metadata_loader,
        get=get,
    )
    assert result["body"] == body
    assert result["sha256"] == hashlib.sha256(body).hexdigest()
    assert result["parents"] == [TARGET_ID]


def test_readback_drive_bytes_refuses_declared_size_mismatch():
    body = b"abc"

    def metadata_loader(_file_id, _token):
        return {
            "id": MASTER_ID,
            "name": "MASTER.pdf",
            "mimeType": "application/pdf",
            "size": "4",
            "parents": [TARGET_ID],
        }

    with pytest.raises(TerminalEvidenceError) as caught:
        readback_drive_bytes(
            MASTER_ID,
            "token",
            max_bytes=1024,
            metadata_loader=metadata_loader,
            get=lambda *_args, **_kwargs: _Response([body]),
        )
    assert caught.value.error_code == "UV_TERMINAL_READBACK_SIZE_MISMATCH"
