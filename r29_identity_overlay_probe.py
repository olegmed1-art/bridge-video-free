#!/usr/bin/env python3
"""Build a private r29 speaker_map from an already validated r25.15 master PDF.

No ASR, visual extraction, acoustic embedding generation, or source-video heavy
processing is repeated.  Real-person evidence and registry data are read only
from private Google Drive documents at runtime and are never printed to logs.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping
from urllib.parse import quote

import pymupdf
import requests

import run_drive_3_1_free as io
from run_drive_3_1_free_oidc import user_oauth_token
from bridge_speaker_mapping_r29 import SpeakerMappingEngine

R25_REVISION = "3.1-free-r25.15"
R29_REVISION = "3.1-free-r29"
DRIVE_API = "https://www.googleapis.com/drive/v3"


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"MISSING_REQUIRED_ENV:{name}")
    return value


def _valid_drive_id(value: str) -> str:
    if not value or any(ch not in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_-" for ch in value):
        raise RuntimeError("INVALID_DRIVE_ID")
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


def _export_private_json(token: str, file_id: str) -> dict[str, Any]:
    file_id = _valid_drive_id(file_id)
    metadata = requests.get(
        f"{DRIVE_API}/files/{quote(file_id, safe='')}",
        params={"fields": "id,name,mimeType,trashed", "supportsAllDrives": "true"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    metadata.raise_for_status()
    meta = metadata.json()
    if meta.get("trashed"):
        raise RuntimeError("PRIVATE_EVIDENCE_TRASHED")
    mime = str(meta.get("mimeType") or "")
    if mime == "application/vnd.google-apps.document":
        response = requests.get(
            f"{DRIVE_API}/files/{quote(file_id, safe='')}/export",
            params={"mimeType": "text/plain"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )
    else:
        response = requests.get(
            f"{DRIVE_API}/files/{quote(file_id, safe='')}",
            params={"alt": "media", "supportsAllDrives": "true"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )
    response.raise_for_status()
    # Drive may return UTF-8 JSON without a charset header. requests can then
    # decode .text as ISO-8859-1, turning an actual UTF-8 BOM into visible
    # mojibake. Decode the raw bytes as utf-8-sig so both BOM and no-BOM files
    # are handled deterministically.
    payload = json.loads(response.content.decode("utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError("PRIVATE_EVIDENCE_JSON_INVALID")
    return payload


def _private_registry(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    if isinstance(payload.get("participants"), Mapping):
        candidates = payload["participants"]
    else:
        candidates = payload
    registry: dict[str, dict[str, Any]] = {}
    for participant_ref, value in candidates.items():
        if not isinstance(value, Mapping):
            continue
        if value.get("confirmed") is not True or value.get("active") is not True:
            continue
        role = str(value.get("role") or value.get("role_if_confirmed") or "").strip()
        if not role:
            continue
        registry[str(participant_ref)] = dict(value)
        registry[str(participant_ref)]["role"] = role
    if len(registry) < 2:
        raise RuntimeError("PRIVATE_PARTICIPANT_REGISTRY_INSUFFICIENT")
    return registry


def _identity_evidence(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = payload.get("anchors") or payload.get("identity_evidence") or payload.get("evidence") or []
    if not isinstance(values, list):
        raise RuntimeError("PRIVATE_IDENTITY_EVIDENCE_INVALID")
    out: list[dict[str, Any]] = []
    for item in values:
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("evidence_type") or "")
        if kind not in {"provider", "acoustic", "visual", "interaction"}:
            continue
        out.append(dict(item))
    if not out:
        raise RuntimeError("PRIVATE_IDENTITY_EVIDENCE_EMPTY")
    return out


def _diarization(master: Mapping[str, Any]) -> Mapping[str, Any]:
    technical = master.get("technical_qc") if isinstance(master.get("technical_qc"), Mapping) else {}
    transcript_qc = technical.get("transcript") if isinstance(technical.get("transcript"), Mapping) else {}
    report = transcript_qc.get("speakerDiarization") if isinstance(transcript_qc.get("speakerDiarization"), Mapping) else {}
    return report


def _validate_r25_master(master: Mapping[str, Any], job_id: str) -> Mapping[str, Any]:
    revision = str(master.get("algorithmRevision") or "")
    if revision != R25_REVISION:
        raise RuntimeError("R29_SOURCE_REVISION_NOT_R25_15")
    if str(master.get("job_id") or "") != job_id:
        raise RuntimeError("R29_SOURCE_JOB_MISMATCH")
    transcript = master.get("transcript")
    if not isinstance(transcript, list) or not transcript:
        raise RuntimeError("R29_SOURCE_TRANSCRIPT_MISSING")
    report = _diarization(master)
    if report.get("cluster_collapse_detected") is True:
        raise RuntimeError("R29_BLOCKED_CLUSTER_COLLAPSE")
    if report.get("status") not in {"DIARIZED_ROLE_MAPPED", "EXISTING_SPEAKER_LABELS_PRESERVED"}:
        raise RuntimeError("R29_SOURCE_DIARIZATION_NOT_READY")
    privacy = report.get("privacy") if isinstance(report.get("privacy"), Mapping) else {}
    if privacy.get("real_person_identity_claimed") is True:
        raise RuntimeError("R29_SOURCE_IDENTITY_BOUNDARY_VIOLATION")
    if privacy.get("voice_embedding_persisted") is True or privacy.get("cross_lesson_voice_profile_persisted") is True:
        raise RuntimeError("R29_SOURCE_PRIVACY_REGRESSION")
    return report


def _intervals_from_transcript(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    intervals: list[dict[str, Any]] = []
    for index, segment in enumerate(transcript):
        if not isinstance(segment, Mapping):
            continue
        try:
            start = float(segment.get("start") or 0.0)
            end = float(segment.get("end") or start)
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        cluster = str(segment.get("speaker") or segment.get("speaker_cluster") or "").strip() or None
        try:
            confidence = float(segment.get("speaker_confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        intervals.append(
            {
                "interval_ref": str(segment.get("segment_id") or f"segment-{index:05d}"),
                "start": start,
                "end": end,
                "acoustic_cluster_id": cluster,
                "overlap_status": "SINGLE_SPEAKER",
                "acoustic_confidence": confidence,
                "anchor_eligible": bool(cluster and confidence >= 0.80),
            }
        )
    if not intervals:
        raise RuntimeError("R29_NO_SPEECH_INTERVALS")
    return intervals


def _validate_anchor_alignment(transcript: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> dict[str, int]:
    by_id = {
        str(item.get("segment_id")): item
        for item in transcript
        if isinstance(item, Mapping) and item.get("segment_id")
    }
    checked = 0
    identity_checked = 0
    for item in evidence:
        if item.get("evidence_type") not in {"provider", "acoustic", "visual"}:
            continue
        identity_checked += 1
        source_ref = str(item.get("source_ref") or "")
        if not source_ref or source_ref not in by_id:
            raise RuntimeError("R29_PRIVATE_ANCHOR_SOURCE_REF_MISSING")
        segment = by_id[source_ref]
        cluster = str(segment.get("speaker") or segment.get("speaker_cluster") or "")
        if cluster != str(item.get("cluster_id") or ""):
            raise RuntimeError("R29_PRIVATE_ANCHOR_CLUSTER_MISMATCH")
        midpoint = (float(item.get("start") or 0.0) + float(item.get("end") or item.get("start") or 0.0)) / 2.0
        if not (float(segment.get("start") or 0.0) - 0.75 <= midpoint <= float(segment.get("end") or 0.0) + 0.75):
            raise RuntimeError("R29_PRIVATE_ANCHOR_TIME_MISMATCH")
        checked += 1
    if identity_checked < 6 or checked != identity_checked:
        raise RuntimeError("R29_PRIVATE_ANCHOR_COUNT_INSUFFICIENT")
    return {"identity_anchors_checked": checked}


def _sanitize_blockers(blockers: list[object]) -> list[str]:
    return sorted({str(item).split(":", 1)[0] for item in blockers})


def main() -> None:
    token = user_oauth_token()
    if not token:
        raise RuntimeError("BLOCKED_ACCESS:GOOGLE_DRIVE_OAUTH_UNAVAILABLE")

    job_id = _required("BRIDGE_JOB_ID")
    if len(job_id) != 32 or any(ch not in "0123456789abcdef" for ch in job_id):
        raise RuntimeError("INVALID_OPAQUE_JOB_ID")
    master_pdf_drive_id = _valid_drive_id(_required("BRIDGE_MASTER_PDF_DRIVE_ID"))
    output_folder_id = _valid_drive_id(_required("BRIDGE_OUTPUT_FOLDER_ID"))
    work_folder_id = _valid_drive_id(_required("BRIDGE_WORK_FOLDER_ID"))
    evidence_doc_id = _valid_drive_id(_required("BRIDGE_R29_IDENTITY_EVIDENCE_DOC_ID"))
    registry_doc_id = _valid_drive_id(_required("BRIDGE_R29_PARTICIPANT_REGISTRY_DOC_ID"))

    with tempfile.TemporaryDirectory(prefix="bridge-r29-identity-") as tmp:
        pdf_path = Path(tmp) / "master.pdf"
        io.download(token, master_pdf_drive_id, pdf_path)
        master = _embedded_master(pdf_path)
        diarization = _validate_r25_master(master, job_id)
        transcript = [dict(item) for item in master.get("transcript") or [] if isinstance(item, Mapping)]

        evidence_payload = _export_private_json(token, evidence_doc_id)
        registry_payload = _export_private_json(token, registry_doc_id)
        for private_payload in (evidence_payload, registry_payload):
            private_job = str(private_payload.get("job_id") or "")
            if private_job and private_job != job_id:
                raise RuntimeError("R29_PRIVATE_EVIDENCE_JOB_MISMATCH")
            source_id = str(private_payload.get("source_drive_id") or "")
            master_source = str((master.get("source") or {}).get("driveId") or "")
            if source_id and master_source and source_id != master_source:
                raise RuntimeError("R29_PRIVATE_EVIDENCE_SOURCE_MISMATCH")

        evidence = _identity_evidence(evidence_payload)
        registry = _private_registry(registry_payload)
        anchor_qc = _validate_anchor_alignment(transcript, evidence)
        intervals = _intervals_from_transcript(transcript)

        engine = SpeakerMappingEngine()
        speaker_map = engine.build_speaker_map(
            transcript,
            intervals,
            evidence,
            registry,
            supersedes=f"{R25_REVISION}:{master_pdf_drive_id}",
        )
        speaker_map.update(
            {
                "job_id": job_id,
                "sourceAlgorithmRevision": R25_REVISION,
                "sourceMasterPdfDriveId": master_pdf_drive_id,
                "sourceMasterJsonSha256": _digest(master),
                "identityEvidenceDocumentRef": evidence_doc_id,
                "participantRegistryDocumentRef": registry_doc_id,
                "fieldValidation": {
                    **anchor_qc,
                    "source_cluster_collapse_detected": bool(diarization.get("cluster_collapse_detected")),
                    "source_speaker_labeled_segments": int(diarization.get("segments_labeled") or 0),
                    "source_speaker_labeled_ratio": float(diarization.get("speaker_labeled_ratio") or 0.0),
                    "source_mean_assignment_confidence": float(diarization.get("mean_assignment_confidence") or 0.0),
                    "source_selected_hypothesis": diarization.get("selected_hypothesis") or diarization.get("model_id"),
                    "source_untouched": True,
                    "asr_reprocessed": False,
                    "visual_reprocessed": False,
                    "heavy_video_reprocessed": False,
                    "paid_api": 0,
                    "paid_cloud": 0,
                },
                "authority": {
                    "canon_write": False,
                    "curriculum_write": False,
                    "student_profile_write": bool(speaker_map.get("operationalGate", {}).get("operational")),
                    "profile_write_requires_confirmed_person": True,
                },
            }
        )

        qc = speaker_map.get("speaker_mapping_qc") or {}
        gate = speaker_map.get("operationalGate") or {}
        digest = _digest(
            {
                "job_id": job_id,
                "master": speaker_map.get("sourceMasterJsonSha256"),
                "cluster_count": len(speaker_map.get("clusterMappings") or {}),
                "qc": qc,
                "gate": gate,
                "anchor_qc": anchor_qc,
            }
        )[:16]
        filename = f"speaker_map_{job_id}_{digest}.json"
        uploaded = io.upload_json(token, output_folder_id, filename, speaker_map)

        receipt = {
            "schema": "bridge.r29.speaker_mapping_receipt",
            "schemaVersion": "1.0",
            "algorithmRevision": R29_REVISION,
            "status": "SPEAKER_MAPPING_OPERATIONAL" if gate.get("operational") else "SPEAKER_MAPPING_BLOCKED",
            "job_id": job_id,
            "sourceAlgorithmRevision": R25_REVISION,
            "sourceMasterPdfDriveId": master_pdf_drive_id,
            "speakerMapDriveId": uploaded.get("id"),
            "speakerMapDigest": digest,
            "clusterCount": len(speaker_map.get("clusterMappings") or {}),
            "identityAnchorCount": anchor_qc["identity_anchors_checked"],
            "speakerCoverage": qc.get("speaker_coverage_by_speech_duration"),
            "participantCoverage": qc.get("participant_coverage_by_speech_duration"),
            "unknownDuration": qc.get("unknown_duration"),
            "conflictDuration": qc.get("conflict_duration"),
            "failureCodes": _sanitize_blockers(list(gate.get("blockers") or [])),
            "sourceUntouched": True,
            "heavyVideoReprocessed": False,
            "asrReprocessed": False,
            "visualReprocessed": False,
            "realNamesLogged": False,
            "speakerEmbeddingsPersisted": False,
            "temporaryAudioAnchorsPersisted": False,
            "authorityWrites": {"canon": False, "curriculum": False, "student_profile": False, "neon": False},
        }
        receipt_item = io.upload_json(token, work_folder_id, f"R29_SPEAKER_MAPPING_{job_id}_{digest}.json", receipt)
        print(
            "R29_SPEAKER_MAPPING_DONE",
            f"receipt={receipt_item.get('id')}",
            f"map={uploaded.get('id')}",
            f"clusters={receipt['clusterCount']}",
            f"anchors={receipt['identityAnchorCount']}",
            f"speakerCoverage={receipt['speakerCoverage']}",
            f"participantCoverage={receipt['participantCoverage']}",
            f"failures={len(receipt['failureCodes'])}",
            f"operational={bool(gate.get('operational'))}",
        )
        if not gate.get("operational"):
            raise RuntimeError("R29_EVIDENCE_GATE_FAILED:" + ",".join(receipt["failureCodes"]))


if __name__ == "__main__":
    main()
