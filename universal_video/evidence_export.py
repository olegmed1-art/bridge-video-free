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
import os
import re
import stat
import time
from pathlib import Path
from typing import Any

from .result_conformance import ResultConformanceError, verify_result
from .runtime_shadow_evidence import (
    MAX_RECEIPT_BYTES as MAX_RUNTIME_RECEIPT_BYTES,
    RECEIPT_FILE as RUNTIME_RECEIPT_FILE,
    SHADOW_OUTPUT_FILE,
    validate_receipt as validate_runtime_shadow_receipt,
)

EXPORT_SCHEMA = "universal-video-evidence-export-v1"
REQUEST_SCHEMA = "universal-video-evidence-export-request-v1"
STATUS_SCHEMAS = frozenset({
    "universal-video-resident-status-v1",
    "universal-video-resident-status-v2",
    "universal-video-resident-status-v3",
})
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
ALLOWED_STATUS_FIELDS_V1 = frozenset({"schema", "instance_state", "active_jobs", "observed_at_unix"})
ALLOWED_STATUS_FIELDS_V2 = ALLOWED_STATUS_FIELDS_V1 | frozenset({"installed_runtime_commit", "job_attestations"})
ALLOWED_STATUS_FIELDS_V3 = ALLOWED_STATUS_FIELDS_V1 | frozenset({"exporter_commit", "job_attestations"})
ALLOWED_ATTESTATION_FIELDS = frozenset({
    "schema", "job_id", "request_commit", "requested_runtime_commit",
    "installed_runtime_commit", "observed_job_runtime_commit", "profile",
    "job_hash", "source_file_id", "canonical_output_untouched",
    "canonical_promotion_allowed", "publication_state",
})
ALLOWED_ATTESTATION_FIELDS_V2 = frozenset({
    "schema", "job_id", "request_commit", "requested_runtime_commit",
    "observed_job_runtime_commit", "processing_revision", "profile",
    "job_hash", "source_file_id", "runtime_shadow_attestation",
    "canonical_output_untouched", "canonical_promotion_allowed",
    "publication_state",
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
    schema = status.get("schema")
    if schema == "universal-video-resident-status-v3":
        allowed = ALLOWED_STATUS_FIELDS_V3
    elif schema == "universal-video-resident-status-v2":
        allowed = ALLOWED_STATUS_FIELDS_V2
    else:
        allowed = ALLOWED_STATUS_FIELDS_V1
    if schema not in STATUS_SCHEMAS or set(status) != allowed:
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
    installed = None
    exporter_commit = None
    attestations: list[dict[str, Any]] = []
    if schema == "universal-video-resident-status-v2":
        installed = _hex(status.get("installed_runtime_commit"), width=40, field="installed_runtime_commit")
        raw = status.get("job_attestations")
        if not isinstance(raw, list) or len(raw) > 32 or any(not isinstance(item, dict) for item in raw):
            raise EvidenceExportError("invalid job attestations")
        for item in raw:
            if set(item) != ALLOWED_ATTESTATION_FIELDS or item.get("schema") != "universal-video-runtime-job-attestation-v1":
                raise EvidenceExportError("invalid job attestation shape")
        attestations = list(raw)
    elif schema == "universal-video-resident-status-v3":
        exporter_commit = _hex(status.get("exporter_commit"), width=40, field="exporter_commit")
        raw = status.get("job_attestations")
        if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict):
            raise EvidenceExportError("invalid v3 job attestations")
        item = raw[0]
        if set(item) != ALLOWED_ATTESTATION_FIELDS_V2 or item.get("schema") != "universal-video-runtime-job-attestation-v2":
            raise EvidenceExportError("invalid v3 job attestation shape")
        try:
            shadow = validate_runtime_shadow_receipt(item.get("runtime_shadow_attestation"))
        except ValueError as exc:
            raise EvidenceExportError("invalid runtime shadow attestation") from exc
        normalized = dict(item)
        normalized["runtime_shadow_attestation"] = shadow
        attestations = [normalized]
    return {
        "schema": schema,
        "observed_at_unix": observed_number,
        "installed_runtime_commit": installed,
        "exporter_commit": exporter_commit,
        "job_attestations": attestations,
    }


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


def _present(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _speaker_summary(rows: list[dict[str, Any]], deferred: list[str]) -> dict[str, Any]:
    labels = [str(row.get("speaker")) for row in rows if row.get("speaker") not in (None, "")]
    roles = [str(row.get("speaker_role") or row.get("speaker_role_candidate")) for row in rows if row.get("speaker_role") or row.get("speaker_role_candidate")]
    if not labels:
        return {
            "status": "UNAVAILABLE",
            "reason": "SPEAKER_LABELS_MISSING" if "speaker_structure" in deferred else "SPEAKER_EVIDENCE_MISSING",
            "speaker_count": None,
            "labeled_segments": 0,
            "unlabeled_segments": len(rows),
            "collapse": "NOT_COMPUTABLE_WITHOUT_HUMAN_REFERENCE",
            "fragmentation": "NOT_COMPUTABLE_WITHOUT_HUMAN_REFERENCE",
            "teacher_student_attribution": "UNAVAILABLE",
        }
    return {
        "status": "OBSERVED_ANONYMOUS_LABELS",
        "speaker_count": len(set(labels)),
        "speaker_labels": sorted(set(labels)),
        "labeled_segments": len(labels),
        "unlabeled_segments": len(rows) - len(labels),
        "collapse": "NOT_COMPUTABLE_WITHOUT_HUMAN_REFERENCE",
        "fragmentation": "NOT_COMPUTABLE_WITHOUT_HUMAN_REFERENCE",
        "teacher_student_attribution": "SUGGESTION_ONLY" if roles else "UNAVAILABLE",
        "role_candidates": sorted(set(roles)),
    }


def _card_summary(
    result_dir: Path,
    deferred: list[str],
    runtime_shadow: dict[str, Any],
) -> dict[str, Any]:
    summary_path = result_dir / "bridge_positions_profiled_shadow_summary.json"
    jsonl_path = result_dir / "bridge_positions_profiled_shadow.jsonl"
    runtime_path = result_dir / RUNTIME_RECEIPT_FILE
    if runtime_shadow["state"] == "UNAVAILABLE":
        if _present(summary_path) or _present(jsonl_path):
            raise EvidenceExportError("unavailable shadow attestation has artifacts")
        runtime_artifacts = []
        if _present(runtime_path):
            runtime_file = _read_regular_json(runtime_path, max_bytes=MAX_RUNTIME_RECEIPT_BYTES)
            try:
                observed_runtime = validate_runtime_shadow_receipt(runtime_file)
            except ValueError as exc:
                raise EvidenceExportError("invalid runtime shadow receipt file") from exc
            if observed_runtime != runtime_shadow:
                raise EvidenceExportError("runtime shadow status/result mismatch")
            runtime_sha, runtime_size = _sha256(runtime_path, max_bytes=MAX_RUNTIME_RECEIPT_BYTES)
            runtime_artifacts.append(
                {"locator": runtime_path.name, "sha256": runtime_sha, "size_bytes": runtime_size}
            )
        return {
            "status": "UNAVAILABLE",
            "reason": runtime_shadow["unavailable_reasons"],
            "recognized_frames": None,
            "profile_id": runtime_shadow["profile_id"],
            "backend_id": runtime_shadow["backend_id"],
            "challenger_invoked": False,
            "canonical_promotion_allowed": False,
            "artifacts": runtime_artifacts,
        }
    if not _present(summary_path) or not _present(jsonl_path) or not _present(runtime_path):
        raise EvidenceExportError("partial shadow card artifact set")
    runtime_file = _read_regular_json(runtime_path, max_bytes=MAX_RUNTIME_RECEIPT_BYTES)
    try:
        observed_runtime = validate_runtime_shadow_receipt(runtime_file)
    except ValueError as exc:
        raise EvidenceExportError("invalid runtime shadow receipt file") from exc
    if observed_runtime != runtime_shadow:
        raise EvidenceExportError("runtime shadow status/result mismatch")
    summary = _read_regular_json(summary_path, max_bytes=1024 * 1024)
    if summary.get("profiled_challenger_enabled") is not True:
        raise EvidenceExportError("shadow summary is not from the profiled challenger")
    if summary.get("result_scope") != "SHADOW_ONLY" or summary.get("canonical_promotion_allowed") is not False:
        raise EvidenceExportError("shadow summary promotion boundary mismatch")
    runtime_sha, runtime_size = _sha256(runtime_path, max_bytes=MAX_RUNTIME_RECEIPT_BYTES)
    if (
        summary.get("runtime_evidence_receipt") != RUNTIME_RECEIPT_FILE
        or summary.get("runtime_evidence_receipt_sha256") != runtime_sha
        or summary.get("output") != SHADOW_OUTPUT_FILE
    ):
        raise EvidenceExportError("shadow summary runtime binding mismatch")
    summary_sha, summary_size = _sha256(summary_path, max_bytes=1024 * 1024)
    jsonl_sha, jsonl_size = _sha256(jsonl_path, max_bytes=MAX_SHADOW_BYTES)
    if jsonl_sha != runtime_shadow["shadow_output_sha256"]:
        raise EvidenceExportError("shadow output attestation mismatch")
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
    return {
        "status": "OBSERVED_SHADOW",
        "profile_id": runtime_shadow["profile_id"],
        "profile_hash": runtime_shadow["profile_hash"],
        "profile_authority": runtime_shadow["profile_authority"],
        "profile_authority_sha256": runtime_shadow["profile_authority_sha256"],
        "backend_id": runtime_shadow["backend_id"],
        "backend_hash": runtime_shadow["backend_hash"],
        "backend_authority": runtime_shadow["backend_authority"],
        "challenger_invoked": True,
        "recognized_frames": int(summary.get("recognized_frames") or 0),
        "conflict_frames": int(summary.get("conflict_frames") or 0),
        "derived_fourth_hand_frames": int(summary.get("derived_fourth_hand_frames") or 0),
        "canonical_promotion_allowed": False,
        "artifacts": [
            {"locator": runtime_path.name, "sha256": runtime_sha, "size_bytes": runtime_size},
            {"locator": summary_path.name, "sha256": summary_sha, "size_bytes": summary_size},
            {"locator": jsonl_path.name, "sha256": jsonl_sha, "size_bytes": jsonl_size},
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
    processing_revision = _hex(
        manifest.get("processing_revision"), width=40, field="processing_revision"
    )
    if processing_revision != request["requested_runtime_commit"]:
        raise EvidenceExportError("manifest runtime revision mismatch")
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
    if status["schema"] != "universal-video-resident-status-v3":
        raise EvidenceExportError("exact runtime attestation unavailable")
    attestation = status["job_attestations"][0]
    if not (
        attestation.get("job_id") == job_id
        and attestation.get("request_commit") == request["request_commit"]
        and attestation.get("requested_runtime_commit") == request["requested_runtime_commit"]
        and attestation.get("observed_job_runtime_commit") == processing_revision
        and attestation.get("processing_revision") == processing_revision
        and attestation.get("profile") == request["profile"]
        and attestation.get("job_hash") == request["job_hash"]
        and attestation.get("source_file_id") == request["source_file_id"]
        and attestation.get("canonical_output_untouched") is True
        and attestation.get("canonical_promotion_allowed") is False
        and attestation.get("publication_state") == "NOT_PUBLISHED"
    ):
        raise EvidenceExportError("exact runtime attestation mismatch")
    runtime_shadow = attestation["runtime_shadow_attestation"]

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
            "observed_job_runtime_commit": processing_revision,
            "exporter_commit": status["exporter_commit"],
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
        "speakers": _speaker_summary(transcript_rows, deferred),
        "cards": _card_summary(result_dir, deferred, runtime_shadow),
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
