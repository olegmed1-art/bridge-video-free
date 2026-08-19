#!/usr/bin/env python3
"""Post-process one completed Bridge Video Drive job into Neon."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

import run_drive_3_1_free as io

from database.outbox_publisher import publish_changeset_outbox
from database.video_result_persistence import persist_video_result


def _load_done(token: str, job_id: str) -> dict:
    name = f"AI_DONE_{job_id}.json"
    candidates = io.search(token, f"trashed=false and name='{name}'")
    candidates.sort(key=lambda item: item.get("modifiedTime") or "", reverse=True)
    for candidate in candidates:
        with tempfile.TemporaryDirectory(prefix="bridge-db-done-") as td:
            path = Path(td) / name
            io.download(token, candidate["id"], path)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if payload.get("job_id") == job_id and payload.get("status") == "AI_DONE":
                return payload
    raise RuntimeError("DATABASE_PERSIST_DONE_NOT_FOUND")


def _load_embedded_master(token: str, done: dict) -> dict:
    import fitz

    pdf_meta = done.get("masterPdf") or {}
    pdf_id = str(pdf_meta.get("driveId") or "")
    expected_pdf_sha = str(pdf_meta.get("sha256") or "").lower()
    expected_master_sha = str(pdf_meta.get("masterJsonSha256") or "").lower()
    if not pdf_id or not expected_pdf_sha:
        raise RuntimeError("DATABASE_PERSIST_MASTER_PDF_METADATA_MISSING")

    with tempfile.TemporaryDirectory(prefix="bridge-db-master-") as td:
        pdf_path = Path(td) / "master.pdf"
        io.download(token, pdf_id, pdf_path)
        if io.sha(pdf_path).lower() != expected_pdf_sha:
            raise RuntimeError("DATABASE_PERSIST_MASTER_PDF_SHA_MISMATCH")

        doc = fitz.open(pdf_path)
        try:
            names = set(doc.embfile_names()) if hasattr(doc, "embfile_names") else set()
            if "master_analysis.json" not in names:
                raise RuntimeError("DATABASE_PERSIST_MASTER_JSON_NOT_EMBEDDED")
            raw = doc.embfile_get("master_analysis.json")
        finally:
            doc.close()

    if expected_master_sha and hashlib.sha256(raw).hexdigest().lower() != expected_master_sha:
        raise RuntimeError("DATABASE_PERSIST_MASTER_JSON_SHA_MISMATCH")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("DATABASE_PERSIST_MASTER_JSON_INVALID") from exc


def persist_completed_drive_job(token: str):
    raw_dsn = os.getenv("BRIDGE_WORKER_DATABASE_URL", "").strip()
    if not raw_dsn:
        io.safe(job_id=os.getenv("BRIDGE_JOB_ID", ""), stage="DATABASE_PERSIST_SKIPPED", exit_code=0)
        return None

    job_id = os.environ["BRIDGE_JOB_ID"]
    done = _load_done(token, job_id)
    master = _load_embedded_master(token, done)
    if master.get("job_id") != job_id:
        raise RuntimeError("DATABASE_PERSIST_JOB_ID_MISMATCH")

    result = persist_video_result(raw_dsn, master, done)
    outbox = publish_changeset_outbox(raw_dsn, str(result["changeset_id"]))
    result["outbox"] = outbox
    io.safe(
        job_id=job_id,
        stage="DATABASE_PERSIST",
        exit_code=0,
        episode_count=len(master.get("episodes") or []),
        outbox_published=outbox["published_count"],
    )
    return result