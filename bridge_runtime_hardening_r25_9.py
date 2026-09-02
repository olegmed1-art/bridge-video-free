#!/usr/bin/env python3
"""Bridge Video 3.1 FREE r25.9 — revision-aware idempotency."""
from __future__ import annotations

import json
import os
import time

import bridge_runtime_hardening_r25_8 as stable
import bridge_worker_3_1_free as core
import run_master_3_1_free as base
import run_master_3_1_free_semantic as semantic

REVISION = "3.1-free-r25.9"


def install(token_func):
    requested = os.getenv("BRIDGE_REQUESTED_ALGORITHM_REVISION", "").strip()
    if requested and requested != REVISION:
        raise RuntimeError(
            f"ALGORITHM_REVISION_MISMATCH: requested={requested} executing={REVISION}"
        )

    had_requested = "BRIDGE_REQUESTED_ALGORITHM_REVISION" in os.environ
    saved_requested = os.environ.get("BRIDGE_REQUESTED_ALGORITHM_REVISION")
    os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = stable.REVISION
    try:
        stable.install(token_func)
    finally:
        if had_requested:
            os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = saved_requested or ""
        else:
            os.environ.pop("BRIDGE_REQUESTED_ALGORITHM_REVISION", None)

    core.ALGORITHM_REVISION = REVISION
    base.ALGORITHM_REVISION = REVISION


def receipt_matches_revision(payload: dict, job_id: str, revision: str = REVISION) -> bool:
    return (
        payload.get("status") == "CLEANUP_ACK"
        and payload.get("job_id") == job_id
        and payload.get("algorithmRevision") == revision
    )


def _already_completed_for_revision(token: str, job_id: str) -> bool:
    name = f"CLEANUP_ACK_{job_id}.json"
    escaped = name.replace("'", "\\'")
    candidates = base.io.search(token, f"trashed=false and name='{escaped}'")
    candidates.sort(key=lambda item: item.get("modifiedTime") or "", reverse=True)
    for candidate in candidates:
        try:
            payload = json.loads(base._read_text(token, candidate))
        except Exception:
            continue
        if receipt_matches_revision(payload, job_id):
            return True
    return False


def _knowledge_status(result: dict, applied: bool) -> dict:
    pdf = result.get("masterPdf") or {}
    return {
        "schema": "bridge-video-knowledge-status",
        "status": "KNOWLEDGE_APPLIED" if applied else "KNOWLEDGE_NOT_APPLIED",
        "job_id": result.get("job_id"),
        "algorithmRevision": REVISION,
        "masterPdfSha256": pdf.get("sha256"),
        "reason": None if applied else "DATABASE_NOT_CONFIGURED_OR_PERSISTENCE_RETURNED_NO_RESULT",
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }



def _latest_done_for_revision(token: str, job_id: str) -> dict | None:
    name = f"AI_DONE_{job_id}.json"
    escaped = name.replace("'", "\\'")
    candidates = base.io.search(token, f"trashed=false and name='{escaped}'")
    candidates.sort(key=lambda item: item.get("modifiedTime") or "", reverse=True)
    for candidate in candidates:
        try:
            payload = json.loads(base._read_text(token, candidate))
        except Exception:
            continue
        if (
            payload.get("status") == "AI_DONE"
            and payload.get("job_id") == job_id
            and payload.get("algorithmRevision") == REVISION
        ):
            return payload
    return None


def _publish_knowledge_status(token_func, result: dict) -> dict:
    parent = ((result.get("original") or {}).get("parentFolderId") or "").strip()
    if not parent:
        raise RuntimeError("KNOWLEDGE_STATUS_PARENT_MISSING")
    receipt = _knowledge_status(result, stable._PERSISTENCE_STATE["applied"])
    base.io.upload_json(
        token_func(),
        parent,
        f"KNOWLEDGE_STATUS_{result['job_id']}.json",
        receipt,
    )
    base.io.safe(
        job_id=result.get("job_id"),
        stage=receipt["status"],
        exit_code=0,
    )
    return receipt


def run(token_func):
    install(token_func)
    token = token_func()
    job_id = os.environ["BRIDGE_JOB_ID"]
    if _already_completed_for_revision(token, job_id):
        result = _latest_done_for_revision(token, job_id)
        if result is None:
            raise RuntimeError("AI_DONE_MISSING_FOR_COMPLETED_REVISION")
        # CLEANUP_ACK proves Drive cleanup only. Reconcile database persistence and
        # publish the explicit knowledge receipt before declaring terminal success.
        semantic.persist_completed_drive_job(token)
        _publish_knowledge_status(token_func, result)
        base.io.safe(
            job_id=job_id,
            stage="ALREADY_COMPLETED",
            exit_code=0,
            terminal_receipt="CLEANUP_ACK+KNOWLEDGE_STATUS",
            algorithm_revision=REVISION,
        )
        return result

    result = semantic.process_job(token)
    if not isinstance(result, dict):
        return result
    _publish_knowledge_status(token_func, result)
    return result
