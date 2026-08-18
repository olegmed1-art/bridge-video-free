#!/usr/bin/env python3
"""Quality-first production adapter for Bridge Video 3.1 FREE r25.7.

The heavy media/ASR implementation remains the proven r25.6 path.  This adapter
adds local speaker diarization, a strict three-stage readiness model and a v2
pedagogical layer.  A technically successful job may now correctly finish as
``METHODOLOGY_PARTIAL`` instead of producing a false methodology-ready receipt.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import bridge_worker_3_1_free as core
import run_master_3_1_free as base
import run_master_3_1_free_semantic as previous
from bridge_neon_persistence import persist_completed_drive_job
from bridge_speaker_diarization import diarize_transcript
from diana_longitudinal_quality_v2 import build_quality_layer

REVISION = "3.1-free-r25.7"
core.ALGORITHM_REVISION = REVISION
base.ALGORITHM_REVISION = REVISION

_previous_obtain_transcript = base.obtain_transcript
_previous_master_payload = base.master_analysis_payload
_previous_validate = base.validate_r24_master
_previous_upload_json = base.io.upload_json
_QUALITY_BY_JOB: dict[str, dict[str, Any]] = {}


def _enabled(name: str, default: bool = True) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().casefold()
    return raw in {"1", "true", "yes", "on"}


def obtain_transcript_with_local_diarization(t, parent, name, video, work, dur, job):
    segments, info, warnings = _previous_obtain_transcript(t, parent, name, video, work, dur, job)
    labeled = sum(bool(segment.get("speaker")) for segment in segments)
    if labeled < max(2, int(len(segments) * 0.5)):
        segments, diarization = diarize_transcript(
            video,
            segments,
            work,
            enabled=_enabled("BRIDGE_DIARIZATION_ENABLED", True),
        )
    else:
        _, diarization = diarize_transcript(video, segments, work, enabled=True)
    info = dict(info)
    info["speakerDiarization"] = diarization
    if diarization.get("status") not in {
        "DIARIZED_ROLE_MAPPED",
        "EXISTING_SPEAKER_LABELS_PRESERVED",
    }:
        warnings = list(warnings) + [
            "Speaker diarization did not prove teacher/student roles; actor-specific methodology remains partial."
        ]
    base.io.safe(
        job_id=job,
        stage="SPEAKER_DIARIZATION",
        exit_code=0,
        content_warning_count=0 if diarization.get("status") == "DIARIZED_ROLE_MAPPED" else 1,
    )
    return segments, info, warnings


def master_payload_with_quality_v2(**kwargs):
    master = _previous_master_payload(**kwargs)
    quality = build_quality_layer(master)
    master["longitudinal_quality_v2"] = quality
    content = master.setdefault("content_quality", {})
    readiness = quality.get("readiness") or {}
    counts = quality.get("counts") or {}
    content.update({
        "quality_v2_method": quality.get("method_version"),
        "methodology_readiness_v2": readiness.get("methodology_status"),
        "technical_readiness_v2": readiness.get("technical_status"),
        "content_readiness_v2": readiness.get("content_status"),
        "speaker_attribution_v2": (readiness.get("speaker_summary") or {}).get("status"),
        "complete_learning_interactions_v2": counts.get("complete_learning_interactions", 0),
        "partial_learning_interactions_v2": counts.get("partial_learning_interactions", 0),
        "strong_canon_evidence_candidates_v2": counts.get("strong_canon_evidence_candidates", 0),
        "promotable_knowledge_candidates_v2": counts.get("promotable_knowledge_candidates", 0),
        "active_reusable_asset_candidates_v2": counts.get("active_reusable_asset_candidates", 0),
        "verified_full_boards_v2": counts.get("verified_full_boards", 0),
    })
    master.setdefault("principles", {}).update({
        "technical_ready_does_not_imply_methodology_ready": True,
        "learning_interaction_requires_observed_task_and_action": True,
        "weak_canon_retrieval_is_not_canon_evidence": True,
        "knowledge_fragments_remain_staging": True,
        "dds_requires_verified_full_board": True,
        "retention_generalization_transfer_require_later_evidence": True,
        "two_stage_candidate_storage": True,
    })
    _QUALITY_BY_JOB[str(master.get("job_id") or "")] = quality
    return master


def validate_with_readiness_v2(master):
    result = dict(_previous_validate(master))
    quality = master.get("longitudinal_quality_v2") or build_quality_layer(master)
    readiness = quality.get("readiness") or {}
    # The old r24 gate remains a technical/content integrity gate.  Methodology
    # readiness is reported separately and never faked merely to finish a job.
    result.update({
        "technicalStatus": readiness.get("technical_status"),
        "contentStatus": readiness.get("content_status"),
        "methodologyStatus": readiness.get("methodology_status"),
        "methodologyIssues": readiness.get("methodology_issues") or [],
        "completeLearningInteractions": readiness.get("complete_learning_interactions", 0),
        "speakerSummary": readiness.get("speaker_summary") or {},
        "canonActivationAllowed": False,
        "studentProfileWriteAllowed": False,
        "curriculumActivationAllowed": False,
    })
    return result


def _receipt_payload(status: str, job_id: str, quality: dict[str, Any], master_pdf_id: str | None = None) -> dict[str, Any]:
    readiness = quality.get("readiness") or {}
    return {
        "schema": "bridge-video-readiness-v2",
        "status": status,
        "job_id": job_id,
        "algorithmVersion": core.ALGORITHM_VERSION,
        "algorithmRevision": REVISION,
        "masterPdfDriveId": master_pdf_id,
        "readiness": readiness,
        "counts": quality.get("counts") or {},
        "authority": quality.get("authority") or {},
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def upload_json_with_readiness_v2(token, parent, name, payload):
    """Route false methodology-ready receipts to a truthful partial receipt."""
    job_id = str(payload.get("job_id") or os.getenv("BRIDGE_JOB_ID", ""))
    quality = _QUALITY_BY_JOB.get(job_id) or {}
    readiness = quality.get("readiness") or {}

    if name.startswith("AI_DONE_"):
        augmented = dict(payload)
        augmented["readinessV2"] = readiness
        augmented["qualityV2Counts"] = quality.get("counts") or {}
        uploaded = _previous_upload_json(token, parent, name, augmented)
        pdf_id = ((augmented.get("masterPdf") or {}).get("driveId"))
        if readiness.get("technical_status") == "TECHNICAL_READY":
            _previous_upload_json(
                token,
                parent,
                f"TECHNICAL_READY_{job_id}.json",
                _receipt_payload("TECHNICAL_READY", job_id, quality, pdf_id),
            )
        if readiness.get("content_status") == "CONTENT_EXTRACTED":
            _previous_upload_json(
                token,
                parent,
                f"CONTENT_EXTRACTED_{job_id}.json",
                _receipt_payload("CONTENT_EXTRACTED", job_id, quality, pdf_id),
            )
        return uploaded

    if name.startswith("METHODOLOGY_READY_"):
        methodology = (
            ((payload.get("contentGate") or {}).get("methodologyStatus"))
            or readiness.get("methodology_status")
            or "METHODOLOGY_PARTIAL"
        )
        adjusted = dict(payload)
        adjusted["readinessV2"] = readiness
        if methodology != "METHODOLOGY_READY":
            name = name.replace("METHODOLOGY_READY_", "METHODOLOGY_PARTIAL_", 1)
            adjusted["status"] = "METHODOLOGY_PARTIAL"
            adjusted["methodologyIssues"] = readiness.get("methodology_issues") or []
        else:
            adjusted["status"] = "METHODOLOGY_READY"
        return _previous_upload_json(token, parent, name, adjusted)

    return _previous_upload_json(token, parent, name, payload)


base.obtain_transcript = obtain_transcript_with_local_diarization
base.master_analysis_payload = master_payload_with_quality_v2
base.validate_r24_master = validate_with_readiness_v2
base.io.upload_json = upload_json_with_readiness_v2


def _read_json(token, item):
    try:
        return json.loads(base._read_text(token, item))
    except Exception:
        return None


def _existing_same_revision_done(token, job_id):
    done_candidates = base.io.search(token, f"trashed=false and name='AI_DONE_{job_id}.json'")
    done_candidates.sort(key=lambda item: item.get("modifiedTime") or "", reverse=True)
    for candidate in done_candidates:
        done = _read_json(token, candidate)
        if not done or done.get("status") != "AI_DONE" or done.get("algorithmRevision") != REVISION:
            continue
        pdf_id = (done.get("masterPdf") or {}).get("driveId")
        for prefix, accepted_status in (
            ("METHODOLOGY_READY", "METHODOLOGY_READY"),
            ("METHODOLOGY_PARTIAL", "METHODOLOGY_PARTIAL"),
        ):
            receipts = base.io.search(token, f"trashed=false and name='{prefix}_{job_id}.json'")
            receipts.sort(key=lambda item: item.get("modifiedTime") or "", reverse=True)
            for receipt_item in receipts:
                receipt = _read_json(token, receipt_item)
                if (
                    receipt
                    and receipt.get("status") == accepted_status
                    and receipt.get("algorithmRevision") == REVISION
                    and receipt.get("masterPdfDriveId") == pdf_id
                ):
                    return done
    return None


def process_job(token):
    job_id = os.environ["BRIDGE_JOB_ID"]
    existing = _existing_same_revision_done(token, job_id)
    if existing is not None:
        base.io.safe(job_id=job_id, stage="ALREADY_DONE", exit_code=0)
        persist_completed_drive_job(token)
        return existing
    return previous.process_job(token)


__all__ = [
    "REVISION",
    "obtain_transcript_with_local_diarization",
    "master_payload_with_quality_v2",
    "validate_with_readiness_v2",
    "upload_json_with_readiness_v2",
    "process_job",
]
