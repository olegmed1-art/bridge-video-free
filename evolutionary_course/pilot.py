"""Fail-closed pilot runner for existing Video 3.1 longitudinal artifacts."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from .video31_adapter import Video31AdapterError, adapt_video31_quality

PILOT_SCHEMA = "evolutionary-course-video31-pilot-report-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _walk(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _source_inventory(payload: Mapping[str, Any]) -> tuple[list[str], dict[str, str]]:
    segment_ids: set[str] = set()
    frame_hashes: dict[str, str] = {}
    for item in _walk(payload.get("technical_qc", {})):
        segment_id = str(item.get("segment_id") or item.get("evidence_id") or "").strip()
        if segment_id.startswith("segment_"):
            segment_ids.add(segment_id)
        evidence_id = str(item.get("evidence_id") or "").strip()
        sha256 = str(item.get("sha256") or "").strip().lower()
        if evidence_id.startswith("frame_") and _SHA256.fullmatch(sha256):
            frame_hashes[evidence_id] = sha256
    return sorted(segment_ids), frame_hashes


def _verified_master_transcript_inventory(
    master_analysis: Mapping[str, Any], *, source_job_id: str,
    source_video_file_id: str, actual_sha256: str | None,
    expected_sha256: str | None,
) -> list[str]:
    if master_analysis.get("schema") != "bridge-video-master-analysis":
        raise ValueError("MASTER_ANALYSIS_SCHEMA_MISMATCH")
    if master_analysis.get("schemaVersion") != 3:
        raise ValueError("MASTER_ANALYSIS_VERSION_MISMATCH")
    if str(master_analysis.get("job_id") or "") != source_job_id:
        raise ValueError("MASTER_ANALYSIS_JOB_ID_MISMATCH")
    source = master_analysis.get("source")
    if not isinstance(source, Mapping) or str(source.get("driveId") or "") != source_video_file_id:
        raise ValueError("MASTER_ANALYSIS_VIDEO_FILE_ID_MISMATCH")
    if not actual_sha256 or not expected_sha256:
        raise ValueError("MASTER_ANALYSIS_SHA256_REQUIRED")
    if not _SHA256.fullmatch(actual_sha256) or actual_sha256 != expected_sha256:
        raise ValueError("MASTER_ANALYSIS_SHA256_MISMATCH")
    transcript = master_analysis.get("transcript")
    if not isinstance(transcript, list) or not transcript:
        raise ValueError("MASTER_TRANSCRIPT_INVENTORY_MISSING")
    segment_ids: list[str] = []
    for segment in transcript:
        if not isinstance(segment, Mapping):
            raise ValueError("MASTER_TRANSCRIPT_SEGMENT_INVALID")
        segment_id = str(segment.get("segment_id") or "").strip()
        if (
            not segment_id.startswith("segment_")
            or segment.get("unreliable") is not False
            or segment.get("semantic_qc") != "PASS"
        ):
            continue
        segment_ids.append(segment_id)
    if not segment_ids or len(segment_ids) != len(set(segment_ids)):
        raise ValueError("MASTER_TRANSCRIPT_INVENTORY_INVALID")
    return sorted(segment_ids)


def _normalized_quality(quality: Mapping[str, Any], frame_hashes: Mapping[str, str]) -> dict[str, Any]:
    result = deepcopy(dict(quality))
    interactions = result.get("learning_interactions")
    if not isinstance(interactions, list):
        return result
    for interaction in interactions:
        if not isinstance(interaction, dict):
            continue
        refs = interaction.get("evidence_refs")
        if isinstance(refs, list):
            interaction["evidence_refs"] = [
                ref for ref in refs if isinstance(ref, str) and ref.startswith("segment_")
            ]
        visual = interaction.get("visual_evidence_refs")
        normalized_frames: list[str] = []
        if isinstance(visual, list):
            for ref in visual:
                evidence_id = (str(ref.get("evidence_id") or "").strip()
                               if isinstance(ref, Mapping) else str(ref or "").strip())
                sha256 = frame_hashes.get(evidence_id, evidence_id)
                if _SHA256.fullmatch(sha256) and sha256 not in normalized_frames:
                    normalized_frames.append(sha256)
        interaction["visual_evidence_refs"] = normalized_frames
    return result


def run_longitudinal_pilot(
    payload: Mapping[str, Any], *, confirmation: Mapping[str, Any] | None = None,
    catalog: Mapping[str, Any] | None = None,
    master_analysis: Mapping[str, Any] | None = None,
    master_analysis_sha256: str | None = None,
    expected_master_analysis_sha256: str | None = None,
) -> dict[str, Any]:
    """Run one read-only pilot or return explicit fail-closed blockers."""
    blockers: list[str] = []
    quality = payload.get("quality_v2")
    if not isinstance(quality, Mapping):
        blockers.append("QUALITY_V2_MISSING")
    identity = payload.get("lesson_identity")
    if not isinstance(identity, Mapping):
        identity = {}
        blockers.append("LESSON_IDENTITY_MISSING")
    confirmation = confirmation or {}
    if confirmation.get("lesson_date_status") != "CONFIRMED":
        blockers.append("INDEPENDENT_LESSON_DATE_CONFIRMATION_REQUIRED")
    confirmed_date = str(confirmation.get("lesson_date") or "").strip()
    if not confirmed_date:
        blockers.append("CONFIRMED_LESSON_DATE_MISSING")
    analyzed_file_id = str(identity.get("master_source_drive_id") or "").strip()
    original_file_id = str(identity.get("original_source_drive_id") or "").strip()
    expected_file_id = analyzed_file_id or original_file_id
    confirmed_file_id = str(confirmation.get("video_file_id") or "").strip()
    if not confirmed_file_id:
        blockers.append("EXACT_VIDEO_FILE_ID_MISSING")
    elif expected_file_id and confirmed_file_id != expected_file_id:
        blockers.append("VIDEO_FILE_ID_MISMATCH")
    source_name = str(confirmation.get("source_name") or "").strip()
    if not source_name:
        blockers.append("EXACT_SOURCE_NAME_MISSING")
    if catalog is None:
        blockers.append("REVIEWED_SKILL_CATALOG_REQUIRED")
    transcript_ids, frame_hashes = _source_inventory(payload)
    if master_analysis is not None:
        try:
            transcript_ids = _verified_master_transcript_inventory(
                master_analysis,
                source_job_id=str(payload.get("job_id") or ""),
                source_video_file_id=expected_file_id,
                actual_sha256=master_analysis_sha256,
                expected_sha256=expected_master_analysis_sha256,
            )
        except ValueError as exc:
            blockers.append(str(exc))
    if not transcript_ids:
        blockers.append("SOURCE_TRANSCRIPT_INVENTORY_MISSING")
    base = {
        "schema": PILOT_SCHEMA,
        "source_job_id": payload.get("job_id"),
        "lesson_number": identity.get("lesson_number"),
        "source_artifact_schema": payload.get("schema"),
        "analyzed_source_video_file_id": analyzed_file_id or None,
        "original_source_video_file_id": original_file_id or None,
        "media_reprocessed": False,
        "authority": {"canonical_promotion_allowed": False, "curriculum_activation_allowed": False,
                      "student_profile_write_allowed": False, "publication_allowed": False},
    }
    if blockers:
        return {**base, "status": "BLOCKED", "blockers": list(dict.fromkeys(blockers))}
    try:
        adapted = adapt_video31_quality(
            _normalized_quality(quality, frame_hashes),
            lesson_identity={"lesson_date": confirmed_date, "lesson_date_status": "CONFIRMED"},
            source={"video_file_id": confirmed_file_id, "source_name": source_name,
                    "evidence_state": "VERIFIED", "transcript_segment_ids": transcript_ids,
                    "frame_sha256": sorted(set(frame_hashes.values()))},
            skill_catalog=catalog,
            require_catalog_binding=True,
        )
    except Video31AdapterError as exc:
        return {**base, "status": "BLOCKED", "blockers": [f"ADAPTER_REJECTED: {exc}"]}
    needs_methodology = any(
        "SKILL_WORDING_NOT_REVIEWED" in item.get("reason_codes", [])
        for item in adapted["rejected_interactions"]
    )
    has_accepted_episode = adapted["accepted_episode_count"] > 0
    needs_evidence_review = not (needs_methodology or has_accepted_episode)
    if needs_methodology:
        status = "METHODOLOGY_REVIEW_REQUIRED"
    elif needs_evidence_review:
        status = "EVIDENCE_REVIEW_REQUIRED"
    else:
        status = "READY_FOR_PRIVATE_REVIEW"
    return {
        **base,
        "status": status,
        "blockers": [],
        "adapter_report": adapted,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the read-only Evolutionary Course pilot")
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--confirmations", type=Path)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--master-analysis", type=Path)
    parser.add_argument("--master-analysis-sha256")
    args = parser.parse_args(argv)
    confirmations: Mapping[str, Any] = {}
    if args.confirmations:
        confirmations = json.loads(args.confirmations.read_text(encoding="utf-8"))
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    master_analysis = None
    master_analysis_sha256 = None
    if args.master_analysis:
        master_bytes = args.master_analysis.read_bytes()
        master_analysis_sha256 = hashlib.sha256(master_bytes).hexdigest()
        master_analysis = json.loads(master_bytes)
        if not args.master_analysis_sha256:
            parser.error("--master-analysis-sha256 is required with --master-analysis")
    reports = []
    for path in args.inputs:
        payload = json.loads(path.read_text(encoding="utf-8"))
        reports.append(run_longitudinal_pilot(
            payload,
            confirmation=confirmations.get(str(payload.get("job_id") or "")),
            catalog=catalog,
            master_analysis=master_analysis,
            master_analysis_sha256=master_analysis_sha256,
            expected_master_analysis_sha256=args.master_analysis_sha256,
        ))
    print(json.dumps(reports, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if all(report["status"] != "BLOCKED" for report in reports) else 2


if __name__ == "__main__":
    raise SystemExit(main())
