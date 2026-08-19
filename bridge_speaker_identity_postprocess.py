#!/usr/bin/env python3
"""Integrated r29 identity-overlay stage for Bridge Video 3.1 FREE.

This stage runs after the r25.15 heavy transcription/diarization result has been
routed to its isolated output folder, or directly from the validated AI_DONE
payload returned by the heavy worker. It never changes raw ASR, the source
video, or anonymous acoustic clusters.

Private identity evidence may be supplied explicitly through environment
variables or discovered conservatively from private Drive documents whose
contents match the exact job/source. If sufficient evidence is unavailable,
the transcript remains anonymous and named/person-specific attribution stays
blocked rather than being guessed.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

import run_drive_3_1_free as io
from run_drive_3_1_free_oidc import user_oauth_token
import r29_identity_overlay_probe as r29

R25_REVISION = "3.1-free-r25.15"
R29_REVISION = "3.1-free-r29"


def _valid_drive_id(value: str) -> bool:
    return bool(value) and all(ch in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_-" for ch in value)


def _read_json(token: str, item: Mapping[str, Any]) -> dict[str, Any] | None:
    with tempfile.TemporaryDirectory(prefix="bridge-r29-integrated-") as td:
        path = Path(td) / "payload.json"
        io.download(token, str(item["id"]), path)
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError, OSError):
            return None
    return value if isinstance(value, dict) else None


def _latest_ai_done(token: str, job_id: str, output_folder_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    query = (
        f"trashed=false and name='AI_DONE_{job_id}.json' "
        f"and '{output_folder_id}' in parents"
    )
    items = io.search(token, query)
    items.sort(key=lambda item: item.get("modifiedTime") or "", reverse=True)
    for item in items:
        payload = _read_json(token, item)
        if _valid_done(payload, job_id):
            return dict(item), dict(payload or {})
    return None


def _valid_done(payload: Mapping[str, Any] | None, job_id: str) -> bool:
    return bool(
        payload
        and payload.get("status") == "AI_DONE"
        and payload.get("job_id") == job_id
        and payload.get("algorithmRevision") == R25_REVISION
        and _valid_drive_id(str((payload.get("masterPdf") or {}).get("driveId") or ""))
    )


def _existing_operational_mapping(token: str, job_id: str, work_folder_id: str, master_pdf_id: str) -> dict[str, Any] | None:
    prefix = f"R29_SPEAKER_MAPPING_{job_id}_"
    query = (
        f"trashed=false and name contains '{prefix}' "
        f"and '{work_folder_id}' in parents"
    )
    items = io.search(token, query)
    items.sort(key=lambda item: item.get("modifiedTime") or "", reverse=True)
    for item in items:
        payload = _read_json(token, item)
        if not payload:
            continue
        if (
            payload.get("status") == "SPEAKER_MAPPING_OPERATIONAL"
            and payload.get("algorithmRevision") == R29_REVISION
            and payload.get("job_id") == job_id
            and payload.get("sourceMasterPdfDriveId") == master_pdf_id
        ):
            return payload
    return None


def _candidate_private_docs(
    token: str,
    name_fragment: str,
    job_id: str,
    source_drive_id: str,
) -> list[str]:
    """Return private Drive doc ids whose *contents* match this exact job/source.

    Names only narrow discovery; they never establish identity. Invalid/unreadable
    candidates are ignored and no private contents are printed.
    """
    query = f"trashed=false and name contains '{name_fragment}'"
    items = io.search(token, query)
    items.sort(key=lambda item: item.get("modifiedTime") or "", reverse=True)
    matches: list[str] = []
    for item in items:
        file_id = str(item.get("id") or "")
        if not _valid_drive_id(file_id):
            continue
        try:
            payload = r29._export_private_json(token, file_id)
        except Exception:
            continue
        if str(payload.get("job_id") or "") != job_id:
            continue
        candidate_source = str(payload.get("source_drive_id") or "")
        if source_drive_id and candidate_source and candidate_source != source_drive_id:
            continue
        matches.append(file_id)
    return matches


def _autodiscover_private_ids(
    token: str,
    job_id: str,
    source_drive_id: str,
) -> tuple[str, str, str | None]:
    evidence = _candidate_private_docs(token, "r29_identity_evidence_", job_id, source_drive_id)
    registry = _candidate_private_docs(token, "r29_participant_registry_", job_id, source_drive_id)
    if len(evidence) == 1 and len(registry) == 1:
        return evidence[0], registry[0], None
    if not evidence and not registry:
        return "", "", "IDENTITY_EVIDENCE_NOT_FOUND"
    if len(evidence) > 1 or len(registry) > 1:
        return "", "", "R29_PRIVATE_EVIDENCE_AMBIGUOUS"
    return "", "", "R29_PRIVATE_EVIDENCE_INCOMPLETE"


def _status_receipt(job_id: str, master_pdf_id: str, status: str, reason: str) -> dict[str, Any]:
    return {
        "schema": "bridge.r29.integrated_identity_status",
        "schemaVersion": "1.0",
        "algorithmRevision": R29_REVISION,
        "sourceAlgorithmRevision": R25_REVISION,
        "status": status,
        "reason": reason,
        "job_id": job_id,
        "sourceMasterPdfDriveId": master_pdf_id,
        "anonymousSpeakerLabelsPreserved": True,
        "namedAttributionAllowed": False,
        "personSpecificWritesAllowed": False,
        "sourceUntouched": True,
        "rawAsrMutated": False,
        "heavyVideoReprocessed": False,
        "asrReprocessed": False,
        "visualReprocessed": False,
        "paidApi": 0,
        "paidCloud": 0,
        "speakerEmbeddingsPersisted": False,
        "temporaryAudioAnchorsPersisted": False,
        "authorityWrites": {
            "canon": False,
            "curriculum": False,
            "student_profile": False,
            "neon": False,
        },
    }


def _write_github_output(name: str, value: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not target:
        return
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def _anonymous_result(
    token: str,
    job_id: str,
    work_folder_id: str,
    master_pdf_id: str,
    reason: str,
) -> dict[str, Any]:
    receipt = _status_receipt(
        job_id,
        master_pdf_id,
        "SPEAKER_MAPPING_ANONYMOUS_ONLY",
        reason,
    )
    io.upload_json(token, work_folder_id, f"R29_IDENTITY_STATUS_{job_id}_ANONYMOUS.json", receipt)
    result = {
        "stage": "R29_IDENTITY_OVERLAY",
        "status": receipt["status"],
        "reason": reason,
        "job_id": job_id,
        "named_attribution": False,
        "source_untouched": True,
    }
    _write_github_output("identity_operational", "false")
    print(json.dumps(result, ensure_ascii=False))
    return result


def run(token: str, done_override: Mapping[str, Any] | None = None) -> dict[str, Any]:
    job_id = os.environ.get("BRIDGE_JOB_ID", "").strip()
    output_folder_id = os.environ.get("BRIDGE_OUTPUT_FOLDER_ID", "").strip()
    work_folder_id = os.environ.get("BRIDGE_WORK_FOLDER_ID", "").strip()
    explicit_evidence = os.environ.get("BRIDGE_R29_IDENTITY_EVIDENCE_DOC_ID", "").strip()
    explicit_registry = os.environ.get("BRIDGE_R29_PARTICIPANT_REGISTRY_DOC_ID", "").strip()

    if len(job_id) != 32 or any(ch not in "0123456789abcdef" for ch in job_id):
        raise RuntimeError("R29_INTEGRATED_INVALID_JOB_ID")
    # Identity overlay is an optional enrichment of a valid anonymous transcript.
    # Missing output/work routing must not destroy the transcription result.
    if not _valid_drive_id(output_folder_id) or not _valid_drive_id(work_folder_id):
        result = {
            "stage": "R29_IDENTITY_OVERLAY",
            "status": "NOT_CONFIGURED_NO_OUTPUT_WORK_FOLDER",
            "job_id": job_id,
            "named_attribution": False,
        }
        _write_github_output("identity_operational", "false")
        print(json.dumps(result, ensure_ascii=False))
        return result
    for value in (explicit_evidence, explicit_registry):
        if value and not _valid_drive_id(value):
            raise RuntimeError("R29_INTEGRATED_INVALID_PRIVATE_DRIVE_ID")

    if done_override is not None:
        done = dict(done_override)
        if not _valid_done(done, job_id):
            raise RuntimeError("R29_INTEGRATED_DONE_OVERRIDE_INVALID")
    else:
        source = _latest_ai_done(token, job_id, output_folder_id)
        if source is None:
            result = {"stage": "R29_IDENTITY_OVERLAY", "status": "SOURCE_NOT_READY", "job_id": job_id}
            _write_github_output("identity_operational", "false")
            print(json.dumps(result, ensure_ascii=False))
            return result
        _, done = source

    master_pdf_id = str((done.get("masterPdf") or {}).get("driveId") or "").strip()
    source_drive_id = str((done.get("original") or {}).get("driveId") or "").strip()

    # Explicit configuration, when supplied, is authoritative and must be complete.
    if bool(explicit_evidence) != bool(explicit_registry):
        receipt = _status_receipt(
            job_id,
            master_pdf_id,
            "SPEAKER_MAPPING_BLOCKED",
            "R29_IDENTITY_CONFIG_INCOMPLETE",
        )
        io.upload_json(token, work_folder_id, f"R29_IDENTITY_STATUS_{job_id}_BLOCKED.json", receipt)
        _write_github_output("identity_operational", "false")
        raise RuntimeError("R29_IDENTITY_CONFIG_INCOMPLETE")

    if explicit_evidence and explicit_registry:
        evidence_doc_id, registry_doc_id, discovery_reason = explicit_evidence, explicit_registry, None
    else:
        evidence_doc_id, registry_doc_id, discovery_reason = _autodiscover_private_ids(
            token, job_id, source_drive_id
        )

    if not evidence_doc_id or not registry_doc_id:
        return _anonymous_result(
            token,
            job_id,
            work_folder_id,
            master_pdf_id,
            discovery_reason or "IDENTITY_EVIDENCE_NOT_CONFIGURED",
        )

    existing = _existing_operational_mapping(token, job_id, work_folder_id, master_pdf_id)
    if existing is not None:
        result = {
            "stage": "R29_IDENTITY_OVERLAY",
            "status": "SPEAKER_MAPPING_ALREADY_OPERATIONAL",
            "job_id": job_id,
            "named_attribution": True,
            "source_untouched": True,
        }
        _write_github_output("identity_operational", "true")
        print(json.dumps(result, ensure_ascii=False))
        return result

    os.environ["BRIDGE_MASTER_PDF_DRIVE_ID"] = master_pdf_id
    os.environ["BRIDGE_R29_IDENTITY_EVIDENCE_DOC_ID"] = evidence_doc_id
    os.environ["BRIDGE_R29_PARTICIPANT_REGISTRY_DOC_ID"] = registry_doc_id
    # Private contents are read by the validated r29 probe and never printed.
    r29.main()
    result = {
        "stage": "R29_IDENTITY_OVERLAY",
        "status": "SPEAKER_MAPPING_OPERATIONAL",
        "job_id": job_id,
        "named_attribution": True,
        "source_untouched": True,
    }
    _write_github_output("identity_operational", "true")
    print(json.dumps(result, ensure_ascii=False))
    return result


def main() -> int:
    token = user_oauth_token()
    if not token:
        raise RuntimeError("BLOCKED_ACCESS:GOOGLE_DRIVE_OAUTH_UNAVAILABLE")
    run(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
