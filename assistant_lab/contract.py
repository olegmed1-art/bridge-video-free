"""Pure contracts for the isolated Assistant Lab v1 runtime."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Mapping

from bridge_school_api.dds3.service import DDS3_HAND_ORDER, DDS3_STRAIN_ORDER, DDS_UPSTREAM

CONTRACT_VERSION = "assistant-lab-v1"


class LabContractError(ValueError):
    """Raised when a lab job violates the bounded v1 contract."""


class Priority(IntEnum):
    INTERACTIVE = 0
    REGRESSION = 10
    EXPERIMENT = 20
    BACKGROUND = 30


ALLOWED_KINDS = frozenset({"DDS3_COMPUTE", "BEN_COMPUTE", "WORLD_GENERATE", "NOOP"})
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


def _validate_ben_payload(data: dict[str, Any]) -> dict[str, Any]:
    hand = str(data.get("hand") or "").strip()
    seat = str(data.get("seat") or "").strip().upper()
    dealer = str(data.get("dealer") or "").strip().upper()
    if not hand or len(hand) > 80:
        raise LabContractError("BEN_COMPUTE requires bounded hand")
    if seat not in {"N", "E", "S", "W"}:
        raise LabContractError("BEN_COMPUTE requires seat N/E/S/W")
    if dealer not in {"N", "E", "S", "W"}:
        raise LabContractError("BEN_COMPUTE requires dealer N/E/S/W")
    auction = data.get("auction", [])
    if not isinstance(auction, list) or len(auction) > 80 or any(not isinstance(call, str) or len(call) > 16 for call in auction):
        raise LabContractError("BEN_COMPUTE auction exceeds bounded contract")
    vul = str(data.get("vul") or "").strip().upper()
    if vul not in {"", "NONE", "NS", "EW", "BOTH", "ALL"}:
        raise LabContractError("BEN_COMPUTE vulnerability is invalid")
    result = dict(data)
    result.update({"hand": hand, "seat": seat, "dealer": dealer, "auction": auction, "vul": vul})
    return result


def _validate_world_payload(data: dict[str, Any]) -> dict[str, Any]:
    from bridge_school_api.ai_worlds import WorldConstraints, parse_hand_pbn

    seat = str(data.get("known_seat") or "").strip().upper()
    hand = str(data.get("known_hand_pbn") or "").strip()
    count = data.get("count", 128)
    seed = data.get("seed")
    if seat not in {"N", "E", "S", "W"}:
        raise LabContractError("WORLD_GENERATE requires known_seat N/E/S/W")
    try:
        parse_hand_pbn(hand)
        constraints = data.get("constraints")
        WorldConstraints.parse(constraints)
    except ValueError as exc:
        raise LabContractError(str(exc)) from exc
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 1000:
        raise LabContractError("WORLD_GENERATE count must be between 1 and 1000")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise LabContractError("WORLD_GENERATE requires integer seed")
    max_attempts = data.get("max_attempts")
    if max_attempts is not None and (
        isinstance(max_attempts, bool) or not isinstance(max_attempts, int)
        or max_attempts < count or max_attempts > 1_000_000
    ):
        raise LabContractError("WORLD_GENERATE max_attempts is invalid")
    result = dict(data)
    result.update({"known_seat": seat, "known_hand_pbn": hand, "count": count, "seed": seed})
    return result


def validate_job_payload(kind: str, payload: Any) -> dict[str, Any]:
    normalized_kind = str(kind or "").strip().upper()
    if normalized_kind not in ALLOWED_KINDS:
        raise LabContractError("unsupported assistant-lab job kind")
    data = _require_mapping(payload, "payload")
    if normalized_kind == "NOOP":
        return data
    if normalized_kind == "BEN_COMPUTE":
        return _validate_ben_payload(data)
    if normalized_kind == "WORLD_GENERATE":
        return _validate_world_payload(data)
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
    encoded = json.dumps({"contract": CONTRACT_VERSION, "kind": normalized_kind, "payload": normalized_payload}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{CONTRACT_VERSION}:{hashlib.sha256(encoded).hexdigest()}"


def verify_dds3_result(result: Any, *, expected_operation: str | None = None) -> dict[str, Any]:
    data = _require_mapping(result, "DDS3 result")
    if data.get("engine") != "DDS3":
        raise LabContractError("assistant-lab accepts only proven DDS3 results")
    if data.get("fallback_used") is not False:
        raise LabContractError("assistant-lab rejects DDS3 fallback results")
    if expected_operation is not None and data.get("operation") != expected_operation:
        raise LabContractError("DDS3 operation provenance mismatch")
    if data.get("engine_version") != DDS_UPSTREAM:
        raise LabContractError("DDS3 engine version provenance mismatch")
    if data.get("operation") == "dd_table":
        if data.get("input_validated") is not True:
            raise LabContractError("DDS3 input validation provenance is missing")
        if data.get("hand_order") != list(DDS3_HAND_ORDER):
            raise LabContractError("DDS3 hand order provenance mismatch")
        if data.get("strain_order") != list(DDS3_STRAIN_ORDER):
            raise LabContractError("DDS3 strain order provenance mismatch")
        for field in ("deal_pbn_sha256", "request_sha256"):
            value = data.get(field)
            if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise LabContractError(f"DDS3 {field} provenance is invalid")
    return data


def verify_ben_result(result: Any) -> dict[str, Any]:
    data = _require_mapping(result, "BEN result")
    bid = data.get("bid") or data.get("call")
    candidates = data.get("candidates")
    if not isinstance(bid, str) or not bid.strip():
        raise LabContractError("BEN result has no selected bid")
    if not isinstance(candidates, list) or not candidates:
        raise LabContractError("BEN result has no candidates")
    selected = bid.strip()
    actions: set[str] = set()
    selected_scored = False
    for candidate in candidates:
        item = _require_mapping(candidate, "BEN candidate")
        action = item.get("call") or item.get("bid") or item.get("action")
        if not isinstance(action, str) or not action.strip():
            raise LabContractError("BEN candidate has no action")
        normalized_action = action.strip()
        actions.add(normalized_action)
        score = item.get("insta_score", item.get("score"))
        if score is not None:
            if isinstance(score, bool):
                raise LabContractError("BEN candidate score is not numeric")
            try:
                numeric = float(score)
            except (TypeError, ValueError) as exc:
                raise LabContractError("BEN candidate score is not numeric") from exc
            if not math.isfinite(numeric):
                raise LabContractError("BEN candidate score is not finite")
            if normalized_action == selected:
                selected_scored = True
    if selected not in actions:
        raise LabContractError("BEN selected bid is absent from candidates")
    if not selected_scored:
        raise LabContractError("BEN selected bid has no finite policy score")
    result_copy = dict(data)
    result_copy.setdefault("engine", "BEN")
    result_copy["evidence_class"] = "POLICY_ONLY"
    result_copy["dds_search_evidence"] = False
    return result_copy
