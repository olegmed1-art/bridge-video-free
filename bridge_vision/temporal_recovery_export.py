"""Lossless handoff to the PBN comment seam and reviewed R3 sidecar ABI.

No PBN serialization, qualification or promotion. Native admission is exclusively
the receiving recovery_pbn.load_reviewed_recovery API with independent pins.
"""
from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
import json
import re

from bridge_vision.original_deal_solver import RecoveryInputError
from bridge_vision.temporal_deal_recovery import TEMPORAL_VERSION


def installed_row_sha256(row: Mapping) -> str:
    if not isinstance(row, Mapping):
        raise RecoveryInputError("ROW_NOT_MAPPING")
    try:
        raw = json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise RecoveryInputError("ROW_NOT_CANONICAL_JSON") from exc
    return sha256(raw.encode("utf-8")).hexdigest()


def reconstruction_by_row_sha256(row, temporal):
    """Require an independent exact-row mapping; never attach by timestamp alone."""
    key = installed_row_sha256(row)
    if not isinstance(temporal, Mapping) or temporal.get("version") != TEMPORAL_VERSION:
        raise RecoveryInputError("TEMPORAL_ENVELOPE_REQUIRED")
    scopes = temporal.get("scopes")
    if not isinstance(scopes, list):
        raise RecoveryInputError("TEMPORAL_SCOPES_INVALID")
    if any(not isinstance(scope, Mapping) or not isinstance(scope.get("review_receipts"), Mapping)
           or any(not isinstance(r, Mapping) for r in scope["review_receipts"].values()) for scope in scopes):
        raise RecoveryInputError("TEMPORAL_SCOPE_RECEIPTS_INVALID")
    selected = [scope for scope in scopes if any(
        receipt.get("input_row_sha256") == key
        and isinstance(receipt.get("input_row_binding_ref"), str) and receipt["input_row_binding_ref"].strip()
        for receipt in scope.get("review_receipts", {}).values())]
    if len(selected) != 1:
        raise RecoveryInputError("EXACT_ROW_REVIEW_BINDING_REQUIRED")
    scope = selected[0]
    return {key: {"schema": "bridge-temporal-recovery-export-v1", "input_row_sha256": key,
                  "status": scope["status"], "scope": deepcopy(scope),
                  "temporal_status": temporal["status"],
                  "canonical_promotion_allowed": False, "native_pbn_qualified": False}}


def reviewed_pbn_handoff(row, temporal, *, producer_pins):
    """Prepare bytes-independent transport from a direct producer result.

    Call immediately on the pinned producer's return value, never on an external
    sidecar claim. The caller must independently verify source pins and capture
    this result digest. Nothing here authenticates those inputs or a reviewer.
    Keep the complete result unchanged; multi-scope results cannot use R3 native
    admission. Unsupported derivations remain present for consumer ABI review.
    """
    key = installed_row_sha256(row)
    reconstruction_by_row_sha256(row, temporal)  # exact independent row receipt
    if len(temporal["scopes"]) != 1:
        raise RecoveryInputError("PBN_R3_REQUIRES_SINGLE_COMPLETE_SCOPE")
    required = {"bridge_vision/temporal_deal_recovery.py", "bridge_vision/original_deal_solver.py"}
    if (not isinstance(producer_pins, Mapping) or not required <= set(producer_pins)
            or any(not isinstance(k, str) or not isinstance(v, str)
                   or not re.fullmatch(r"[0-9a-f]{64}", v) for k, v in producer_pins.items())):
        raise RecoveryInputError("PRODUCER_PINS_REQUIRED")
    scope = temporal["scopes"][0]
    receipts = [r for r in scope["review_receipts"].values() if r.get("input_row_sha256") == key]
    if len(receipts) != 1:
        raise RecoveryInputError("PBN_R3_REQUIRES_ONE_TARGET_EVENT")
    target = {k: receipts[0].get(k) for k in (
        "input_row_sha256", "source_video_sha256", "frame_sha256", "timestamp_ms")}
    if (any(not isinstance(target[k], str) or not re.fullmatch(r"[0-9a-f]{64}", target[k])
            for k in ("input_row_sha256", "source_video_sha256", "frame_sha256"))
            or type(target["timestamp_ms"]) is not int or target["timestamp_ms"] < 0):
        raise RecoveryInputError("PBN_TARGET_TYPES")
    captured = deepcopy(temporal)
    try:
        digest = sha256(json.dumps(captured, ensure_ascii=True, sort_keys=True,
                                  separators=(",", ":"), allow_nan=False).encode("ascii")).hexdigest()
    except (TypeError, ValueError) as exc:
        raise RecoveryInputError("TEMPORAL_NOT_CANONICAL_JSON") from exc
    unsupported = sorted({f.get("source", "VISUAL") for f in scope.get("facts", [])
                          if f.get("source", "VISUAL") not in ("VISUAL", "TEACHER_SPEECH")})
    reasons = ["R3_UNSUPPORTED_DERIVED_PREMISE:" + source for source in unsupported]
    if any(f.get("zone_type") is not None for f in scope.get("facts", [])):
        reasons.append("R3_PLAYED_ZONE_OWNERSHIP_ABI_REQUIRES_EXTENSION")
    if scope.get("status") not in ("VERIFIED_PARTIAL", "VERIFIED_FULL"):
        reasons.append("R3_REQUIRES_CONSISTENT_RECOVERY")
    return {"sidecar": {"schema": "bridge-reviewed-ownership-sidecar-v1", "row_sha256": key,
                        "producer_pins": dict(producer_pins), "temporal_recovery": captured},
            "target_binding": target, "producer_result_sha256": digest,
            "native_pbn_qualified": False, "consumer_r3_limitations": reasons}
