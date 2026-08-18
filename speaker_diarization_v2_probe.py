#!/usr/bin/env python3
"""Re-evaluate an already processed lesson with speaker diarization v2 only.

The probe deliberately reuses the embedded master-analysis JSON from an
existing PDF. It downloads the source video read-only only to obtain acoustic
speaker turns; it does not repeat ASR, visual extraction, semantic parsing, PDF
construction, or authoritative database writes.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

import pymupdf

import run_drive_3_1_free as io
from run_drive_3_1_free_oidc import user_oauth_token
from bridge_speaker_diarization_v2 import DIARIZATION_REVISION, diarize_transcript
from diana_longitudinal_quality_v2 import build_quality_layer


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"MISSING_REQUIRED_ENV:{name}")
    return value


def _digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _embedded_master(pdf_path: Path) -> dict[str, Any]:
    doc = pymupdf.open(pdf_path)
    try:
        names = list(doc.embfile_names())
        if "master_analysis.json" not in names:
            raise RuntimeError("MASTER_ANALYSIS_ATTACHMENT_MISSING")
        raw = doc.embfile_get("master_analysis.json")
    finally:
        doc.close()
    if not raw:
        raise RuntimeError("MASTER_ANALYSIS_ATTACHMENT_EMPTY")
    payload = json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
    if not isinstance(payload, dict):
        raise RuntimeError("MASTER_ANALYSIS_ATTACHMENT_INVALID")
    return payload


def _compact_readiness(quality: Mapping[str, Any] | None) -> dict[str, Any]:
    quality = quality if isinstance(quality, Mapping) else {}
    readiness = quality.get("readiness") if isinstance(quality.get("readiness"), Mapping) else {}
    counts = quality.get("counts") if isinstance(quality.get("counts"), Mapping) else {}
    speaker = readiness.get("speaker_summary") if isinstance(readiness.get("speaker_summary"), Mapping) else {}
    return {
        "status": readiness.get("status"),
        "technical_status": readiness.get("technical_status"),
        "content_status": readiness.get("content_status"),
        "methodology_status": readiness.get("methodology_status"),
        "methodology_issues": list(readiness.get("methodology_issues") or []),
        "complete_learning_interactions": counts.get("complete_learning_interactions"),
        "partial_learning_interactions": counts.get("partial_learning_interactions"),
        "speaker_summary": {
            "status": speaker.get("status"),
            "transcript_segments": speaker.get("transcript_segments"),
            "speaker_labeled_segments": speaker.get("speaker_labeled_segments"),
            "speaker_labeled_ratio": speaker.get("speaker_labeled_ratio"),
            "role_labeled_segments": speaker.get("role_labeled_segments"),
            "role_labeled_ratio": speaker.get("role_labeled_ratio"),
            "role_counts": speaker.get("role_counts"),
            "speaker_clusters": speaker.get("speaker_clusters"),
            "mean_role_confidence": speaker.get("mean_role_confidence"),
            "roles_mapped": speaker.get("roles_mapped"),
        },
    }


def main() -> None:
    token = user_oauth_token()
    if not token:
        raise RuntimeError("BLOCKED_ACCESS:GOOGLE_DRIVE_OAUTH_UNAVAILABLE")

    job_id = _required("BRIDGE_JOB_ID")
    source_drive_id = _required("BRIDGE_SOURCE_DRIVE_ID")
    master_pdf_drive_id = _required("BRIDGE_MASTER_PDF_DRIVE_ID")
    work_folder_id = _required("BRIDGE_WORK_FOLDER_ID")
    output_folder_id = os.environ.get("BRIDGE_OUTPUT_FOLDER_ID", "").strip() or work_folder_id
    lesson_number = os.environ.get("BRIDGE_LESSON_NUMBER", "").strip() or None
    lesson_date = os.environ.get("BRIDGE_LESSON_DATE_CANDIDATE", "").strip() or None

    with tempfile.TemporaryDirectory(prefix="bridge-speaker-v2-probe-") as tmp:
        root = Path(tmp)
        video_path = root / "source.mp4"
        pdf_path = root / "master.pdf"

        print("SPEAKER_V2_PROBE: downloading source read-only")
        io.download(token, source_drive_id, video_path)
        print("SPEAKER_V2_PROBE: downloading existing master PDF")
        io.download(token, master_pdf_drive_id, pdf_path)

        master = _embedded_master(pdf_path)
        transcript = master.get("transcript") or []
        if not isinstance(transcript, list) or not transcript:
            raise RuntimeError("MASTER_TRANSCRIPT_MISSING")

        baseline_quality = master.get("longitudinal_quality_v2")
        if not isinstance(baseline_quality, Mapping):
            baseline_quality = master.get("quality_v2") if isinstance(master.get("quality_v2"), Mapping) else {}

        diarized, report = diarize_transcript(video_path, transcript, root, enabled=True)
        labeled = sum(bool(item.get("speaker") or item.get("speaker_cluster")) for item in diarized)
        if labeled == 0:
            raise RuntimeError(
                "SPEAKER_DIARIZATION_V2_PRODUCED_NO_LABELS: "
                + str(report.get("status"))
                + ":"
                + str(report.get("primary_engine_detail") or report.get("detail") or "")
            )

        enriched_master = copy.deepcopy(master)
        enriched_master["transcript"] = diarized
        enriched_master["speaker_diarization_v2"] = report
        quality = build_quality_layer(
            enriched_master,
            lesson_identity={
                "lesson_number": lesson_number,
                "lesson_date": lesson_date,
                "lesson_date_status": "CANDIDATE" if lesson_date else "UNKNOWN",
            },
        )

        baseline = _compact_readiness(baseline_quality)
        recomputed = _compact_readiness(quality)
        comparison = {
            "speaker_labeled_delta": (
                int((recomputed.get("speaker_summary") or {}).get("speaker_labeled_segments") or 0)
                - int((baseline.get("speaker_summary") or {}).get("speaker_labeled_segments") or 0)
            ),
            "role_labeled_delta": (
                int((recomputed.get("speaker_summary") or {}).get("role_labeled_segments") or 0)
                - int((baseline.get("speaker_summary") or {}).get("role_labeled_segments") or 0)
            ),
            "complete_learning_interactions_delta": (
                int(recomputed.get("complete_learning_interactions") or 0)
                - int(baseline.get("complete_learning_interactions") or 0)
            ),
            "methodology_status_before": baseline.get("methodology_status"),
            "methodology_status_after": recomputed.get("methodology_status"),
        }

        full_payload = {
            "schema": "bridge-speaker-diarization-v2-probe",
            "schema_version": 1,
            "job_id": job_id,
            "source_drive_id": source_drive_id,
            "source_master_pdf_drive_id": master_pdf_drive_id,
            "source_algorithm_revision": master.get("algorithmRevision"),
            "diarization_revision": DIARIZATION_REVISION,
            "source_untouched": True,
            "heavy_video_reprocessed": False,
            "asr_reprocessed": False,
            "visual_reprocessed": False,
            "semantic_source_reused": True,
            "authority_writes": {
                "canon": False,
                "curriculum": False,
                "student_profile": False,
                "neon": False,
            },
            "diarization": report,
            "baseline": baseline,
            "recomputed": recomputed,
            "comparison": comparison,
            "diarized_transcript": diarized,
            "quality_v2_recomputed": quality,
        }
        digest = _digest({
            "job_id": job_id,
            "diarization": report,
            "comparison": comparison,
            "quality_input": (quality.get("incremental_processing") or {}).get("input_fingerprint"),
        })[:12]
        summary = {
            "schema": "bridge-speaker-diarization-v2-probe-receipt",
            "job_id": job_id,
            "digest": digest,
            "diarization_revision": DIARIZATION_REVISION,
            "source_untouched": True,
            "heavy_video_reprocessed": False,
            "asr_reprocessed": False,
            "visual_reprocessed": False,
            "authority_writes": full_payload["authority_writes"],
            "diarization_status": report.get("status"),
            "diarization_engine": report.get("engine") or report.get("primary_engine"),
            "speaker_labeled_segments": labeled,
            "speaker_labeled_ratio": round(labeled / max(1, len(diarized)), 4),
            "role_mapping_supported": report.get("mapping_supported") or report.get("role_mapping_supported"),
            "baseline": baseline,
            "recomputed": recomputed,
            "comparison": comparison,
        }

        full_name = f"DIANA_SPEAKER_V2_{job_id}_{digest}.json"
        receipt_name = f"DIANA_SPEAKER_V2_DONE_{job_id}_{digest}.json"
        full_item = io.upload_json(token, output_folder_id, full_name, full_payload)
        summary["full_result_drive_id"] = full_item.get("id")
        receipt_item = io.upload_json(token, work_folder_id, receipt_name, summary)
        print(
            "SPEAKER_V2_PROBE_DONE",
            f"receipt={receipt_item.get('id')}",
            f"full={full_item.get('id')}",
            f"labeled={labeled}/{len(diarized)}",
            f"status={report.get('status')}",
            f"methodology={comparison.get('methodology_status_before')}->{comparison.get('methodology_status_after')}",
            f"complete_delta={comparison.get('complete_learning_interactions_delta')}",
        )


if __name__ == "__main__":
    main()
