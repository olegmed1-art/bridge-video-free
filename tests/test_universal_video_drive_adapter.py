import json

import pytest

from universal_video.drive_adapter import _oauth_parts


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
