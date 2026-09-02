from __future__ import annotations

import pytest

from universal_video import single_canary as subject


SOURCE = {
    "file_id": "source00000001",
    "name": "Lesson 13.mp4",
    "mime_type": "video/mp4",
    "size_bytes": 696237577,
    "parent_id": "sourcefolder0001",
    "checksum": "md5:" + "a" * 32,
}


def _request():
    return {
        "schema": subject.SCHEMA,
        "request_key": "issue-881-exact-canary",
        "runtime_sha": "1" * 40,
        "image_digest": "sha256:" + "2" * 64,
        "source": dict(SOURCE),
        "output_folder_id": "outputfolder0001",
        "work_folder_id": "workfolder000001",
        "processing_profile": subject.EXACT_CANARY_PROFILE,
        "algorithm_revision": subject.EXACT_CANARY_REVISION,
        "canonical_promotion_allowed": False,
        "publication_state": "NOT_PUBLISHED",
    }


def test_prepare_captures_provider_checksum_without_media(monkeypatch):
    monkeypatch.setattr(subject, "read_source_identity", lambda *_: dict(SOURCE))
    checked = []
    monkeypatch.setattr(subject, "_folder", lambda folder, _token, field: checked.append((folder, field)))
    result = subject.prepare_exact_request(
        request_key="issue-881-exact-canary", runtime_sha="1" * 40,
        image_digest="sha256:" + "2" * 64,
        expected_source={key: SOURCE[key] for key in ("file_id", "name", "mime_type", "size_bytes", "parent_id")},
        output_folder_id="outputfolder0001", work_folder_id="workfolder000001", token="token",
    )
    assert result["source"]["checksum"] == SOURCE["checksum"]
    assert len(checked) == 2


def test_prepare_fails_closed_on_source_change(monkeypatch):
    monkeypatch.setattr(subject, "read_source_identity", lambda *_: dict(SOURCE, size_bytes=SOURCE["size_bytes"] + 1))
    with pytest.raises(subject.ExactSingleCanaryError, match="PREFLIGHT_MISMATCH"):
        subject.prepare_exact_request(
            request_key="issue-881-exact-canary", runtime_sha="1" * 40,
            image_digest="sha256:" + "2" * 64,
            expected_source={key: SOURCE[key] for key in ("file_id", "name", "mime_type", "size_bytes", "parent_id")},
            output_folder_id="outputfolder0001", work_folder_id="workfolder000001", token="token",
        )


def test_floating_runtime_or_image_is_rejected():
    request = _request()
    request["runtime_sha"] = "main"
    with pytest.raises(subject.ExactSingleCanaryError, match="RUNTIME_IMAGE"):
        subject.validate_exact_request(request)
    request = _request()
    request["image_digest"] = "latest"
    with pytest.raises(subject.ExactSingleCanaryError, match="RUNTIME_IMAGE"):
        subject.validate_exact_request(request)


def test_enqueue_registers_one_item_without_folder_enumeration(monkeypatch):
    monkeypatch.setattr(subject, "verify_expected_source_identity", lambda *_: dict(SOURCE))
    monkeypatch.setattr(subject, "_folder", lambda *_: None)
    calls = {}

    def build(base, raw):
        calls["base"] = base
        calls["raw"] = raw
        return {**base, "expected_count": 1, "files": [{"file_id": SOURCE["file_id"]}]}

    monkeypatch.setattr(subject, "build_drive_manifest", build)
    monkeypatch.setattr(subject, "enqueue_manifest", lambda *_: {
        "batch_id": "batch0000000001", "status": "QUEUED_CANARY", "expected_count": 1,
        "canary_source_file_id": SOURCE["file_id"], "inventory_sha256": "3" * 64,
        "canonical_promotion_allowed": False,
    })
    receipt = subject.enqueue_exact_single_canary(_request(), database_url="dsn", token="token")
    assert receipt["status"] == "QUEUED_EXACTLY_ONE"
    assert receipt["expected_count"] == 1
    assert len(calls["raw"]) == 1
    assert calls["base"]["processing_profile"] == subject.EXACT_CANARY_PROFILE


def test_receipt_that_could_release_more_items_is_rejected(monkeypatch):
    monkeypatch.setattr(subject, "verify_expected_source_identity", lambda *_: dict(SOURCE))
    monkeypatch.setattr(subject, "_folder", lambda *_: None)
    monkeypatch.setattr(subject, "build_drive_manifest", lambda base, raw: {
        **base, "expected_count": 1, "files": [{"file_id": SOURCE["file_id"]}]})
    monkeypatch.setattr(subject, "enqueue_manifest", lambda *_: {
        "expected_count": 2, "canary_source_file_id": SOURCE["file_id"],
        "canonical_promotion_allowed": False})
    with pytest.raises(subject.ExactSingleCanaryError, match="RECEIPT_INVALID"):
        subject.enqueue_exact_single_canary(_request(), database_url="dsn", token="token")
