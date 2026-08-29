"""Bounded read-only export of one completed Universal Video evidence bundle.

The exporter never starts the resident service, submits work, reads raw media,
or promotes bridge/card evidence.  It validates one exact request against the
fixed resident status, done receipt, and result directory, then emits a small
sanitized receipt suitable for an external audit.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import stat
import time
from pathlib import Path
from typing import Any

from .result_conformance import ResultConformanceError, verify_result

EXPORT_SCHEMA = "universal-video-evidence-export-v1"
REQUEST_SCHEMA = "universal-video-evidence-export-request-v1"
STATUS_SCHEMA = "universal-video-resident-status-v2"
MAX_REQUEST_BYTES = 16 * 1024
MAX_STATUS_BYTES = 16 * 1024
MAX_DONE_BYTES = 256 * 1024
MAX_SHADOW_BYTES = 32 * 1024 * 1024
MAX_RECEIPT_BYTES = 64 * 1024
MAX_STATUS_AGE_SECONDS = 30
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
ALLOWED_REQUEST_FIELDS = frozenset({
    "schema", "job_id", "profile", "job_hash", "source_file_id",
    "request_commit", "requested_runtime_commit", "timeout_seconds",
})
ALLOWED_STATUS_FIELDS = frozenset({
    "schema", "instance_state", "active_jobs", "observed_at_unix",
    "installed_runtime_commit", "job_attestations",
})
ALLOWED_ATTESTATION_FIELDS = frozenset({
    "schema", "job_id", "request_commit", "requested_runtime_commit",
    "installed_runtime_commit", "observed_job_runtime_commit", "profile",
    "job_hash", "source_file_id", "canonical_output_untouched",
    "canonical_promotion_allowed", "publication_state",
})


class EvidenceExportError(RuntimeError):
    """A bounded export could not be proven safe and exact."""


def _strict_loads(raw: str) -> Any:
    def pairs(rows: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in rows:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value: str) -> Any:
        raise ValueError(f"non-finite JSON number: {value}")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def _read_regular_json(path: Path, *, max_bytes: int) -> Any:
    try:
        info = path.lstat()
    except OSError as exc:
        raise EvidenceExportError(f"missing fixed input: {path.name}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise EvidenceExportError(f"unsafe fixed input: {path.name}")
    if not 0 < info.st_size <= max_bytes:
        raise EvidenceExportError(f"fixed input exceeds byte cap: {path.name}")
    try:
        value = _strict_loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise EvidenceExportError(f"invalid fixed JSON input: {path.name}") from exc
    if not isinstance(value, dict):
        raise EvidenceExportError(f"fixed JSON input must be an object: {path.name}")
    return value


def _sha256(path: Path, *, max_bytes: int) -> tuple[str, int]:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise EvidenceExportError(f"unsafe artifact: {path.name}")
    if not 0 < info.st_size <= max_bytes:
        raise EvidenceExportError(f"artifact exceeds byte cap: {path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest(), info.st_size


def _hex(value: Any, *, width: int, field: str) -> str:
    text = str(value or "").strip().lower()
    pattern = HEX40 if width == 40 else HEX64
    if not pattern.fullmatch(text):
        raise EvidenceExportError(f"invalid {field}")
    return text


def _id(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not SAFE_ID.fullmatch(text) or text in {".", ".."}:
        raise EvidenceExportError(f"invalid {field}")
    return text


def _validate_request(request: dict[str, Any]) -> dict[str, Any]:
    if set(request) != ALLOWED_REQUEST_FIELDS or request.get("schema") != REQUEST_SCHEMA:
        raise EvidenceExportError("invalid export request shape")
    timeout = request.get("timeout_seconds")
    if type(timeout) is not int or not 1 <= timeout <= 20:
        raise EvidenceExportError("invalid export timeout")
    profile = str(request.get("profile") or "").strip().lower()
    if not SAFE_ID.fullmatch(profile):
        raise EvidenceExportError("invalid profile")
    return {
        "job_id": _id(request.get("job_id"), "job_id"),
        "profile": profile,
        "job_hash": _hex(request.get("job_hash"), width=64, field="job_hash"),
        "source_file_id": _id(request.get("source_file_id"), "source_file_id"),
        "request_commit": _hex(request.get("request_commit"), width=40, field="request_commit"),
        "requested_runtime_commit": _hex(
            request.get("requested_runtime_commit"), width=40, field="requested_runtime_commit"
        ),
        "timeout_seconds": timeout,
    }


def _validate_status(status: dict[str, Any], *, now: float) -> dict[str, Any]:
    if status.get("schema") != STATUS_SCHEMA or set(status) != ALLOWED_STATUS_FIELDS:
        raise EvidenceExportError("invalid resident status shape")
    if status.get("instance_state") != "RUNNING":
        raise EvidenceExportError("resident instance is not RUNNING")
    if status.get("active_jobs") != []:
        raise EvidenceExportError("resident has an active job")
    observed = status.get("observed_at_unix")
    if isinstance(observed, bool) or not isinstance(observed, (int, float)):
        raise EvidenceExportError("invalid resident status timestamp")
    observed_number = float(observed)
    if not math.isfinite(observed_number) or observed_number > now + 2 or now - observed_number > MAX_STATUS_AGE_SECONDS:
        raise EvidenceExportError("resident status is stale")
    installed = _hex(status.get("installed_runtime_commit"), width=40, field="installed_runtime_commit")
    raw = status.get("job_attestations")
    if not isinstance(raw, list) or len(raw) > 32 or any(not isinstance(item, dict) for item in raw):
        raise EvidenceExportError("invalid job attestations")
    for item in raw:
        if set(item) != ALLOWED_ATTESTATION_FIELDS or item.get("schema") != "universal-video-runtime-job-attestation-v1":
            raise EvidenceExportError("invalid job attestation shape")
        _id(item.get("job_id"), "attestation job_id")
        _hex(item.get("request_commit"), width=40, field="attestation request_commit")
        _hex(
            item.get("requested_runtime_commit"),
            width=40,
            field="attestation requested_runtime_commit",
        )
        _hex(
            item.get("installed_runtime_commit"),
            width=40,
            field="attestation installed_runtime_commit",
        )
        _hex(
            item.get("observed_job_runtime_commit"),
            width=40,
            field="attestation observed_job_runtime_commit",
        )
        if not re.fullmatch(r"^[a-z0-9._:-]{1,80}$", str(item.get("profile") or "")):
            raise EvidenceExportError("invalid attestation profile")
        _hex(item.get("job_hash"), width=64, field="attestation job_hash")
        _id(item.get("source_file_id"), "attestation source_file_id")
        if (
            item.get("canonical_output_untouched") is not True
            or item.get("canonical_promotion_allowed") is not False
            or item.get("publication_state") != "NOT_PUBLISHED"
        ):
            raise EvidenceExportError("invalid job attestation safety boundary")
    return {
        "schema": STATUS_SCHEMA,
        "observed_at_unix": observed_number,
        "installed_runtime_commit": installed,
        "job_attestations": list(raw),
    }


def _exact_runtime_attestation(
    status: dict[str, Any], request: dict[str, Any]
) -> dict[str, Any]:
    installed = status["installed_runtime_commit"]
    if installed != request["requested_runtime_commit"]:
        raise EvidenceExportError("installed runtime does not match requested runtime")
    matches = [
        item
        for item in status["job_attestations"]
        if (
            item.get("job_id") == request["job_id"]
            and item.get("request_commit") == request["request_commit"]
            and item.get("requested_runtime_commit") == request["requested_runtime_commit"]
            and item.get("installed_runtime_commit") == installed
            and item.get("observed_job_runtime_commit") == installed
            and item.get("profile") == request["profile"]
            and item.get("job_hash") == request["job_hash"]
            and item.get("source_file_id") == request["source_file_id"]
            and item.get("canonical_output_untouched") is True
            and item.get("canonical_promotion_allowed") is False
            and item.get("publication_state") == "NOT_PUBLISHED"
        )
    ]
    if len(matches) != 1:
        raise EvidenceExportError("exact observed runtime attestation unavailable")
    return matches[0]


def _transcript_rows(path: Path) -> tuple[list[dict[str, Any]], str, int]:
    digest, size = _sha256(path, max_bytes=MAX_SHADOW_BYTES)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise EvidenceExportError("invalid transcript JSONL") from exc
    if raw and not raw.endswith("\n"):
        raise EvidenceExportError("transcript JSONL is not canonically terminated")
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        value = _strict_loads(line)
        if not isinstance(value, dict):
            raise EvidenceExportError("invalid transcript segment")
        rows.append(value)
    return rows, digest, size


def _contains_promotion_true(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("canonical_promotion_allowed") is True:
            return True
        return any(_contains_promotion_true(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_promotion_true(item) for item in value)
    return False


def _speaker_summary(
    rows: list[dict[str, Any]],
    deferred: list[str],
    *,
    result_dir: Path,
    artifacts_by_name: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    labels = [str(row.get("speaker")) for row in rows if row.get("speaker") not in (None, "")]
    roles = sorted(
        {
            str(row.get("speaker_role_candidate"))
            for row in rows
            if row.get("speaker_role_candidate") in {"teacher", "student"}
        }
    )
    report_path = result_dir / "speaker_diarization.json"
    report_artifact = artifacts_by_name.get("speaker_diarization.json")
    report = None
    artifacts: list[dict[str, Any]] = []
    if report_path.exists() != (report_artifact is not None):
        raise EvidenceExportError("speaker report inventory mismatch")
    if report_path.exists() and report_artifact is not None:
        before_sha, before_size = _sha256(report_path, max_bytes=1024 * 1024)
        if (
            before_sha != report_artifact["sha256"]
            or before_size != report_artifact["size_bytes"]
        ):
            raise EvidenceExportError("speaker report changed before export")
        report = _read_regular_json(report_path, max_bytes=1024 * 1024)
        after_sha, after_size = _sha256(report_path, max_bytes=1024 * 1024)
        if (after_sha, after_size) != (before_sha, before_size):
            raise EvidenceExportError("speaker report changed during export")
        artifacts.append(
            {
                "locator": "speaker_diarization.json",
                "sha256": before_sha,
                "size_bytes": before_size,
            }
        )
    if not labels:
        summary = {
            "status": "UNAVAILABLE",
            "stage_status": report.get("status") if report else "DEFERRED",
            "reason": (
                str(report.get("reason"))
                if report
                else "SPEAKER_LABELS_MISSING" if "speaker_structure" in deferred else "SPEAKER_EVIDENCE_MISSING"
            ),
            "speaker_count": 0,
            "labeled_segments": 0,
            "unlabeled_segments": len(rows),
            "collapse": "GATE_REJECTED" if report else "NOT_COMPUTABLE_WITHOUT_HUMAN_REFERENCE",
            "fragmentation": "NOT_COMPUTABLE_WITHOUT_HUMAN_REFERENCE",
            "teacher_student_attribution": "UNAVAILABLE",
            "artifacts": artifacts,
        }
        if report and "label_coverage" in report:
            summary["label_coverage"] = report["label_coverage"]
            summary["speech_duration_coverage"] = report.get("speech_duration_coverage")
            summary["minimum_label_coverage"] = report.get("minimum_label_coverage")
        return summary
    summary = {
        "status": "OBSERVED_ANONYMOUS_LABELS",
        "stage_status": report.get("status") if report else "UNKNOWN",
        "quality_gate": report.get("quality_gate") if report else "UNAVAILABLE",
        "speaker_count": len(set(labels)),
        "speaker_labels": sorted(set(labels)),
        "labeled_segments": len(labels),
        "unlabeled_segments": len(rows) - len(labels),
        "collapse": "GATE_PASS",
        "fragmentation": "NOT_COMPUTABLE_WITHOUT_HUMAN_REFERENCE",
        "teacher_student_attribution": "SUGGESTION_ONLY" if roles else "UNAVAILABLE",
        "role_candidates": roles,
        "artifacts": artifacts,
    }
    if report and "label_coverage" in report:
        summary["label_coverage"] = report["label_coverage"]
        summary["speech_duration_coverage"] = report.get("speech_duration_coverage")
        summary["minimum_label_coverage"] = report.get("minimum_label_coverage")
    return summary


def _card_summary(result_dir: Path, deferred: list[str]) -> dict[str, Any]:
    summary_path = result_dir / "bridge_positions_profiled_shadow_summary.json"
    jsonl_path = result_dir / "bridge_positions_profiled_shadow.jsonl"
    pbn_path = result_dir / "bridge_positions_profiled_shadow.pbn"
    pdf_path = result_dir / "bridge_positions_profiled_shadow_report.pdf"
    if not summary_path.exists() and not jsonl_path.exists() and not pbn_path.exists() and not pdf_path.exists():
        return {
            "status": "UNAVAILABLE",
            "reason": "BRIDGE_POSITIONS_DEFERRED" if "bridge_positions" in deferred else "SHADOW_ARTIFACT_MISSING",
            "recognized_frames": None,
            "canonical_promotion_allowed": False,
        }
    if not summary_path.exists() or not jsonl_path.exists() or not pbn_path.exists() or not pdf_path.exists():
        raise EvidenceExportError("partial shadow card artifact set")
    summary = _read_regular_json(summary_path, max_bytes=1024 * 1024)
    if summary.get("profiled_challenger_enabled") is not True:
        raise EvidenceExportError("shadow summary is not from the profiled challenger")
    if summary.get("result_scope") != "SHADOW_ONLY" or summary.get("canonical_promotion_allowed") is not False:
        raise EvidenceExportError("shadow summary promotion boundary mismatch")
    summary_sha, summary_size = _sha256(summary_path, max_bytes=1024 * 1024)
    jsonl_sha, jsonl_size = _sha256(jsonl_path, max_bytes=MAX_SHADOW_BYTES)
    pbn_sha, pbn_size = _sha256(pbn_path, max_bytes=MAX_SHADOW_BYTES)
    pdf_sha, pdf_size = _sha256(pdf_path, max_bytes=MAX_SHADOW_BYTES)
    if (
        summary.get("pdf_output") != pdf_path.name
        or summary.get("pdf_sha256") != pdf_sha
        or type(summary.get("pdf_pages")) is not int
        or int(summary["pdf_pages"]) < 1
    ):
        raise EvidenceExportError("shadow PDF summary binding mismatch")
    try:
        raw_jsonl = jsonl_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise EvidenceExportError("invalid shadow JSONL") from exc
    if not raw_jsonl or not raw_jsonl.endswith("\n"):
        raise EvidenceExportError("shadow JSONL is empty or not canonically terminated")
    for line in raw_jsonl.splitlines():
        try:
            record = _strict_loads(line)
        except (json.JSONDecodeError, ValueError) as exc:
            raise EvidenceExportError("invalid shadow JSONL") from exc
        if not isinstance(record, dict) or _contains_promotion_true(record):
            raise EvidenceExportError("shadow record promotion boundary mismatch")
    try:
        raw_pbn = pbn_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise EvidenceExportError("invalid shadow PBN") from exc
    if (
        not raw_pbn.startswith("% PBN 2.1\n")
        or "% X-ResultScope: SHADOW_ONLY\n" not in raw_pbn
        or "% X-CanonicalPromotionAllowed: false\n" not in raw_pbn
        or 'X-CanonicalPromotionAllowed "true"' in raw_pbn
    ):
        raise EvidenceExportError("shadow PBN promotion boundary mismatch")
    try:
        raw_pdf = pdf_path.read_bytes()
        from pypdf import PdfReader

        pdf_reader = PdfReader(str(pdf_path))
    except OSError as exc:
        raise EvidenceExportError("invalid shadow PDF") from exc
    except Exception as exc:
        raise EvidenceExportError("shadow PDF cannot be reopened") from exc
    if (
        not raw_pdf.startswith(b"%PDF-")
        or b"SHADOW_ONLY" not in raw_pdf
        or b"CanonicalPromotionAllowed=false" not in raw_pdf
        or len(pdf_reader.pages) != summary["pdf_pages"]
        or (pdf_reader.metadata or {}).get("/Subject")
        != "SHADOW_ONLY; CanonicalPromotionAllowed=false"
    ):
        raise EvidenceExportError("shadow PDF promotion boundary mismatch")
    return {
        "status": "OBSERVED_SHADOW",
        "recognized_frames": int(summary.get("recognized_frames") or 0),
        "conflict_frames": int(summary.get("conflict_frames") or 0),
        "derived_fourth_hand_frames": int(summary.get("derived_fourth_hand_frames") or 0),
        "canonical_promotion_allowed": False,
        "artifacts": [
            {"locator": summary_path.name, "sha256": summary_sha, "size_bytes": summary_size},
            {"locator": jsonl_path.name, "sha256": jsonl_sha, "size_bytes": jsonl_size},
            {"locator": pbn_path.name, "sha256": pbn_sha, "size_bytes": pbn_size},
            {"locator": pdf_path.name, "sha256": pdf_sha, "size_bytes": pdf_size},
        ],
    }


def build_evidence_export(
    *,
    request_path: Path,
    status_path: Path,
    spool_root: Path,
    now: float | None = None,
) -> dict[str, Any]:
    """Validate and export one exact job using only fixed resident paths."""
    observed_now = float(time.time() if now is None else now)
    request = _validate_request(_read_regular_json(request_path, max_bytes=MAX_REQUEST_BYTES))
    status = _validate_status(_read_regular_json(status_path, max_bytes=MAX_STATUS_BYTES), now=observed_now)
    job_id = request["job_id"]
    result_dir = spool_root / "results" / job_id
    done_path = spool_root / "done" / f"{job_id}.json"
    try:
        result_dir.resolve().relative_to((spool_root / "results").resolve())
        done_path.resolve().parent.relative_to((spool_root / "done").resolve())
    except ValueError as exc:
        raise EvidenceExportError("fixed job path escaped spool") from exc
    done = _read_regular_json(done_path, max_bytes=MAX_DONE_BYTES)
    if done.get("receipt_version") != "universal-video-compute-receipt-v1":
        raise EvidenceExportError("unexpected done receipt version")
    if done.get("compute_status") != "COMPLETED" or done.get("job_id") != job_id:
        raise EvidenceExportError("done receipt is not exact completed job")
    runtime_attestation = _exact_runtime_attestation(status, request)
    if done.get("runtime_attestation") != runtime_attestation:
        raise EvidenceExportError("resident status attestation is not bound to done receipt")
    try:
        conformance = verify_result(
            result_dir,
            expected_job_id=job_id,
            expected_profile=request["profile"],
            expected_job_hash=request["job_hash"],
            expected_source_file_id=request["source_file_id"],
            evidence_phase="READ_ONLY_EXACT_JOB_EXPORT",
            require_server_review=True,
        )
    except ResultConformanceError as exc:
        raise EvidenceExportError("result bundle is unavailable or invalid") from exc
    prior = done.get("result_conformance")
    if not isinstance(prior, dict) or prior.get("artifact_set_sha256") != conformance["artifact_set_sha256"]:
        raise EvidenceExportError("done receipt/result bundle mismatch")

    manifest = _read_regular_json(result_dir / "manifest.json", max_bytes=256 * 1024)
    if manifest.get("processing_revision") != request["requested_runtime_commit"]:
        raise EvidenceExportError("manifest processing revision does not match requested runtime")
    if conformance.get("processing_revision") != request["requested_runtime_commit"]:
        raise EvidenceExportError("conformance processing revision does not match requested runtime")
    transcript_rows, transcript_sha, transcript_size = _transcript_rows(result_dir / "transcript.jsonl")
    deferred = list(conformance.get("deferred_analysis") or [])
    artifacts_by_name = {item["relative_name"]: item for item in conformance["artifacts"]}
    if (
        artifacts_by_name["transcript.jsonl"]["sha256"] != transcript_sha
        or artifacts_by_name["transcript.jsonl"]["size_bytes"] != transcript_size
    ):
        raise EvidenceExportError("transcript changed during export")
    asr_artifacts = [
        {
            "locator": name,
            "sha256": artifacts_by_name[name]["sha256"],
            "size_bytes": artifacts_by_name[name]["size_bytes"],
        }
        for name in ("transcript.jsonl", "transcript.txt", "transcript_qc.json")
    ]
    receipt = {
        "schema": EXPORT_SCHEMA,
        "state": "PASS",
        "exported_at_unix": observed_now,
        "job": {
            "job_id": job_id,
            "profile": request["profile"],
            "job_hash": request["job_hash"],
            "source_file_id": request["source_file_id"],
            "request_commit": request["request_commit"],
        },
        "runtime": {
            "requested_runtime_commit": request["requested_runtime_commit"],
            "installed_runtime_commit": status["installed_runtime_commit"],
            "observed_job_runtime_commit": runtime_attestation["observed_job_runtime_commit"],
            "binding": "OBSERVED_EXACT",
        },
        "technical": {
            "artifact_set_sha256": conformance["artifact_set_sha256"],
            "manifest_sha256": conformance["manifest_sha256"],
            "artifact_count": conformance["artifact_count"],
            "total_bytes": conformance["total_bytes"],
            "source_binding_status": conformance["source_binding_status"],
            "server_final_review_status": conformance["server_final_review_status"],
            "deferred_analysis": deferred,
            "canonical_promotion_allowed": False,
        },
        "asr_qc": {
            "status": "PASS",
            "duration_seconds": manifest["media"]["duration_seconds"],
            "segments": manifest["transcript"]["segments"],
            "words": manifest["transcript"]["words"],
            "qc_blocks": manifest["transcript"]["qc_blocks"],
            "qc_failed": manifest["transcript"]["qc_failed"],
            "qc_critical_failed": manifest["transcript"]["qc_critical_failed"],
            "qc_hallucination_blocks": manifest["transcript"]["qc_hallucination_blocks"],
            "artifacts": asr_artifacts,
        },
        "speakers": _speaker_summary(
            transcript_rows,
            deferred,
            result_dir=result_dir,
            artifacts_by_name=artifacts_by_name,
        ),
        "cards": _card_summary(result_dir, deferred),
        "publication_state": "NOT_PUBLISHED",
        "school_canon_changed": False,
    }
    encoded = json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_RECEIPT_BYTES:
        raise EvidenceExportError("sanitized receipt exceeds byte cap")
    return receipt


def main() -> None:
    request_path = Path("/var/lib/bridge-school/universal-video/evidence-export-request.json")
    status_path = Path("/run/bridge-school/universal-video-status.json")
    spool_root = Path("/opt/bridge-school/universal-video/spool")
    try:
        receipt = build_evidence_export(
            request_path=request_path,
            status_path=status_path,
            spool_root=spool_root,
        )
    except EvidenceExportError as exc:
        receipt = {
            "schema": EXPORT_SCHEMA,
            "state": "INCONCLUSIVE",
            "reason": str(exc).upper().replace(" ", "_")[:120],
            "publication_state": "NOT_PUBLISHED",
            "school_canon_changed": False,
        }
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
