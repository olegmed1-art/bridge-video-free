"""Build a fresh, exact-job resident status for bounded evidence export.

The snapshot reads only fixed local control/result paths.  It never starts a
service, submits work, contacts a network, or changes a result bundle.
"""
from __future__ import annotations

import json
import re
import stat
import time
from pathlib import Path
from typing import Any

from .evidence_export import (
    MAX_DONE_BYTES,
    MAX_REQUEST_BYTES,
    EvidenceExportError,
    _read_regular_json,
    _validate_request,
)
from .runtime_shadow_evidence import (
    RECEIPT_FILE,
    SHADOW_OUTPUT_FILE,
    unavailable_receipt,
    validate_receipt,
)


STATUS_SCHEMA = "universal-video-resident-status-v3"
JOB_ATTESTATION_SCHEMA = "universal-video-runtime-job-attestation-v2"
STATUS_PATH = Path("/run/bridge-school/universal-video-status.json")
REQUEST_PATH = Path("/var/lib/bridge-school/universal-video/evidence-export-request.json")
SPOOL_ROOT = Path("/opt/bridge-school/universal-video/spool")
EXPORTER_PIN_PATH = Path("/etc/bridge-school/universal-video-admin-source-commit")
MAX_MANIFEST_BYTES = 256 * 1024
MAX_PIN_BYTES = 64
MAX_STATUS_BYTES = 16 * 1024
HEX40 = re.compile(r"^[0-9a-f]{40}$")


def _present(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _read_pin(path: Path) -> str:
    try:
        info = path.lstat()
    except OSError as exc:
        raise EvidenceExportError("missing exporter revision pin") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_PIN_BYTES:
        raise EvidenceExportError("unsafe exporter revision pin")
    try:
        value = path.read_text(encoding="ascii").strip().lower()
    except (OSError, UnicodeError) as exc:
        raise EvidenceExportError("invalid exporter revision pin") from exc
    if not HEX40.fullmatch(value):
        raise EvidenceExportError("invalid exporter revision pin")
    return value


def _runtime_shadow(result_dir: Path, request: dict[str, Any], observed_runtime: str) -> dict[str, Any]:
    receipt_path = result_dir / RECEIPT_FILE
    shadow_path = result_dir / SHADOW_OUTPUT_FILE
    summary_path = result_dir / "bridge_positions_profiled_shadow_summary.json"
    if not _present(receipt_path):
        if _present(shadow_path) or _present(summary_path):
            raise EvidenceExportError("partial unattested shadow evidence")
        return unavailable_receipt(
            {
                "request_commit": request["request_commit"],
                "requested_runtime_commit": request["requested_runtime_commit"],
                "observed_job_runtime_commit": observed_runtime,
            },
            ["SHADOW_RECEIPT_MISSING"],
        )
    try:
        receipt = validate_receipt(_read_regular_json(receipt_path, max_bytes=64 * 1024))
    except ValueError as exc:
        raise EvidenceExportError("invalid runtime shadow attestation") from exc
    if receipt["state"] == "OBSERVED":
        if not _present(shadow_path) or not _present(summary_path):
            raise EvidenceExportError("partial observed shadow evidence")
        if (
            receipt["request_commit"] != request["request_commit"]
            or receipt["requested_runtime_commit"] != request["requested_runtime_commit"]
            or receipt["observed_job_runtime_commit"] != observed_runtime
        ):
            raise EvidenceExportError("runtime shadow request binding mismatch")
    elif _present(shadow_path) or _present(summary_path):
        raise EvidenceExportError("unavailable attestation has shadow artifacts")
    return receipt


def build_resident_status(
    *,
    request_path: Path,
    spool_root: Path,
    exporter_commit: str,
    now: float | None = None,
) -> dict[str, Any]:
    """Return one fresh v3 snapshot bound to a terminal exact job."""
    if not HEX40.fullmatch(str(exporter_commit)):
        raise EvidenceExportError("invalid exporter commit")
    running = spool_root / "running"
    if running.exists():
        if running.is_symlink() or not running.is_dir():
            raise EvidenceExportError("unsafe running spool")
        if any(path.is_file() and path.suffix == ".json" for path in running.iterdir()):
            raise EvidenceExportError("resident has an active job")

    request = _validate_request(_read_regular_json(request_path, max_bytes=MAX_REQUEST_BYTES))
    job_id = request["job_id"]
    result_dir = spool_root / "results" / job_id
    done_path = spool_root / "done" / f"{job_id}.json"
    try:
        result_info = result_dir.lstat()
    except OSError as exc:
        raise EvidenceExportError("result directory missing") from exc
    if stat.S_ISLNK(result_info.st_mode) or not stat.S_ISDIR(result_info.st_mode):
        raise EvidenceExportError("unsafe result directory")
    done = _read_regular_json(done_path, max_bytes=MAX_DONE_BYTES)
    manifest = _read_regular_json(result_dir / "manifest.json", max_bytes=MAX_MANIFEST_BYTES)
    if (
        done.get("receipt_version") != "universal-video-compute-receipt-v1"
        or done.get("compute_status") != "COMPLETED"
        or done.get("job_id") != job_id
        or manifest.get("status") != "COMPLETED"
        or manifest.get("job_id") != job_id
        or manifest.get("profile") != request["profile"]
        or manifest.get("job_hash") != request["job_hash"]
    ):
        raise EvidenceExportError("terminal exact-job binding mismatch")
    source = manifest.get("source")
    if not isinstance(source, dict) or source.get("kind") != "google_drive" or source.get("file_id") != request["source_file_id"]:
        raise EvidenceExportError("source binding mismatch")
    processing_revision = str(manifest.get("processing_revision") or "").lower()
    runtime_revision = str((manifest.get("runtime") or {}).get("source_revision") or "").lower()
    metadata_revision = str((manifest.get("metadata") or {}).get("requested_runtime_commit") or "").lower()
    if (
        not HEX40.fullmatch(processing_revision)
        or processing_revision != runtime_revision
        or processing_revision != metadata_revision
        or processing_revision != request["requested_runtime_commit"]
    ):
        raise EvidenceExportError("job runtime revision binding mismatch")

    shadow = _runtime_shadow(result_dir, request, processing_revision)
    status = {
        "schema": STATUS_SCHEMA,
        "instance_state": "RUNNING",
        "active_jobs": [],
        "observed_at_unix": float(time.time() if now is None else now),
        "exporter_commit": exporter_commit,
        "job_attestations": [
            {
                "schema": JOB_ATTESTATION_SCHEMA,
                "job_id": job_id,
                "request_commit": request["request_commit"],
                "requested_runtime_commit": request["requested_runtime_commit"],
                "observed_job_runtime_commit": processing_revision,
                "processing_revision": processing_revision,
                "profile": request["profile"],
                "job_hash": request["job_hash"],
                "source_file_id": request["source_file_id"],
                "runtime_shadow_attestation": shadow,
                "canonical_output_untouched": True,
                "canonical_promotion_allowed": False,
                "publication_state": "NOT_PUBLISHED",
            }
        ],
    }
    rendered = json.dumps(status, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(rendered) > MAX_STATUS_BYTES:
        raise EvidenceExportError("resident status exceeds byte cap")
    return status


def main() -> None:
    status = build_resident_status(
        request_path=REQUEST_PATH,
        spool_root=SPOOL_ROOT,
        exporter_commit=_read_pin(EXPORTER_PIN_PATH),
    )
    print(json.dumps(status, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
