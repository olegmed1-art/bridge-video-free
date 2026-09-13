#!/usr/bin/env python3
"""Backfill completed Bridge Video AI_DONE results from Google Drive into Neon.

No media is reprocessed. The script reuses AI_DONE metadata and the embedded
master_analysis.json, then calls the same transactional/idempotent persistence
path used by newly completed video jobs.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path

import run_drive_3_1_free as io
from bridge_worker_3_1_free import stable_job_id
from run_drive_3_1_free_oidc import user_oauth_token

from bridge_neon_persistence import _load_embedded_master
from database.outbox_publisher import publish_changeset_outbox
from database.runtime_worker_preflight import normalize_dsn
from database.video_result_persistence import persist_video_result

JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _load_done(token: str, item: dict) -> dict | None:
    name = str(item.get("name") or "")
    if not name.startswith("AI_DONE_") or not name.endswith(".json"):
        return None
    with tempfile.TemporaryDirectory(prefix="bridge-backfill-done-") as td:
        path = Path(td) / "done.json"
        io.download(token, item["id"], path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
    job_id = str(payload.get("job_id") or "")
    if payload.get("status") != "AI_DONE" or not JOB_ID_RE.fullmatch(job_id):
        return None
    if not (payload.get("masterPdf") or {}).get("driveId"):
        return None
    return payload


def discover_done(token: str) -> list[tuple[dict, dict]]:
    candidates = io.search(token, "trashed=false and name contains 'AI_DONE_'")
    loaded: dict[str, tuple[dict, dict]] = {}
    for item in candidates:
        try:
            done = _load_done(token, item)
        except Exception:
            continue
        if not done:
            continue
        job_id = str(done["job_id"])
        current = loaded.get(job_id)
        if current is None or str(item.get("modifiedTime") or "") > str(current[0].get("modifiedTime") or ""):
            loaded[job_id] = (item, done)
    return sorted(loaded.values(), key=lambda pair: str(pair[0].get("modifiedTime") or ""))


def _verify_master_identity(master: dict, job_id: str) -> None:
    if str(master.get("job_id") or "") != job_id:
        raise RuntimeError("JOB_ID_MISMATCH")
    source = master.get("source") or {}
    source_drive_id = str(source.get("driveId") or "").strip()
    if not source_drive_id:
        raise RuntimeError("SOURCE_DRIVE_ID_MISSING")
    if stable_job_id("drive", source_drive_id) != job_id:
        raise RuntimeError("SOURCE_JOB_ID_MISMATCH")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="0 means all discovered completed jobs")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be a non-negative integer")

    raw_dsn = os.getenv("BRIDGE_WORKER_DATABASE_URL", "").strip()
    if not normalize_dsn(raw_dsn):
        raise SystemExit("BACKFILL_DB_SECRET_MISSING")
    token = user_oauth_token()
    if not token:
        raise SystemExit("BACKFILL_DRIVE_OAUTH_MISSING")

    discovered = discover_done(token)
    selected = discovered[: args.limit] if args.limit > 0 else discovered
    ok = 0
    failed = 0
    errors: list[dict[str, str]] = []

    for _, done in selected:
        job_id = str(done.get("job_id") or "")
        try:
            master = _load_embedded_master(token, done)
            _verify_master_identity(master, job_id)
            result = persist_video_result(raw_dsn, master, done)
            publish_changeset_outbox(raw_dsn, str(result["changeset_id"]))
            ok += 1
            print(json.dumps({"stage": "BACKFILL_ITEM", "job_id": job_id, "status": "ok"}))
        except Exception as exc:
            failed += 1
            errors.append({"job_id": job_id, "error": type(exc).__name__})
            print(json.dumps({"stage": "BACKFILL_ITEM", "job_id": job_id, "status": "failed", "error": type(exc).__name__}))

    summary = {
        "stage": "BACKFILL_SUMMARY",
        "discovered": len(discovered),
        "selected": len(selected),
        "persisted": ok,
        "failed": failed,
        "errors": errors[:20],
    }
    print(json.dumps(summary, ensure_ascii=False))
    if not selected:
        raise SystemExit("BACKFILL_NO_ELIGIBLE_AI_DONE")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()