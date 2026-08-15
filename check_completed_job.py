#!/usr/bin/env python3
"""Fast zero-dependency Drive check for terminal CLEANUP_ACK receipt.

Runs before installing the heavy media/ASR environment. Prints GitHub output
already_completed=true/false without exposing OAuth data or user content.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_API = "https://www.googleapis.com/drive/v3"


def _json_request(url, *, method="GET", headers=None, data=None):
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        return json.loads(raw.decode("utf-8")) if raw else {}


def _token():
    packed = os.environ.get("GOOGLE_DRIVE_OAUTH_JSON", "").strip()
    if not packed:
        raise RuntimeError("BLOCKED_ACCESS: GOOGLE_DRIVE_OAUTH_JSON missing")
    cfg = json.loads(packed)
    form = {
        "client_id": cfg.get("client_id", ""),
        "client_secret": cfg.get("client_secret", ""),
        "refresh_token": cfg.get("refresh_token", ""),
        "grant_type": "refresh_token",
    }
    if not all(form[k] for k in ("client_id", "client_secret", "refresh_token")):
        raise RuntimeError("BLOCKED_ACCESS: incomplete Drive OAuth secret")
    payload = _json_request(
        TOKEN_URL,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=urllib.parse.urlencode(form).encode(),
    )
    token = payload.get("access_token")
    if not token:
        raise RuntimeError("BLOCKED_ACCESS: OAuth refresh failed")
    return token


def main():
    job = os.environ["BRIDGE_JOB_ID"]
    token = _token()
    name = f"CLEANUP_ACK_{job}.json"
    q = f"trashed=false and name='{name}'"
    params = urllib.parse.urlencode({"q": q, "fields": "files(id,name)", "pageSize": "1"})
    out = _json_request(
        DRIVE_API + "/files?" + params,
        headers={"Authorization": f"Bearer {token}"},
    )
    completed = bool(out.get("files"))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as f:
            f.write(f"already_completed={'true' if completed else 'false'}\n")
    print(json.dumps({
        "stage": "TERMINAL_RECEIPT_PREFLIGHT",
        "status": "ALREADY_COMPLETED" if completed else "NOT_COMPLETED",
        "job_id": job,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
