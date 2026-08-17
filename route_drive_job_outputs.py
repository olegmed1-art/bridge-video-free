#!/usr/bin/env python3
"""Move derived Bridge Video outputs to an explicit Drive result folder.

The source/master video is never renamed, moved, modified, copied, or deleted.
This post-step is idempotent and acts only on output IDs proved by AI_DONE.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import requests

import run_drive_3_1_free as io
from run_drive_3_1_free_oidc import user_oauth_token

DRIVE = "https://www.googleapis.com/drive/v3"


def _read_json(token: str, item: dict) -> dict | None:
    with tempfile.TemporaryDirectory(prefix="bridge-route-") as td:
        path = Path(td) / "payload.json"
        io.download(token, item["id"], path)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None


def _latest_named(token: str, name: str) -> tuple[dict, dict] | None:
    items = io.search(token, f"trashed=false and name='{name}'")
    items.sort(key=lambda item: item.get("modifiedTime") or "", reverse=True)
    for item in items:
        payload = _read_json(token, item)
        if payload is not None:
            return item, payload
    return None


def _move(token: str, file_id: str, target_parent: str) -> str:
    meta = io.meta(token, file_id)
    parents = [str(value) for value in (meta.get("parents") or []) if value]
    if parents == [target_parent] or (target_parent in parents and len(parents) == 1):
        return "already_routed"
    params = {
        "addParents": target_parent,
        "removeParents": ",".join(parent for parent in parents if parent != target_parent),
        "fields": "id,name,parents",
        "supportsAllDrives": "true",
    }
    response = requests.patch(
        f"{DRIVE}/files/{file_id}",
        headers={**io.hdr(token), "Content-Type": "application/json"},
        params=params,
        json={},
        timeout=60,
    )
    response.raise_for_status()
    routed = response.json()
    if target_parent not in (routed.get("parents") or []):
        raise RuntimeError("OUTPUT_ROUTE_READBACK_FAILED")
    return "moved"


def main() -> int:
    job_id = os.environ.get("BRIDGE_JOB_ID", "").strip()
    target = os.environ.get("BRIDGE_OUTPUT_FOLDER_ID", "").strip()
    if not job_id:
        raise RuntimeError("BRIDGE_JOB_ID_REQUIRED")
    if not target:
        print(json.dumps({"stage": "OUTPUT_ROUTE", "job_id": job_id, "status": "NO_TARGET_CONFIGURED"}))
        return 0

    token = user_oauth_token()
    if not token:
        raise RuntimeError("BLOCKED_ACCESS: Drive OAuth unavailable")

    done_found = _latest_named(token, f"AI_DONE_{job_id}.json")
    if done_found is None:
        print(json.dumps({"stage": "OUTPUT_ROUTE", "job_id": job_id, "status": "AI_DONE_NOT_FOUND"}))
        return 0
    done_item, done = done_found
    if done.get("status") != "AI_DONE" or done.get("job_id") != job_id:
        raise RuntimeError("OUTPUT_ROUTE_AI_DONE_IDENTITY_MISMATCH")

    source_id = str((done.get("original") or {}).get("driveId") or "")
    report_id = str((done.get("masterPdf") or {}).get("driveId") or "")
    if not source_id or not report_id:
        raise RuntimeError("OUTPUT_ROUTE_METADATA_INCOMPLETE")

    file_ids: list[tuple[str, str]] = [("master_pdf", report_id), ("ai_done", done_item["id"])]
    for label, name, expected_status in (
        ("methodology_ready", f"METHODOLOGY_READY_{job_id}.json", "METHODOLOGY_READY"),
        ("cleanup_ack", f"CLEANUP_ACK_{job_id}.json", "CLEANUP_ACK"),
    ):
        found = _latest_named(token, name)
        if found is None:
            continue
        item, payload = found
        if payload.get("job_id") == job_id and payload.get("status") == expected_status:
            file_ids.append((label, item["id"]))

    results = []
    for label, file_id in file_ids:
        if file_id == source_id:
            raise RuntimeError("OUTPUT_ROUTE_REFUSED_TO_MOVE_SOURCE")
        results.append({"kind": label, "file_id": file_id, "result": _move(token, file_id, target)})

    # Final source read-back proves the master itself was not moved by this operation.
    source = io.meta(token, source_id)
    if target in (source.get("parents") or []):
        raise RuntimeError("OUTPUT_ROUTE_SOURCE_IN_RESULT_FOLDER")

    print(json.dumps({
        "stage": "OUTPUT_ROUTE",
        "job_id": job_id,
        "status": "ROUTED",
        "target_folder_id": target,
        "source_untouched": True,
        "results": results,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
