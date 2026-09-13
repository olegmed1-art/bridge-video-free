#!/usr/bin/env python3
"""Zero-cost scheduled discovery for Bridge Video 3.1 FREE.

Uses only Python stdlib + Google Drive REST with the existing user OAuth secret.
Finds the oldest unprocessed video created after AUTO_DISCOVERY_NOT_BEFORE,
writes a Drive dispatch marker to avoid duplicate runs, and dispatches the
existing GitHub Actions worker via workflow_dispatch.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request

DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ZOOM_ROOT_ID = os.getenv("BRIDGE_ZOOM_ROOT_ID", "1W-5gfOFUrSJ0a7XN0zXjU8o8JTUcorP1")
NOT_BEFORE = os.getenv("AUTO_DISCOVERY_NOT_BEFORE", "2026-08-15T11:30:00Z")
STALE_HOURS = float(os.getenv("AUTO_QUEUE_STALE_HOURS", "8"))
WORKFLOW = "bridge-video-3.1-free.yml"


def _json_request(url, *, method="GET", headers=None, data=None):
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        return json.loads(raw.decode("utf-8")) if raw else {}


def _oauth_token():
    packed = os.environ.get("GOOGLE_DRIVE_OAUTH_JSON", "").strip()
    if not packed:
        raise RuntimeError("BLOCKED_ACCESS: GOOGLE_DRIVE_OAUTH_JSON missing")
    cfg = json.loads(packed)
    fields = {
        "client_id": cfg.get("client_id", ""),
        "client_secret": cfg.get("client_secret", ""),
        "refresh_token": cfg.get("refresh_token", ""),
        "grant_type": "refresh_token",
    }
    if not all(fields[k] for k in ("client_id", "client_secret", "refresh_token")):
        raise RuntimeError("BLOCKED_ACCESS: incomplete Drive OAuth secret")
    data = urllib.parse.urlencode(fields).encode()
    out = _json_request(TOKEN_URL, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"}, data=data)
    token = out.get("access_token")
    if not token:
        raise RuntimeError("BLOCKED_ACCESS: Drive OAuth refresh failed")
    return token


def _drive_headers(token):
    return {"Authorization": f"Bearer {token}"}


def _list_children(token, parent_id):
    out = []
    page = None
    while True:
        q = f"'{parent_id}' in parents and trashed=false"
        params = {
            "q": q,
            "fields": "nextPageToken,files(id,name,mimeType,size,createdTime,modifiedTime,parents)",
            "pageSize": "1000",
            "orderBy": "createdTime",
        }
        if page:
            params["pageToken"] = page
        url = DRIVE_API + "/files?" + urllib.parse.urlencode(params)
        payload = _json_request(url, headers=_drive_headers(token))
        out.extend(payload.get("files") or [])
        page = payload.get("nextPageToken")
        if not page:
            return out


def _walk_videos(token, root_id):
    stack = [root_id]
    seen = set()
    videos = []
    while stack:
        parent = stack.pop()
        if parent in seen:
            continue
        seen.add(parent)
        for f in _list_children(token, parent):
            if f.get("mimeType") == "application/vnd.google-apps.folder":
                stack.append(f["id"])
            elif str(f.get("mimeType") or "").startswith("video/"):
                f["parent_id"] = parent
                videos.append(f)
    return videos


def _job_id(file_id):
    return hashlib.sha256(f"bridge-video|drive|{file_id}".encode()).hexdigest()[:32]


def _parse_time(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _marker_name(job):
    return f"AUTO_QUEUED_{job}.json"


def _ack_name(job):
    return f"CLEANUP_ACK_{job}.json"


def _find_named(token, parent, names):
    wanted = set(names)
    return [f for f in _list_children(token, parent) if f.get("name") in wanted]


def _upload_marker(token, parent, job, source):
    name = _marker_name(job)
    body = json.dumps({
        "schema": "bridge-video-auto-queue",
        "job_id": job,
        "source_drive_id": source["id"],
        "source_name": source.get("name"),
        "status": "DISPATCHED",
        "at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "zero_cost_only": True,
    }, ensure_ascii=False).encode("utf-8")
    boundary = "bridgefreeboundary"
    metadata = json.dumps({"name": name, "parents": [parent]}, ensure_ascii=False).encode("utf-8")
    payload = (
        b"--" + boundary.encode() + b"\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n" + metadata +
        b"\r\n--" + boundary.encode() + b"\r\nContent-Type: application/json\r\n\r\n" + body +
        b"\r\n--" + boundary.encode() + b"--\r\n"
    )
    url = DRIVE_UPLOAD + "/files?uploadType=multipart&fields=id,name,createdTime"
    return _json_request(url, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": f"multipart/related; boundary={boundary}",
    }, data=payload)


def _dispatch(job):
    repo = os.environ["GITHUB_REPOSITORY"]
    gh_token = os.environ["GITHUB_TOKEN"]
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{WORKFLOW}/dispatches"
    data = json.dumps({"ref": "main", "inputs": {"job_id": job}}).encode()
    req = urllib.request.Request(url, method="POST", data=data, headers={
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        if r.status != 204:
            raise RuntimeError(f"GitHub dispatch failed HTTP {r.status}")


def main():
    token = _oauth_token()
    cutoff = _parse_time(NOT_BEFORE)
    now = dt.datetime.now(dt.timezone.utc)
    candidates = []
    for f in _walk_videos(token, ZOOM_ROOT_ID):
        created = _parse_time(f.get("createdTime") or "1970-01-01T00:00:00Z")
        if created < cutoff:
            continue
        job = _job_id(f["id"])
        names = [_ack_name(job), _marker_name(job)]
        receipts = _find_named(token, f["parent_id"], names)
        ack = next((x for x in receipts if x.get("name") == _ack_name(job)), None)
        if ack:
            continue
        marker = next((x for x in receipts if x.get("name") == _marker_name(job)), None)
        if marker:
            mt = _parse_time(marker.get("createdTime") or marker.get("modifiedTime") or "1970-01-01T00:00:00Z")
            if (now - mt).total_seconds() < STALE_HOURS * 3600:
                continue
        candidates.append((created, f, job))

    if not candidates:
        print(json.dumps({"stage": "AUTO_DISCOVERY", "status": "NO_NEW_VIDEO", "zero_cost_only": True}))
        return 0

    created, source, job = sorted(candidates, key=lambda x: x[0])[0]
    _upload_marker(token, source["parent_id"], job, source)
    _dispatch(job)
    print(json.dumps({
        "stage": "AUTO_DISCOVERY",
        "status": "DISPATCHED",
        "job_id": job,
        "createdTime": source.get("createdTime"),
        "zero_cost_only": True,
    }))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(json.dumps({"stage": "AUTO_DISCOVERY", "status": "ERROR", "error": str(e)[:300]}), file=sys.stderr)
        sys.exit(1)
