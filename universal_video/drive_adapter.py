"""Minimal Google Drive adapter for universal-video sources.

Uses the same service-account secret boundary as the existing video pipeline.
No credentials are persisted by this module.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests

DRIVE = "https://www.googleapis.com/drive/v3"


def access_token() -> str:
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is required for google_drive sources")
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    info = json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )
    creds.refresh(Request())
    return str(creds.token)


def file_metadata(file_id: str, token: str) -> dict:
    response = requests.get(
        f"{DRIVE}/files/{file_id}",
        headers={"Authorization": f"Bearer {token}"},
        params={"fields": "id,name,mimeType,size,modifiedTime,parents"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def download_file(file_id: str, destination: Path, token: str) -> dict:
    meta = file_metadata(file_id, token)
    mime = str(meta.get("mimeType") or "")
    if mime.startswith("application/vnd.google-apps."):
        raise RuntimeError("native Google Workspace files are not video sources")
    destination.parent.mkdir(parents=True, exist_ok=True)
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
                if chunk:
                    handle.write(chunk)
    return meta


__all__ = ["access_token", "download_file", "file_metadata"]
