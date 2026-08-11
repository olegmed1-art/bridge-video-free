#!/usr/bin/env python3
"""Authentication adapter for Bridge 3.1 FREE.

GitHub Workload Identity Federation remains the keyless default identity for
Google Cloud.  For Google Drive file creation in a user's My Drive, the runner
can instead use a user OAuth refresh token supplied through GitHub Actions
secrets.  This avoids service-account storage-quota/ownership limitations.
"""

import os

import google.auth
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

import run_drive_3_1_free as runner

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
TOKEN_URI = "https://oauth2.googleapis.com/token"


def user_oauth_token():
    client_id = os.getenv("GOOGLE_DRIVE_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_DRIVE_OAUTH_CLIENT_SECRET", "").strip()
    refresh_token = os.getenv("GOOGLE_DRIVE_OAUTH_REFRESH_TOKEN", "").strip()

    present = [bool(client_id), bool(client_secret), bool(refresh_token)]
    if any(present) and not all(present):
        raise RuntimeError("BLOCKED_ACCESS: incomplete Google Drive OAuth secrets")
    if not all(present):
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


runner.token = drive_token

if __name__ == "__main__":
    runner.main()
