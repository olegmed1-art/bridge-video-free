"""Minimal Google Drive adapter for universal-video sources.

The preferred credential boundary is the existing user OAuth refresh-token
bundle. GitHub Actions may supply it directly through GOOGLE_DRIVE_OAUTH_JSON;
Oracle should normally use GOOGLE_DRIVE_OAUTH_JSON_FILE so the JSON secret stays
in a dedicated protected file instead of a systemd environment file. A service
account is supported only as an explicit alternative. Credentials are never
written by this module.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import requests

DRIVE = "https://www.googleapis.com/drive/v3"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
TOKEN_URI = "https://oauth2.googleapis.com/token"


def _read_json_secret() -> str:
    direct = os.getenv("GOOGLE_DRIVE_OAUTH_JSON", "").strip()
    if direct:
        return direct
    file_name = os.getenv("GOOGLE_DRIVE_OAUTH_JSON_FILE", "").strip()
    if not file_name:
        return ""
    path = Path(file_name)
    if not path.is_absolute():
        raise RuntimeError("GOOGLE_DRIVE_OAUTH_JSON_FILE must be an absolute path")
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError("cannot read GOOGLE_DRIVE_OAUTH_JSON_FILE") from exc
    if not raw:
        raise RuntimeError("GOOGLE_DRIVE_OAUTH_JSON_FILE is empty")
    return raw


def _oauth_parts() -> tuple[str, str, str]:
    packed = _read_json_secret()
    if packed:
        try:
            data = json.loads(packed)
        except json.JSONDecodeError as exc:
            raise RuntimeError("invalid Google Drive OAuth JSON") from exc
        if not isinstance(data, dict):
            raise RuntimeError("Google Drive OAuth JSON must be an object")
        client_id = str(data.get("client_id") or "").strip()
        client_secret = str(data.get("client_secret") or "").strip()
        refresh_token = str(data.get("refresh_token") or "").strip()
    else:
        client_id = os.getenv("GOOGLE_DRIVE_OAUTH_CLIENT_ID", "").strip()
        client_secret = os.getenv("GOOGLE_DRIVE_OAUTH_CLIENT_SECRET", "").strip()
        refresh_token = os.getenv("GOOGLE_DRIVE_OAUTH_REFRESH_TOKEN", "").strip()
    present = [bool(client_id), bool(client_secret), bool(refresh_token)]
    if any(present) and not all(present):
        raise RuntimeError("incomplete Google Drive OAuth credentials")
    return client_id, client_secret, refresh_token


def _user_oauth_token() -> str | None:
    client_id, client_secret, refresh_token = _oauth_parts()
    if not all((client_id, client_secret, refresh_token)):
        return None
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=[DRIVE_SCOPE],
    )
    creds.refresh(Request())
    return str(creds.token) if creds.token else None


def _service_account_token() -> str | None:
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        return None
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("invalid GOOGLE_SERVICE_ACCOUNT_JSON") from exc
    if not isinstance(info, dict):
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON must be an object")
    creds = service_account.Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )
    creds.refresh(Request())
    return str(creds.token) if creds.token else None


def access_token() -> str:
    token = _user_oauth_token()
    if token:
        return token
    token = _service_account_token()
    if token:
        return token
    raise RuntimeError(
        "Google Drive credentials are not configured; use GOOGLE_DRIVE_OAUTH_JSON, "
        "GOOGLE_DRIVE_OAUTH_JSON_FILE or GOOGLE_SERVICE_ACCOUNT_JSON"
    )


def file_metadata(file_id: str, token: str) -> dict:
    response = requests.get(
        f"{DRIVE}/files/{file_id}",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "fields": (
                "id,name,mimeType,size,modifiedTime,version,trashed,parents,"
                "md5Checksum,sha1Checksum,sha256Checksum"
            )
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("invalid Google Drive metadata response")
    # Every live source/artifact metadata read is fail-closed on trash state.
    # files.get can still read recoverable trashed objects, so allowing a true
    # or omitted state would let a source/artifact disappear from its folder
    # while retaining the same id, checksum, size and revision.
    if payload.get("trashed") is not False:
        raise RuntimeError("Google Drive file is trashed or trash state unavailable")
    return payload


def list_folder_files(folder_id: str, token: str) -> list[dict]:
    """List one exact Drive folder without recursive or fuzzy discovery."""

    folder = file_metadata(folder_id, token)
    if folder.get("mimeType") != "application/vnd.google-apps.folder":
        raise RuntimeError("Google Drive source id is not a folder")
    items: list[dict] = []
    page_token: str | None = None
    while True:
        params = {
            "q": f"'{folder_id}' in parents and trashed=false",
            "fields": (
                "nextPageToken,files(id,name,mimeType,size,parents,modifiedTime,version,trashed,"
                "md5Checksum,sha1Checksum,sha256Checksum)"
            ),
            "pageSize": 1000,
            "spaces": "drive",
        }
        if page_token:
            params["pageToken"] = page_token
        response = requests.get(
            f"{DRIVE}/files",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        page = payload.get("files") or []
        if not isinstance(page, list):
            raise RuntimeError("invalid Google Drive folder listing")
        items.extend(dict(item) for item in page if isinstance(item, dict))
        page_token = str(payload.get("nextPageToken") or "") or None
        if not page_token:
            return items


def _validate_binary_metadata(meta: dict, *, max_bytes: int | None) -> None:
    mime = str(meta.get("mimeType") or "")
    if mime.startswith("application/vnd.google-apps."):
        raise RuntimeError("native Google Workspace files are not video sources")
    if meta.get("trashed") is not False:
        raise RuntimeError("Google Drive binary is trashed or trash state unavailable")
    if max_bytes is not None:
        try:
            declared = int(meta.get("size") or 0)
        except (TypeError, ValueError):
            declared = 0
        if declared > max_bytes:
            raise RuntimeError("Google Drive video exceeds configured source-size limit")


def download_file(
    file_id: str,
    destination: Path,
    token: str,
    *,
    max_bytes: int | None = None,
    metadata: dict | None = None,
) -> dict:
    meta = dict(metadata) if metadata is not None else file_metadata(file_id, token)
    _validate_binary_metadata(meta, max_bytes=max_bytes)

    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    sha256 = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)
    try:
        with requests.get(
            f"{DRIVE}/files/{file_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"alt": "media"},
            stream=True,
            timeout=180,
        ) as response:
            response.raise_for_status()
            with destination.open("wb") as handle:
                for chunk in response.iter_content(8 * 1024 * 1024):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if max_bytes is not None and written > max_bytes:
                        raise RuntimeError("Google Drive download exceeded configured source-size limit")
                    sha256.update(chunk)
                    md5.update(chunk)
                    handle.write(chunk)

        actual_sha256 = sha256.hexdigest()
        actual_md5 = md5.hexdigest()
        expected_sha256 = str(meta.get("sha256Checksum") or "").strip().lower()
        expected_md5 = str(meta.get("md5Checksum") or "").strip().lower()
        if expected_sha256 and actual_sha256.lower() != expected_sha256:
            raise RuntimeError("Google Drive SHA-256 checksum mismatch after download")
        if expected_md5 and actual_md5.lower() != expected_md5:
            raise RuntimeError("Google Drive MD5 checksum mismatch after download")
        declared_size = int(meta.get("size") or 0)
        if declared_size <= 0 or written != declared_size:
            raise RuntimeError("Google Drive download size mismatch")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise

    meta["_download_sha256"] = actual_sha256
    meta["_download_md5"] = actual_md5
    return meta


def hash_remote_file(file_id: str, token: str, *, expected_size: int,
                     expected_sha256: str, deadline_seconds: float = 900) -> dict:
    """Hash complete remote bytes with bounded memory; never store or mutate Drive.

    No partial/Range resume: interruptions produce no success receipt. An outer
    worker deadline must additionally bound DNS/connect and process lifetime.
    """
    if expected_size < 0 or len(expected_sha256) != 64 or deadline_seconds <= 0:
        raise RuntimeError("invalid remote read-back bounds")
    started = time.monotonic()
    size = 0
    digest = hashlib.sha256()
    with requests.get(f"{DRIVE}/files/{file_id}",
                      headers={"Authorization": f"Bearer {token}"},
                      params={"alt": "media", "supportsAllDrives": True},
                      stream=True, timeout=(30, 60)) as response:
        response.raise_for_status()
        for chunk in response.iter_content(8 * 1024 * 1024):
            if time.monotonic() - started > deadline_seconds:
                raise TimeoutError("Drive read-back deadline exceeded")
            size += len(chunk)
            if size > expected_size:
                raise RuntimeError("Drive read-back size mismatch")
            digest.update(chunk)
    observed = digest.hexdigest()
    if size != expected_size or observed != expected_sha256:
        raise RuntimeError("Drive read-back size/SHA-256 mismatch")
    return {"file_id": file_id, "size_bytes": size, "sha256": observed,
            "verification": "CONTENT_READBACK_SHA256"}


def original_snapshot(meta: dict) -> dict:
    """A complete identity/version snapshot; missing fields never prove integrity."""
    keys = ("id", "name", "mimeType", "size", "version", "modifiedTime", "parents", "trashed")
    if any(key not in meta for key in keys) or meta.get("trashed") is not False:
        raise RuntimeError("Drive original identity/version is incomplete")
    if not all(str(meta[key]).strip() for key in keys[:6]) or not isinstance(meta["parents"], list):
        raise RuntimeError("Drive original identity/version is invalid")
    return {key: (sorted(meta[key]) if key == "parents" else meta[key]) for key in keys}


__all__ = ["access_token", "download_file", "file_metadata", "list_folder_files",
           "hash_remote_file", "original_snapshot"]
