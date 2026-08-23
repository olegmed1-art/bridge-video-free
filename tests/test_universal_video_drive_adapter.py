import hashlib
import json

import pytest

from universal_video import drive_adapter
from universal_video.drive_adapter import _oauth_parts, download_file


ENV_KEYS = (
    "GOOGLE_DRIVE_OAUTH_JSON",
    "GOOGLE_DRIVE_OAUTH_JSON_FILE",
    "GOOGLE_DRIVE_OAUTH_CLIENT_ID",
    "GOOGLE_DRIVE_OAUTH_CLIENT_SECRET",
    "GOOGLE_DRIVE_OAUTH_REFRESH_TOKEN",
    "GOOGLE_SERVICE_ACCOUNT_JSON",
)


def _clear(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_file_backed_user_oauth(monkeypatch, tmp_path):
    _clear(monkeypatch)
    secret = tmp_path / "oauth.json"
    secret.write_text(
        json.dumps(
            {
                "client_id": "client-id",
                "client_secret": "client-secret",
                "refresh_token": "refresh-token",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GOOGLE_DRIVE_OAUTH_JSON_FILE", str(secret))
    assert _oauth_parts() == ("client-id", "client-secret", "refresh-token")


def test_direct_oauth_env_precedes_file(monkeypatch, tmp_path):
    _clear(monkeypatch)
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("not-json", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_DRIVE_OAUTH_JSON_FILE", str(bad_file))
    monkeypatch.setenv(
        "GOOGLE_DRIVE_OAUTH_JSON",
        json.dumps(
            {
                "client_id": "direct-id",
                "client_secret": "direct-secret",
                "refresh_token": "direct-refresh",
            }
        ),
    )
    assert _oauth_parts() == ("direct-id", "direct-secret", "direct-refresh")


def test_oauth_file_path_must_be_absolute(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("GOOGLE_DRIVE_OAUTH_JSON_FILE", "relative/oauth.json")
    with pytest.raises(RuntimeError, match="absolute path"):
        _oauth_parts()


def test_incomplete_oauth_fails_closed(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv(
        "GOOGLE_DRIVE_OAUTH_JSON",
        json.dumps({"client_id": "id", "client_secret": "secret"}),
    )
    with pytest.raises(RuntimeError, match="incomplete"):
        _oauth_parts()


def test_drive_declared_size_is_rejected_before_download(monkeypatch, tmp_path):
    monkeypatch.setattr(
        drive_adapter,
        "file_metadata",
        lambda file_id, token: {"mimeType": "video/mp4", "size": "1001"},
    )

    def unexpected_get(*args, **kwargs):
        pytest.fail("download request must not start for declared oversize source")

    monkeypatch.setattr(drive_adapter.requests, "get", unexpected_get)
    with pytest.raises(RuntimeError, match="source-size limit"):
        download_file("file-id-12345", tmp_path / "video.mp4", "token", max_bytes=1000)


def test_streaming_size_limit_removes_partial_download(monkeypatch, tmp_path):
    monkeypatch.setattr(
        drive_adapter,
        "file_metadata",
        lambda file_id, token: {"mimeType": "video/mp4", "size": "0"},
    )

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield b"123456"
            yield b"abcdef"

    monkeypatch.setattr(drive_adapter.requests, "get", lambda *args, **kwargs: Response())
    destination = tmp_path / "video.mp4"
    with pytest.raises(RuntimeError, match="download exceeded"):
        download_file("file-id-12345", destination, "token", max_bytes=10)
    assert not destination.exists()


def test_download_verifies_drive_checksum_and_returns_stream_digest(monkeypatch, tmp_path):
    payload = b"verified-video-bytes"
    md5 = hashlib.md5(payload, usedforsecurity=False).hexdigest()
    sha256 = hashlib.sha256(payload).hexdigest()
    meta = {
        "mimeType": "video/mp4",
        "size": str(len(payload)),
        "md5Checksum": md5,
        "sha256Checksum": sha256,
    }

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield payload

    monkeypatch.setattr(drive_adapter.requests, "get", lambda *args, **kwargs: Response())
    destination = tmp_path / "video.mp4"
    result = download_file(
        "file-id-12345",
        destination,
        "token",
        max_bytes=1000,
        metadata=meta,
    )
    assert destination.read_bytes() == payload
    assert result["_download_sha256"] == sha256
    assert result["_download_md5"] == md5


def test_checksum_mismatch_removes_download(monkeypatch, tmp_path):
    payload = b"corrupted-or-stale-content"
    meta = {
        "mimeType": "video/mp4",
        "size": str(len(payload)),
        "md5Checksum": "0" * 32,
    }

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield payload

    monkeypatch.setattr(drive_adapter.requests, "get", lambda *args, **kwargs: Response())
    destination = tmp_path / "video.mp4"
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        download_file(
            "file-id-12345",
            destination,
            "token",
            max_bytes=1000,
            metadata=meta,
        )
    assert not destination.exists()
