#!/usr/bin/env python3
"""OIDC/ADC adapter for the existing Bridge 3.1 FREE Drive runner.

The google-github-actions/auth action creates Application Default Credentials.
This adapter replaces only the legacy service-account-key token function and
then executes the existing, previously tested processing pipeline unchanged.
"""

import google.auth
from google.auth.transport.requests import Request
import run_drive_3_1_free as runner


def adc_token():
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    if hasattr(credentials, "with_scopes"):
        try:
            credentials = credentials.with_scopes(
                ["https://www.googleapis.com/auth/drive"]
            )
        except (AttributeError, TypeError):
            pass
    credentials.refresh(Request())
    if not credentials.token:
        raise RuntimeError("BLOCKED_ACCESS: Workload Identity ADC token unavailable")
    return credentials.token


runner.token = adc_token

if __name__ == "__main__":
    runner.main()
