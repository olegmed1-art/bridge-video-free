#!/usr/bin/env python3
"""Durable, evidence-preserving transcript-stage checkpoint for Bridge Video.

This adapter wraps the already-installed transcript+diarization function.  It
reuses only an exact checkpoint for the same job, algorithm revision, Drive
source identity, local source SHA256 and duration.  Invalid/stale checkpoints
are ignored and the proven pipeline is recomputed.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Callable

_INSTALLED = False


def _checkpoint_name(job_id: str, revision: str) -> str:
    safe_revision = "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in revision)
    return f"TRANSCRIPT_STAGE_CHECKPOINT_{job_id}_{safe_revision}.json"


def _read_json(base, token: str, item: dict[str, Any]) -> dict[str, Any] | None:
    try:
        value = json.loads(base._read_text(token, item))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _valid_segments(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    prior_start = -1.0
    for item in value:
        if not isinstance(item, dict):
            return False
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError):
            return False
        if start < 0 or end < start or start + 1e-6 < prior_start:
            return False
        if not str(item.get("text") or "").strip():
            return False
        prior_start = start
    return True


def _load_checkpoint(base, token, video: Path, duration: float, job_id: str, revision: str):
    folder_id = os.getenv("BRIDGE_WORK_FOLDER_ID", "").strip()
    if not folder_id:
        return None
    name = _checkpoint_name(job_id, revision)
    candidates = base.io.search(
        token,
        f"'{folder_id}' in parents and trashed=false and name='{name}'",
    )
    candidates.sort(key=lambda item: item.get("modifiedTime") or "", reverse=True)
    if not candidates:
        return None

    source_drive_id = os.getenv("BRIDGE_ORIGINAL_SOURCE_DRIVE_ID", "").strip()
    source_sha = base.io.sha(video).lower()
    for item in candidates:
        payload = _read_json(base, token, item)
        if not payload:
            continue
        if payload.get("schema") != "bridge-video-transcript-stage-checkpoint-v1":
            continue
        if payload.get("status") != "TRANSCRIPT_STAGE_COMPLETE":
            continue
        if str(payload.get("job_id") or "") != job_id:
            continue
        if str(payload.get("algorithmRevision") or "") != revision:
            continue
        recorded_source_id = str(payload.get("sourceDriveId") or "")
        if source_drive_id and recorded_source_id and recorded_source_id != source_drive_id:
            continue
        if str(payload.get("sourceSha256") or "").lower() != source_sha:
            continue
        try:
            recorded_duration = float(payload.get("durationSeconds"))
        except (TypeError, ValueError):
            continue
        if abs(recorded_duration - float(duration)) > 0.05:
            continue
        segments = payload.get("segments")
        info = payload.get("info")
        warnings = payload.get("warnings")
        if not _valid_segments(segments) or not isinstance(info, dict) or not isinstance(warnings, list):
            continue
        result_info = copy.deepcopy(info)
        result_info["transcriptStageCheckpoint"] = {
            "status": "REUSED",
            "driveId": item.get("id"),
            "schema": payload.get("schema"),
        }
        return copy.deepcopy(segments), result_info, list(warnings), item
    return None


def _save_checkpoint(base, token, video: Path, duration: float, job_id: str, revision: str, segments, info, warnings):
    folder_id = os.getenv("BRIDGE_WORK_FOLDER_ID", "").strip()
    if not folder_id:
        return None
    name = _checkpoint_name(job_id, revision)
    payload = {
        "schema": "bridge-video-transcript-stage-checkpoint-v1",
        "status": "TRANSCRIPT_STAGE_COMPLETE",
        "job_id": job_id,
        "algorithmRevision": revision,
        "sourceDriveId": os.getenv("BRIDGE_ORIGINAL_SOURCE_DRIVE_ID") or None,
        "sourceSha256": base.io.sha(video).lower(),
        "durationSeconds": float(duration),
        "segments": segments,
        "info": info,
        "warnings": list(warnings or []),
        "technicalRecordOnly": True,
        "publicationAllowed": False,
        "canonWriteAllowed": False,
        "studentProfileWriteAllowed": False,
    }
    return base.io.upload_json(token, folder_id, name, payload)


def install(base, revision: str) -> Callable:
    """Wrap the currently installed transcript path exactly once."""
    global _INSTALLED
    if _INSTALLED:
        return base.obtain_transcript
    previous = base.obtain_transcript

    def checkpointed_obtain(token, parent, name, video, work, duration, job_id):
        loaded = _load_checkpoint(base, token, Path(video), float(duration), str(job_id), revision)
        if loaded is not None:
            segments, info, warnings, item = loaded
            base.io.safe(
                job_id=job_id,
                stage="TRANSCRIPT_STAGE_CHECKPOINT_REUSED",
                exit_code=0,
                checkpoint_drive_id=item.get("id"),
                transcript_segments=len(segments),
            )
            return segments, info, warnings

        segments, info, warnings = previous(
            token, parent, name, video, work, duration, job_id
        )
        if not _valid_segments(segments):
            # Never persist an incomplete/invalid transcript as resumable evidence.
            return segments, info, warnings
        receipt = _save_checkpoint(
            base,
            token,
            Path(video),
            float(duration),
            str(job_id),
            revision,
            segments,
            info,
            warnings,
        )
        if receipt:
            info = dict(info)
            info["transcriptStageCheckpoint"] = {
                "status": "CREATED",
                "driveId": receipt.get("id"),
                "schema": "bridge-video-transcript-stage-checkpoint-v1",
            }
            base.io.safe(
                job_id=job_id,
                stage="TRANSCRIPT_STAGE_CHECKPOINT_SAVED",
                exit_code=0,
                checkpoint_drive_id=receipt.get("id"),
                transcript_segments=len(segments),
            )
        return segments, info, warnings

    base.obtain_transcript = checkpointed_obtain
    _INSTALLED = True
    return checkpointed_obtain
