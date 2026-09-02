#!/usr/bin/env python3
"""Authentication adapter for Bridge 3.1 FREE.

Zero-cost invariant:
- Google Drive access uses only the user's OAuth refresh token stored in
  GOOGLE_DRIVE_OAUTH_JSON (or the legacy three OAuth secret variables).
- There is NO Google Cloud Workload Identity / ADC fallback.
- There is NO service-account or billable-cloud dependency in the worker path.

The heavy video runtime is imported lazily so lightweight Drive/status tools can
reuse ``user_oauth_token`` without importing ASR and database dependencies.

GOOGLE_DRIVE_OAUTH_JSON (or the file named by GOOGLE_DRIVE_OAUTH_JSON_FILE) must contain:
  client_id, client_secret, refresh_token
"""

import json
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
TOKEN_URI = "https://oauth2.googleapis.com/token"


def _oauth_parts():
    packed = os.getenv("GOOGLE_DRIVE_OAUTH_JSON", "").strip()
    if not packed:
        file_name = os.getenv("GOOGLE_DRIVE_OAUTH_JSON_FILE", "").strip()
        if file_name:
            path = Path(file_name)
            if not path.is_absolute():
                raise RuntimeError("BLOCKED_ACCESS: GOOGLE_DRIVE_OAUTH_JSON_FILE must be absolute")
            try:
                packed = path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise RuntimeError("BLOCKED_ACCESS: cannot read GOOGLE_DRIVE_OAUTH_JSON_FILE") from exc
            if not packed:
                raise RuntimeError("BLOCKED_ACCESS: GOOGLE_DRIVE_OAUTH_JSON_FILE is empty")
    if packed:
        try:
            data = json.loads(packed)
        except json.JSONDecodeError as e:
            raise RuntimeError("BLOCKED_ACCESS: invalid GOOGLE_DRIVE_OAUTH_JSON") from e
        client_id = str(data.get("client_id", "")).strip()
        client_secret = str(data.get("client_secret", "")).strip()
        refresh_token = str(data.get("refresh_token", "")).strip()
    else:
        client_id = os.getenv("GOOGLE_DRIVE_OAUTH_CLIENT_ID", "").strip()
        client_secret = os.getenv("GOOGLE_DRIVE_OAUTH_CLIENT_SECRET", "").strip()
        refresh_token = os.getenv("GOOGLE_DRIVE_OAUTH_REFRESH_TOKEN", "").strip()

    present = [bool(client_id), bool(client_secret), bool(refresh_token)]
    if any(present) and not all(present):
        raise RuntimeError("BLOCKED_ACCESS: incomplete Google Drive OAuth credentials")
    return client_id, client_secret, refresh_token


def user_oauth_token():
    client_id, client_secret, refresh_token = _oauth_parts()
    if not all([client_id, client_secret, refresh_token]):
        return None

    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=[DRIVE_SCOPE],
    )
    credentials.refresh(Request())
    if not credentials.token:
        raise RuntimeError("BLOCKED_ACCESS: Google Drive user OAuth token unavailable")
    return credentials.token


def drive_token():
    token = user_oauth_token()
    if not token:
        raise RuntimeError(
            "BLOCKED_ACCESS: GOOGLE_DRIVE_OAUTH_JSON is required; paid/cloud fallback is forbidden"
        )
    return token


def main() -> None:
    from run_drive_3_1_free_generic import main as generic_main

    generic_main(drive_token)


if __name__ == "__main__":
    main()
