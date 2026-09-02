from __future__ import annotations

import hashlib

import pytest

from universal_video import drive_readback_probe as subject


def test_probe_uploads_reads_checksums_and_deletes(monkeypatch):
    seen = {}
    monkeypatch.setattr(subject, "_folder", lambda *_: None)

    def upload(folder, name, body, token):
        seen["body"] = body
        return {"id": "probe0000000001", "name": name, "mimeType": "application/json",
                "size": str(len(body)), "parents": [folder],
                "md5Checksum": hashlib.md5(body, usedforsecurity=False).hexdigest()}

    monkeypatch.setattr(subject, "_upload_json", upload)
    monkeypatch.setattr(subject, "readback_artifact", lambda metadata, **kwargs: ({
        "drive_id": metadata["id"], "name": kwargs["expected_name"],
        "mime_type": "application/json", "size_bytes": len(seen["body"]),
        "provider_checksum": "md5:" + hashlib.md5(seen["body"], usedforsecurity=False).hexdigest(),
        "sha256": hashlib.sha256(seen["body"]).hexdigest(),
        "parent_folder_id": kwargs["expected_parent_id"], "readback_verified": True,
    }, seen["body"]))
    deleted = []
    monkeypatch.setattr(subject, "_delete_and_verify", lambda file_id, _token: deleted.append(file_id))
    monkeypatch.setattr(subject.requests, "delete", lambda *_args, **_kwargs: None)
    receipt = subject.run_probe(
        folder_id="outputfolder0001", runtime_sha="1" * 40,
        image_digest="sha256:" + "2" * 64, token="token",
    )
    assert receipt["status"] == "PASS"
    assert receipt["real_media_read"] is False
    assert receipt["real_video_result_written"] is False
    assert receipt["probe_deleted"] is True
    assert deleted == ["probe0000000001"]


def test_probe_fails_closed_when_readback_fails(monkeypatch):
    monkeypatch.setattr(subject, "_folder", lambda *_: None)
    monkeypatch.setattr(subject, "_upload_json", lambda *_args, **_kwargs: {"id": "probe0000000001"})
    monkeypatch.setattr(subject, "readback_artifact", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("unreadable")))
    monkeypatch.setattr(subject.requests, "delete", lambda *_args, **_kwargs: None)
    with pytest.raises(RuntimeError, match="unreadable"):
        subject.run_probe(
            folder_id="outputfolder0001", runtime_sha="1" * 40,
            image_digest="sha256:" + "2" * 64, token="token",
        )
