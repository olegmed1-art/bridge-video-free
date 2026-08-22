"""Pure contracts for the isolated Assistant Lab v1 runtime."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Mapping

CONTRACT_VERSION = "assistant-lab-v1"


class LabContractError(ValueError):
    """Raised when a lab job violates the bounded v1 contract."""


class Priority(IntEnum):
    INTERACTIVE = 0
    REGRESSION = 10
    EXPERIMENT = 20
    BACKGROUND = 30


ALLOWED_KINDS = frozenset({"DDS3_COMPUTE", "NOOP"})
ALLOWED_DDS3_OPERATIONS = frozenset({"dd_table", "position_all_moves", "position_trajectory"})


@dataclass(frozen=True)
class LabJob:
    job_id: str
    kind: str
    payload: dict[str, Any]
    priority: int
    attempts: int
    max_attempts: int


def validate_priority(value: int) -> int:
    try:
        priority = Priority(int(value))
    except (TypeError, ValueError) as exc:
        raise LabContractError("unsupported assistant-lab priority") from exc
    return int(priority)


def _require_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise LabContractError(f"{field} must be an object")
    return dict(value)


def validate_job_payload(kind: str, payload: Any) -> dict[str, Any]:
    normalized_kind = str(kind or "").strip().upper()
    if normalized_kind not in ALLOWED_KINDS:
        raise LabContractError("unsupported assistant-lab job kind")
    data = _require_mapping(payload, "payload")

    if normalized_kind == "NOOP":
        return data

    operation = str(data.get("operation") or "dd_table").strip()
    if operation not in ALLOWED_DDS3_OPERATIONS:
        raise LabContractError("unsupported DDS3 operation for assistant-lab v1")
    data["operation"] = operation

    if operation == "dd_table":
        pbn = str(data.get("pbn") or "").strip()
        if not pbn:
            raise LabContractError("dd_table requires pbn")
        if len(pbn) > 512:
            raise LabContractError("pbn exceeds bounded assistant-lab contract")
    elif operation == "position_all_moves":
        _require_mapping(data.get("position"), "position")
    elif operation == "position_trajectory":
        positions = data.get("positions")
        if not isinstance(positions, list) or not positions:
            raise LabContractError("position_trajectory requires positions")
        if len(positions) > 60:
            raise LabContractError("position_trajectory exceeds DDS3 bounded limit")
        perspective = str(data.get("perspective") or "").upper()
        if perspective not in {"NS", "EW"}:
            raise LabContractError("position_trajectory requires perspective NS or EW")
        data["perspective"] = perspective
    return data


def canonical_idempotency_key(kind: str, payload: Any) -> str:
    normalized_kind = str(kind or "").strip().upper()
    normalized_payload = validate_job_payload(normalized_kind, payload)
    encoded = json.dumps(
        {"contract": CONTRACT_VERSION, "kind": normalized_kind, "payload": normalized_payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{CONTRACT_VERSION}:{hashlib.sha256(encoded).hexdigest()}"


def verify_dds3_result(result: Any, *, expected_operation: str | None = None) -> dict[str, Any]:
    data = _require_mapping(result, "DDS3 result")
    if data.get("engine") != "DDS3":
        raise LabContractError("assistant-lab accepts only proven DDS3 results")
    if data.get("fallback_used") is not False:
        raise LabContractError("assistant-lab rejects DDS3 fallback results")
    if expected_operation is not None and data.get("operation") != expected_operation:
        raise LabContractError("DDS3 operation provenance mismatch")
    return data
