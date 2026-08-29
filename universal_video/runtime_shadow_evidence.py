"""Fail-closed observability for the opt-in profiled card shadow path.

This module does not activate a detector.  It validates an explicit
programmatic activation context and emits a bounded per-job receipt proving
which code/profile/backend was requested and observed.  Missing authority or
provenance is represented as ``UNAVAILABLE`` and must prevent invocation.
"""
from __future__ import annotations

import hashlib
import json
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any


SCHEMA = "universal-video-runtime-shadow-attestation-v1"
RECEIPT_FILE = "bridge_positions_profiled_shadow_runtime.json"
SHADOW_OUTPUT_FILE = "bridge_positions_profiled_shadow.jsonl"
CANONICAL_OUTPUT_FILE = "bridge_positions.jsonl"
PROFILE_AUTHORITY = "APPROVED_HUMAN_REVIEW"
BACKEND_AUTHORITY = "APPROVED_VERSIONED_BACKEND"
PUBLICATION_STATE = "NOT_PUBLISHED"
MAX_RECEIPT_BYTES = 64 * 1024
MAX_SHADOW_OUTPUT_BYTES = 64 * 1024 * 1024

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
_CONTEXT_FIELDS = frozenset(
    {
        "schema",
        "request_commit",
        "requested_runtime_commit",
        "installed_runtime_commit",
        "observed_job_runtime_commit",
        "profile_id",
        "profile_hash",
        "profile_authority",
        "profile_authority_sha256",
        "backend_id",
        "backend_hash",
        "backend_authority",
    }
)
UNAVAILABLE_CODES = frozenset(
    {
        "ACTIVATION_CONTEXT_MISSING",
        "ACTIVATION_CONTEXT_UNKNOWN_FIELD",
        "BACKEND_AUTHORITY_MISSING",
        "BACKEND_HASH_INVALID",
        "BACKEND_ID_INVALID",
        "BACKEND_ID_MISMATCH",
        "BACKEND_NOT_BOUND",
        "CANONICAL_OUTPUT_CHANGED",
        "CHALLENGER_MISSING",
        "CHALLENGER_NOT_SHADOW_ONLY",
        "COMMIT_INVALID",
        "PROFILE_AUTHORITY_MISSING",
        "PROFILE_AUTHORITY_MISMATCH",
        "PROFILE_HASH_INVALID",
        "PROFILE_HASH_MISMATCH",
        "PROFILE_ID_INVALID",
        "PROFILE_ID_MISMATCH",
        "PROFILE_NOT_BOUND",
        "RUNTIME_COMMIT_MISMATCH",
        "SHADOW_OUTPUT_INVALID",
        "SHADOW_RECEIPT_MISSING",
        "SHADOW_SUMMARY_INVALID",
    }
)


class ShadowRuntimeEvidenceError(RuntimeError):
    """Bounded activation/attestation failure safe for an external receipt."""

    def __init__(self, code: str):
        if code not in UNAVAILABLE_CODES:
            raise ValueError("unsupported runtime evidence error code")
        self.code = code
        super().__init__(code)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _hex(value: Any, pattern: re.Pattern[str], code: str) -> str:
    text = str(value or "").strip().lower()
    if not pattern.fullmatch(text):
        raise ShadowRuntimeEvidenceError(code)
    return text


def _identifier(value: Any, code: str) -> str:
    text = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(text):
        raise ShadowRuntimeEvidenceError(code)
    return text


def _optional_hex(value: Any, pattern: re.Pattern[str]) -> str | None:
    text = str(value or "").strip().lower()
    return text if pattern.fullmatch(text) else None


def _optional_identifier(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if _IDENTIFIER.fullmatch(text) else None


def _base_receipt(context: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = context if isinstance(context, Mapping) else {}
    return {
        "schema": SCHEMA,
        "state": "UNAVAILABLE",
        "request_commit": _optional_hex(raw.get("request_commit"), _HEX40),
        "requested_runtime_commit": _optional_hex(raw.get("requested_runtime_commit"), _HEX40),
        "installed_runtime_commit": _optional_hex(raw.get("installed_runtime_commit"), _HEX40),
        "observed_job_runtime_commit": _optional_hex(raw.get("observed_job_runtime_commit"), _HEX40),
        "runtime_binding": "UNAVAILABLE",
        "profile_id": _optional_identifier(raw.get("profile_id")),
        "profile_hash": _optional_hex(raw.get("profile_hash"), _HEX64),
        "profile_authority": PROFILE_AUTHORITY if raw.get("profile_authority") == PROFILE_AUTHORITY else None,
        "profile_authority_sha256": _optional_hex(raw.get("profile_authority_sha256"), _HEX64),
        "backend_id": _optional_identifier(raw.get("backend_id")),
        "backend_hash": _optional_hex(raw.get("backend_hash"), _HEX64),
        "backend_authority": BACKEND_AUTHORITY if raw.get("backend_authority") == BACKEND_AUTHORITY else None,
        "challenger_invoked": False,
        "shadow_only": True,
        "shadow_output_locator": None,
        "shadow_output_sha256": None,
        "canonical_output_untouched": True,
        "canonical_promotion_allowed": False,
        "publication_state": PUBLICATION_STATE,
        "unavailable_reasons": [],
    }


def unavailable_receipt(
    context: Mapping[str, Any] | None,
    reasons: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    normalized = sorted(set(reasons))
    if not normalized or any(reason not in UNAVAILABLE_CODES for reason in normalized):
        raise ValueError("invalid unavailable reason")
    receipt = _base_receipt(context)
    receipt["unavailable_reasons"] = normalized[:16]
    return receipt


def validate_receipt(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a normalized runtime receipt or reject it fail closed.

    The resident status/export path consumes producer output through this
    validator instead of trusting a JSON-schema-shaped mapping.  It mirrors
    the checked-in schema while keeping the runtime dependency-free.
    """

    if not isinstance(value, Mapping) or set(value) != {
        "schema",
        "state",
        "request_commit",
        "requested_runtime_commit",
        "installed_runtime_commit",
        "observed_job_runtime_commit",
        "runtime_binding",
        "profile_id",
        "profile_hash",
        "profile_authority",
        "profile_authority_sha256",
        "backend_id",
        "backend_hash",
        "backend_authority",
        "challenger_invoked",
        "shadow_only",
        "shadow_output_locator",
        "shadow_output_sha256",
        "canonical_output_untouched",
        "canonical_promotion_allowed",
        "publication_state",
        "unavailable_reasons",
    }:
        raise ValueError("invalid runtime shadow attestation shape")
    receipt = dict(value)
    if receipt.get("schema") != SCHEMA or receipt.get("state") not in {"UNAVAILABLE", "OBSERVED"}:
        raise ValueError("invalid runtime shadow attestation identity")
    if (
        receipt.get("shadow_only") is not True
        or receipt.get("canonical_output_untouched") is not True
        or receipt.get("canonical_promotion_allowed") is not False
        or receipt.get("publication_state") != PUBLICATION_STATE
    ):
        raise ValueError("invalid runtime shadow safety boundary")

    for field in (
        "request_commit",
        "requested_runtime_commit",
        "installed_runtime_commit",
        "observed_job_runtime_commit",
    ):
        if receipt[field] is not None and not _HEX40.fullmatch(str(receipt[field])):
            raise ValueError(f"invalid runtime shadow field: {field}")
    for field in ("profile_hash", "profile_authority_sha256", "backend_hash", "shadow_output_sha256"):
        if receipt[field] is not None and not _HEX64.fullmatch(str(receipt[field])):
            raise ValueError(f"invalid runtime shadow field: {field}")
    for field in ("profile_id", "backend_id"):
        if receipt[field] is not None and not _IDENTIFIER.fullmatch(str(receipt[field])):
            raise ValueError(f"invalid runtime shadow field: {field}")
    if receipt["profile_authority"] not in {None, PROFILE_AUTHORITY}:
        raise ValueError("invalid runtime shadow profile authority")
    if receipt["backend_authority"] not in {None, BACKEND_AUTHORITY}:
        raise ValueError("invalid runtime shadow backend authority")
    reasons = receipt.get("unavailable_reasons")
    if (
        not isinstance(reasons, list)
        or len(reasons) > 16
        or len(reasons) != len(set(reasons))
        or reasons != sorted(reasons)
        or any(reason not in UNAVAILABLE_CODES for reason in reasons)
    ):
        raise ValueError("invalid runtime shadow unavailable reasons")

    if receipt["state"] == "OBSERVED":
        required = (
            "request_commit",
            "requested_runtime_commit",
            "installed_runtime_commit",
            "observed_job_runtime_commit",
            "profile_id",
            "profile_hash",
            "profile_authority_sha256",
            "backend_id",
            "backend_hash",
            "shadow_output_sha256",
        )
        if any(receipt[field] is None for field in required):
            raise ValueError("observed runtime shadow attestation is incomplete")
        if len(
            {
                receipt["requested_runtime_commit"],
                receipt["installed_runtime_commit"],
                receipt["observed_job_runtime_commit"],
            }
        ) != 1:
            raise ValueError("observed runtime shadow commit mismatch")
        if (
            receipt["runtime_binding"] != "PASS"
            or receipt["profile_authority"] != PROFILE_AUTHORITY
            or receipt["backend_authority"] != BACKEND_AUTHORITY
            or receipt["challenger_invoked"] is not True
            or receipt["shadow_output_locator"] != SHADOW_OUTPUT_FILE
            or reasons
        ):
            raise ValueError("invalid observed runtime shadow attestation")
    elif (
        receipt["runtime_binding"] != "UNAVAILABLE"
        or receipt["challenger_invoked"] is not False
        or receipt["shadow_output_locator"] is not None
        or receipt["shadow_output_sha256"] is not None
        or not reasons
    ):
        raise ValueError("invalid unavailable runtime shadow attestation")
    return receipt


def write_receipt(job_dir: Path, receipt: Mapping[str, Any]) -> tuple[str, str]:
    root = job_dir.resolve()
    path = root / RECEIPT_FILE
    payload = json.dumps(validate_receipt(receipt), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if len(payload.encode("utf-8")) > MAX_RECEIPT_BYTES:
        raise ShadowRuntimeEvidenceError("SHADOW_OUTPUT_INVALID")
    try:
        info = path.lstat()
    except FileNotFoundError:
        pass
    else:
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ShadowRuntimeEvidenceError("SHADOW_OUTPUT_INVALID")
    path.write_text(payload, encoding="utf-8")
    return path.name, _sha256(path)


def validate_activation_context(
    context: Mapping[str, Any] | None,
    challenger: Any,
) -> dict[str, Any]:
    """Validate the explicit gate before the challenger can see a frame."""

    if not isinstance(context, Mapping):
        raise ShadowRuntimeEvidenceError("ACTIVATION_CONTEXT_MISSING")
    if set(context) != _CONTEXT_FIELDS or context.get("schema") != SCHEMA:
        raise ShadowRuntimeEvidenceError("ACTIVATION_CONTEXT_UNKNOWN_FIELD")
    if challenger is None:
        raise ShadowRuntimeEvidenceError("CHALLENGER_MISSING")
    if getattr(challenger, "shadow_only", None) is not True:
        raise ShadowRuntimeEvidenceError("CHALLENGER_NOT_SHADOW_ONLY")

    request_commit = _hex(context.get("request_commit"), _HEX40, "COMMIT_INVALID")
    requested = _hex(context.get("requested_runtime_commit"), _HEX40, "COMMIT_INVALID")
    installed = _hex(context.get("installed_runtime_commit"), _HEX40, "COMMIT_INVALID")
    observed = _hex(context.get("observed_job_runtime_commit"), _HEX40, "COMMIT_INVALID")
    if len({requested, installed, observed}) != 1:
        raise ShadowRuntimeEvidenceError("RUNTIME_COMMIT_MISMATCH")

    profile_id = _identifier(context.get("profile_id"), "PROFILE_ID_INVALID")
    profile_hash = _hex(context.get("profile_hash"), _HEX64, "PROFILE_HASH_INVALID")
    authority_hash = _hex(
        context.get("profile_authority_sha256"),
        _HEX64,
        "PROFILE_AUTHORITY_MISSING",
    )
    if context.get("profile_authority") != PROFILE_AUTHORITY:
        raise ShadowRuntimeEvidenceError("PROFILE_AUTHORITY_MISSING")
    profile = getattr(challenger, "profile", None)
    recognizer_view = getattr(profile, "recognizer_view", None)
    if profile is None or not callable(recognizer_view):
        raise ShadowRuntimeEvidenceError("PROFILE_NOT_BOUND")
    if getattr(profile, "profile_id", None) != profile_id:
        raise ShadowRuntimeEvidenceError("PROFILE_ID_MISMATCH")
    if _fingerprint(recognizer_view()) != profile_hash:
        raise ShadowRuntimeEvidenceError("PROFILE_HASH_MISMATCH")
    if getattr(profile, "verification_sha256", None) != authority_hash:
        raise ShadowRuntimeEvidenceError("PROFILE_AUTHORITY_MISMATCH")

    backend_id = _identifier(context.get("backend_id"), "BACKEND_ID_INVALID")
    backend_hash = _hex(context.get("backend_hash"), _HEX64, "BACKEND_HASH_INVALID")
    if context.get("backend_authority") != BACKEND_AUTHORITY:
        raise ShadowRuntimeEvidenceError("BACKEND_AUTHORITY_MISSING")
    if not getattr(challenger, "backend_id", None) or not getattr(challenger, "backend_sha256", None):
        raise ShadowRuntimeEvidenceError("BACKEND_NOT_BOUND")
    if challenger.backend_id != backend_id or challenger.backend_sha256 != backend_hash:
        raise ShadowRuntimeEvidenceError("BACKEND_ID_MISMATCH")

    return {
        "schema": SCHEMA,
        "request_commit": request_commit,
        "requested_runtime_commit": requested,
        "installed_runtime_commit": installed,
        "observed_job_runtime_commit": observed,
        "profile_id": profile_id,
        "profile_hash": profile_hash,
        "profile_authority": PROFILE_AUTHORITY,
        "profile_authority_sha256": authority_hash,
        "backend_id": backend_id,
        "backend_hash": backend_hash,
        "backend_authority": BACKEND_AUTHORITY,
    }


def file_snapshot(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"exists": False}
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ShadowRuntimeEvidenceError("CANONICAL_OUTPUT_CHANGED")
    return {"exists": True, "size": info.st_size, "sha256": _sha256(path)}


def observed_receipt(
    job_dir: Path,
    context: Mapping[str, Any],
    summary: Mapping[str, Any],
    canonical_before: Mapping[str, Any],
) -> dict[str, Any]:
    """Finalize a receipt after the explicitly gated shadow path returns."""

    root = job_dir.resolve()
    shadow_path = root / SHADOW_OUTPUT_FILE
    try:
        info = shadow_path.lstat()
    except OSError as exc:
        raise ShadowRuntimeEvidenceError("SHADOW_OUTPUT_INVALID") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ShadowRuntimeEvidenceError("SHADOW_OUTPUT_INVALID")
    if not 0 < info.st_size <= MAX_SHADOW_OUTPUT_BYTES:
        raise ShadowRuntimeEvidenceError("SHADOW_OUTPUT_INVALID")
    if (
        summary.get("result_scope") != "SHADOW_ONLY"
        or summary.get("profiled_challenger_enabled") is not True
        or summary.get("canonical_promotion_allowed") is not False
        or summary.get("output") != SHADOW_OUTPUT_FILE
    ):
        raise ShadowRuntimeEvidenceError("SHADOW_SUMMARY_INVALID")
    if file_snapshot(root / CANONICAL_OUTPUT_FILE) != dict(canonical_before):
        raise ShadowRuntimeEvidenceError("CANONICAL_OUTPUT_CHANGED")

    receipt = _base_receipt(context)
    receipt.update(
        {
            "state": "OBSERVED",
            "runtime_binding": "PASS",
            "challenger_invoked": True,
            "shadow_output_locator": SHADOW_OUTPUT_FILE,
            "shadow_output_sha256": _sha256(shadow_path),
            "unavailable_reasons": [],
        }
    )
    return receipt


__all__ = [
    "BACKEND_AUTHORITY",
    "CANONICAL_OUTPUT_FILE",
    "PROFILE_AUTHORITY",
    "RECEIPT_FILE",
    "SCHEMA",
    "SHADOW_OUTPUT_FILE",
    "ShadowRuntimeEvidenceError",
    "file_snapshot",
    "observed_receipt",
    "unavailable_receipt",
    "validate_receipt",
    "validate_activation_context",
    "write_receipt",
]
