#!/usr/bin/env python3
"""Authentication adapter for Bridge 3.1 FREE.

GitHub Workload Identity Federation remains the keyless default identity for
Google Cloud. For Google Drive file creation in a user's My Drive, the runner
uses a user OAuth refresh token supplied through one GitHub Actions secret:
GOOGLE_DRIVE_OAUTH_JSON.

The secret value is JSON with keys:
  client_id, client_secret, refresh_token

Legacy three-secret variables are still accepted for backward compatibility.
"""

import json
import os

import google.auth
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from run_drive_3_1_free_generic import main as generic_main

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
TOKEN_URI = "https://oauth2.googleapis.com/token"


def _oauth_parts():
    packed = os.getenv("GOOGLE_DRIVE_OAUTH_JSON", "").strip()
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


def adc_token():
    credentials, _ = google.auth.default(scopes=[DRIVE_SCOPE])
    if hasattr(credentials, "with_scopes"):
        try:
            credentials = credentials.with_scopes([DRIVE_SCOPE])
        except (AttributeError, TypeError):
            pass
    credentials.refresh(Request())
    if not credentials.token:
        raise RuntimeError("BLOCKED_ACCESS: Workload Identity ADC token unavailable")
    return credentials.token


def drive_token():
    # User OAuth is required for creating files owned by the user in My Drive.
    # ADC remains a safe fallback for read-only diagnostics before OAuth setup.
    return user_oauth_token() or adc_token()


if __name__ == "__main__":
    generic_main(drive_token)
