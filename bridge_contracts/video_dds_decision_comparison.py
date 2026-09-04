"""Offline-only link between source-bound player logic and verified DDS3 moves."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Mapping

from bridge_contracts.assistant_lab import (
    LabContractError,
    validate_job_payload,
    verify_dds3_result,
)


SCHEMA = "video-decision-logic-dds-v3"
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#=-]{0,159}$")
_HIDDEN_REF = re.compile(
    r"(?:^|[^A-Z0-9])(?:[NESW]|partner|opponent|north|east|south|west|lho|rho)[:/=.\-]"
    r"(?:(?:(?:(?:10)|[AKQJT2-9X])[SHDC])+|(?:(?:10)|[AKQJT2-9X.-])+)"
    r"(?=$|[^A-Z0-9])",
    re.IGNORECASE,
)
_HAND_SUIT = r"(?:-|(?:(?:10)|[AKQJT2-9X]){0,13})"
_COMPLETE_HAND_REF = re.compile(
    rf"(?<![A-Z0-9])(?:[NESW]:)?"
    rf"(?P<spades>{_HAND_SUIT})[./](?P<hearts>{_HAND_SUIT})[./]"
    rf"(?P<diamonds>{_HAND_SUIT})[./](?P<clubs>{_HAND_SUIT})(?![A-Z0-9])",
    re.IGNORECASE,
)
_HIDDEN_REF_KEYS = ("partner_hand", "opponent_hand", "hidden_hand", "full_deal")
_HIDDEN_NORMALIZED_REF = re.compile(
    r"(?:(?:actual)?(?:partner|opponent|north|east|south|west|lho|rho)s?"
    r"(?:hand|holding|cards?|deals?)+s?|(?:hidden|concealed)(?:hand|holding|cards?|deals?)+s?)"
)
_PUBLIC_CONTEXT = {
    "auction", "played_cards", "contract", "declarer", "vulnerability",
    "trick_no", "lead", "seat_to_play",
}
_CALL = re.compile(r"^(?:P|PASS|X|XX|DBL|RDBL|[1-7](?:C|D|H|S|NT))$", re.IGNORECASE)
_CARD = re.compile(r"^(?:[2-9TJQKA][CDHS])$", re.IGNORECASE)
_CONTRACT = re.compile(r"^[1-7](?:C|D|H|S|NT)(?:X|XX)?$", re.IGNORECASE)
_SEAT = {"N", "E", "S", "W"}
_SEAT_ORDER = ("N", "E", "S", "W")
_VULNERABILITY = {"NONE", "NS", "EW", "BOTH", "ALL"}


class VideoDDSComparisonError(ValueError):
    pass


DDSRequestExecutor = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _text(value: Any, label: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise VideoDDSComparisonError(f"{label} required")
    return value


def _sha(value: Any, label: str) -> str:
    value = _text(value, label).lower()
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise VideoDDSComparisonError(f"invalid {label}")
    return value


def _contains_complete_hand_ref(value: str) -> bool:
    for match in _COMPLETE_HAND_REF.finditer(value):
        card_count = sum(
            len(re.findall(r"10|[AKQJT2-9X]", holding, re.IGNORECASE))
            for holding in match.group("spades", "hearts", "diamonds", "clubs")
        )
        if card_count == 13:
            return True
    return False


def _ref(value: Any, label: str) -> str:
    value = _text(value, label)
    lowered = value.casefold()
    if (
        not _REF.fullmatch(value)
        or _HIDDEN_REF.search(value)
        or _contains_complete_hand_ref(value)
        or any(marker in lowered for marker in _HIDDEN_REF_KEYS)
        or _HIDDEN_NORMALIZED_REF.search(re.sub(r"[^a-z0-9]", "", lowered))
    ):
        raise VideoDDSComparisonError(f"invalid {label}")
    return value


def _refs(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise VideoDDSComparisonError(f"{label} required")
    result = [_ref(item, label) for item in value]
    if len(result) != len(set(result)):
        raise VideoDDSComparisonError(f"duplicate {label}")
    return result


def _public_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not set(value) <= _PUBLIC_CONTEXT:
        raise VideoDDSComparisonError("decision public context fields invalid")
    result: dict[str, Any] = {}
    for field, raw in value.items():
        if field == "auction":
            if not isinstance(raw, list) or len(raw) > 80 or any(
                not isinstance(call, str) or not _CALL.fullmatch(call.strip()) for call in raw
            ):
                raise VideoDDSComparisonError("decision public auction invalid")
            result[field] = [call.strip().upper() for call in raw]
        elif field == "played_cards":
            if not isinstance(raw, list) or len(raw) > 52 or any(
                not isinstance(card, str) or not _CARD.fullmatch(card.strip()) for card in raw
            ):
                raise VideoDDSComparisonError("decision public played cards invalid")
            cards = [card.strip().upper() for card in raw]
            if len(cards) != len(set(cards)):
                raise VideoDDSComparisonError("decision public played cards duplicate")
            result[field] = cards
        elif field == "contract":
            if not isinstance(raw, str) or not _CONTRACT.fullmatch(raw.strip()):
                raise VideoDDSComparisonError("decision public contract invalid")
            result[field] = raw.strip().upper()
        elif field in {"declarer", "seat_to_play"}:
            seat = raw.strip().upper() if isinstance(raw, str) else ""
            if seat not in _SEAT:
                raise VideoDDSComparisonError(f"decision public {field} invalid")
            result[field] = seat
        elif field == "vulnerability":
            vulnerability = raw.strip().upper() if isinstance(raw, str) else ""
            if vulnerability not in _VULNERABILITY:
                raise VideoDDSComparisonError("decision public vulnerability invalid")
            result[field] = vulnerability
        elif field == "trick_no":
            if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= 13:
                raise VideoDDSComparisonError("decision public trick_no invalid")
            result[field] = raw
        elif field == "lead":
            if not isinstance(raw, str) or not _CARD.fullmatch(raw.strip()):
                raise VideoDDSComparisonError("decision public lead invalid")
            result[field] = raw.strip().upper()
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded) > 4096:
        raise VideoDDSComparisonError("decision public context too large")
    return result


def _rerun_authenticated_dds_request(
    observation: Mapping[str, Any],
    public_context: Mapping[str, Any],
    executor: DDSRequestExecutor | None,
) -> tuple[dict[str, Any], str, str, str, str]:
    if executor is None:
        raise VideoDDSComparisonError("pinned DDS request executor required")
    try:
        payload = validate_job_payload("DDS3_COMPUTE", observation.get("dds_request"))
    except LabContractError as exc:
        raise VideoDDSComparisonError(str(exc)) from exc
    if payload.get("operation") != "position_all_moves":
        raise VideoDDSComparisonError("DDS request operation invalid")
    position = payload.get("position")
    if not isinstance(position, Mapping):
        raise VideoDDSComparisonError("DDS position invalid")
    first = str(position.get("first") or "").strip().upper()
    trump = str(position.get("trump") or "").strip().upper()
    current_raw = position.get("current_trick") or []
    if not isinstance(current_raw, list):
        raise VideoDDSComparisonError("DDS current trick invalid")
    current = [str(card).strip().upper() for card in current_raw]
    played = list(public_context.get("played_cards") or [])
    trick_no = public_context.get("trick_no", 1)
    # v3 carries only a hash of the original verified 52-card deal.  It cannot
    # prove a reduced remaining-card PBN, so mid-play requests must fail closed
    # instead of evaluating the opening deal under a later public context.
    if current or played or trick_no != 1 or "lead" in public_context:
        raise VideoDDSComparisonError(
            "DDS comparison supports only a verified opening position"
        )
    if "contract" not in public_context or not (
        "seat_to_play" in public_context or "declarer" in public_context
    ):
        raise VideoDDSComparisonError(
            "DDS opening position requires public contract and acting-seat context"
        )
    if "seat_to_play" in public_context and first != public_context["seat_to_play"]:
        raise VideoDDSComparisonError("DDS position first does not match public seat_to_play")
    if "declarer" in public_context:
        expected_leader = _SEAT_ORDER[
            (_SEAT_ORDER.index(public_context["declarer"]) + 1) % len(_SEAT_ORDER)
        ]
        if first != expected_leader:
            raise VideoDDSComparisonError(
                "DDS position first does not match public declarer"
            )
    if "contract" in public_context:
        contract_strain = re.match(r"^[1-7](NT|[CDHS])", public_context["contract"]).group(1)
        if trump != contract_strain:
            raise VideoDDSComparisonError("DDS position trump does not match public contract")
    try:
        rerun_result = executor(dict(payload))
    except Exception as exc:
        raise VideoDDSComparisonError("pinned DDS request rerun failed") from exc
    if not isinstance(rerun_result, Mapping):
        raise VideoDDSComparisonError("pinned DDS request returned an invalid result")
    try:
        dds = verify_dds3_result(rerun_result, expected_operation="position_all_moves")
    except LabContractError as exc:
        raise VideoDDSComparisonError(str(exc)) from exc
    binary_sha = _sha(dds.get("binary_sha256"), "DDS binary_sha256")
    pbn = _text(position.get("pbn") if isinstance(position, Mapping) else None, "DDS position pbn")
    deal_sha = hashlib.sha256(pbn.encode("utf-8")).hexdigest()
    request_sha = _digest(payload)
    result_sha = _digest(dds)
    return dds, deal_sha, request_sha, result_sha, binary_sha


def build_offline_dds_comparison(
    observation: Mapping[str, Any],
    verified_board_evidence: Mapping[str, Any],
    source_bound_logic_evidence: Mapping[str, Any],
    *,
    dds_request_executor: DDSRequestExecutor | None = None,
) -> dict[str, Any]:
    """Derive public alternatives from a verified DDS position result."""
    if not isinstance(observation, Mapping) or set(observation) != {
        "decision", "dds_request", "full_deal_evidence"
    }:
        raise VideoDDSComparisonError("DDS comparison fields mismatch")
    decision = observation["decision"]
    if not isinstance(decision, Mapping) or set(decision) != {
        "decision_id", "domain", "selected_action", "logic_candidate_id",
        "source_sha256", "public_context", "evidence_refs"
    }:
        raise VideoDDSComparisonError("decision fields mismatch")
    if decision.get("domain") not in {"PLAY", "DEFENSE"}:
        raise VideoDDSComparisonError("unsupported decision domain")
    public_context = _public_context(decision.get("public_context"))
    decision_refs = _refs(decision.get("evidence_refs"), "decision evidence ref")
    logic_id = _ref(decision.get("logic_candidate_id"), "logic_candidate_id")
    source_sha = _sha(decision.get("source_sha256"), "decision source_sha256")

    if not isinstance(source_bound_logic_evidence, Mapping) or set(source_bound_logic_evidence) != {
        "status", "logic_candidate_id", "source_sha256", "evidence_refs"
    }:
        raise VideoDDSComparisonError("source-bound logic evidence fields mismatch")
    if source_bound_logic_evidence.get("status") != "SOURCE_BOUND":
        raise VideoDDSComparisonError("player logic is not source-bound")
    logic_refs = _refs(source_bound_logic_evidence.get("evidence_refs"), "logic evidence ref")
    if (
        _ref(source_bound_logic_evidence.get("logic_candidate_id"), "logic evidence id") != logic_id
        or _sha(source_bound_logic_evidence.get("source_sha256"), "logic source_sha256") != source_sha
        or not set(decision_refs) <= set(logic_refs)
    ):
        raise VideoDDSComparisonError("decision does not resolve to source-bound logic")

    full_deal = observation["full_deal_evidence"]
    if not isinstance(full_deal, Mapping) or set(full_deal) != {
        "board_evidence_id", "deal_pbn_sha256", "source_refs", "verified_full_board"
    }:
        raise VideoDDSComparisonError("full deal evidence fields mismatch")
    if full_deal.get("verified_full_board") is not True:
        raise VideoDDSComparisonError("DDS comparison requires verified full board")
    board_id = _ref(full_deal.get("board_evidence_id"), "board_evidence_id")
    deal_sha = _sha(full_deal.get("deal_pbn_sha256"), "deal_pbn_sha256")
    board_refs = _refs(full_deal.get("source_refs"), "full deal source ref")
    if not isinstance(verified_board_evidence, Mapping) or set(verified_board_evidence) != {
        "status", "board_evidence_id", "deal_pbn_sha256", "card_count",
        "unique_card_count", "source_refs", "evidence_sha256"
    }:
        raise VideoDDSComparisonError("verified board evidence fields mismatch")
    card_count = verified_board_evidence.get("card_count")
    unique_card_count = verified_board_evidence.get("unique_card_count")
    if (
        verified_board_evidence.get("status") != "VERIFIED_FULL_BOARD"
        or isinstance(card_count, bool) or not isinstance(card_count, int) or card_count != 52
        or isinstance(unique_card_count, bool) or not isinstance(unique_card_count, int) or unique_card_count != 52
        or _ref(verified_board_evidence.get("board_evidence_id"), "verified board id") != board_id
        or _sha(verified_board_evidence.get("deal_pbn_sha256"), "verified deal sha256") != deal_sha
        or _refs(verified_board_evidence.get("source_refs"), "verified board source ref") != board_refs
    ):
        raise VideoDDSComparisonError("full-board assertion does not match verified reconstruction")
    board_evidence_sha = _sha(verified_board_evidence.get("evidence_sha256"), "board evidence_sha256")

    dds, trusted_deal_sha, request_sha, result_sha, binary_sha = _rerun_authenticated_dds_request(
        observation, public_context, dds_request_executor
    )
    if trusted_deal_sha != deal_sha:
        raise VideoDDSComparisonError("DDS result not bound to verified full deal")
    moves = dds.get("moves")
    if not isinstance(moves, list) or not moves:
        raise VideoDDSComparisonError("DDS result has no moves")
    selected_action = _text(decision.get("selected_action"), "selected_action").upper()
    alternatives: list[dict[str, Any]] = []
    for move in moves:
        if not isinstance(move, Mapping):
            raise VideoDDSComparisonError("DDS move invalid")
        action = _text(move.get("card"), "DDS move card").upper()
        value = move.get("tricks")
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 13:
            raise VideoDDSComparisonError("DDS move tricks invalid")
        regret = move.get("regret")
        if not isinstance(regret, int) or isinstance(regret, bool) or regret < 0:
            raise VideoDDSComparisonError("DDS move regret invalid")
        alternatives.append({
            "action": action, "metric": "tricks_for_side_to_play", "value": value,
            "regret": regret, "optimal": move.get("optimal") is True,
            "selected": action == selected_action,
        })
    if sum(item["selected"] for item in alternatives) != 1:
        raise VideoDDSComparisonError("selected player action missing from verified DDS moves")

    payload = {
        "schema": SCHEMA, "offline_only": True,
        "live_resolver_input_allowed": False, "canon_evidence_allowed": False,
        "decision": {
            "decision_id": _ref(decision.get("decision_id"), "decision_id"),
            "domain": decision["domain"], "selected_action": selected_action,
            "logic_candidate_id": logic_id, "source_sha256": source_sha,
            "public_context": public_context, "evidence_refs": decision_refs,
        },
        "dds_provenance": {
            "engine": dds["engine"], "engine_version": dds["engine_version"],
            "operation": "position_all_moves",
            "request_sha256": request_sha, "result_sha256": result_sha,
            "binary_sha256": binary_sha,
            "verification_mode": "PINNED_DDS_RERUN",
        },
        "full_deal_evidence": {
            "board_evidence_id": board_id, "board_evidence_sha256": board_evidence_sha,
            "source_refs": board_refs, "verified_full_board": True,
        },
        "alternatives": alternatives,
        "interpretation": "DDS evaluates post-hoc consequences, not the authority of player logic or a bidding rule.",
    }
    payload["comparison_sha256"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


__all__ = [
    "SCHEMA", "DDSRequestExecutor", "VideoDDSComparisonError",
    "build_offline_dds_comparison",
]
