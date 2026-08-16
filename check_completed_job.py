#!/usr/bin/env python3
"""Fast revision-aware Drive check for terminal CLEANUP_ACK receipts."""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_API = "https://www.googleapis.com/drive/v3"


def _json_request(url, *, method="GET", headers=None, data=None):
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    with urllib.request.urlopen(req, timeout=60) as response:
        raw = response.read()
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
    if not all(form[key] for key in ("client_id", "client_secret", "refresh_token")):
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


def receipt_matches_revision(payload: dict, job_id: str, revision: str) -> bool:
    return (
        payload.get("status") == "CLEANUP_ACK"
        and payload.get("job_id") == job_id
        and payload.get("algorithmRevision") == revision
    )


def _download_receipt(token: str, file_id: str) -> dict:
    params = urllib.parse.urlencode({"alt": "media", "supportsAllDrives": "true"})
    return _json_request(
        f"{DRIVE_API}/files/{urllib.parse.quote(file_id, safe='')}?{params}",
        headers={"Authorization": f"Bearer {token}"},
    )


def knowledge_status_matches_revision(payload: dict, job_id: str, revision: str) -> bool:
    return (
        payload.get("status") == "KNOWLEDGE_APPLIED"
        and payload.get("job_id") == job_id
        and payload.get("algorithmRevision") == revision
    )


def _matching_named_receipt(token: str, name: str, matcher, job: str, revision: str) -> bool:
    escaped = name.replace("'", "\\'")
    q = f"trashed=false and name='{escaped}'"
    params = urllib.parse.urlencode({
        "q": q,
        "fields": "files(id,name,modifiedTime)",
        "pageSize": "100",
        "orderBy": "modifiedTime desc",
    })
    out = _json_request(
        DRIVE_API + "/files?" + params,
        headers={"Authorization": f"Bearer {token}"},
    )
    for item in out.get("files") or []:
        try:
            payload = _download_receipt(token, item["id"])
        except Exception:
            continue
        if matcher(payload, job, revision):
            return True
    return False


def main():
    job = os.environ["BRIDGE_JOB_ID"]
    revision = os.environ.get("BRIDGE_REQUESTED_ALGORITHM_REVISION", "").strip()
    if not revision:
        raise RuntimeError("ALGORITHM_REVISION_REQUIRED_FOR_IDEMPOTENCY")
    token = _token()
    cleanup_ready = _matching_named_receipt(
        token, f"CLEANUP_ACK_{job}.json", receipt_matches_revision, job, revision
    )
    knowledge_ready = cleanup_ready and _matching_named_receipt(
        token,
        f"KNOWLEDGE_STATUS_{job}.json",
        knowledge_status_matches_revision,
        job,
        revision,
    )
    completed = cleanup_ready and knowledge_ready

    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"already_completed={'true' if completed else 'false'}\n")
    print(json.dumps({
        "stage": "TERMINAL_RECEIPT_PREFLIGHT",
        "status": "ALREADY_COMPLETED" if completed else "TERMINAL_RECEIPTS_INCOMPLETE",
        "cleanupReady": cleanup_ready,
        "knowledgeReady": knowledge_ready,
        "job_id": job,
        "algorithmRevision": revision,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
