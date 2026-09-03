"""Offline-only link between source-bound player logic and verified DDS3 moves."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from bridge_contracts.assistant_lab import LabContractError, verify_dds3_result


SCHEMA = "video-decision-logic-dds-v2"
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#=-]{0,159}$")
_PUBLIC_CONTEXT = {
    "auction", "played_cards", "contract", "declarer", "vulnerability",
    "trick_no", "lead", "seat_to_play",
}


class VideoDDSComparisonError(ValueError):
    pass


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


def _ref(value: Any, label: str) -> str:
    value = _text(value, label)
    if not _REF.fullmatch(value) or re.match(r"^[NESW]:", value, re.IGNORECASE):
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
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(raw) > 4096 or any(marker in raw for marker in ('["N:', '["E:', '["S:', '["W:')):
        raise VideoDDSComparisonError("decision public context contains hidden information")
    return dict(value)


def build_offline_dds_comparison(
    observation: Mapping[str, Any],
    verified_board_evidence: Mapping[str, Any],
    source_bound_logic_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive public alternatives from a verified DDS position result."""
    if not isinstance(observation, Mapping) or set(observation) != {
        "decision", "dds_result", "full_deal_evidence"
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

    try:
        dds = verify_dds3_result(observation["dds_result"], expected_operation="position_all_moves")
    except LabContractError as exc:
        raise VideoDDSComparisonError(str(exc)) from exc
    if _sha(dds.get("deal_pbn_sha256"), "DDS deal_pbn_sha256") != deal_sha:
        raise VideoDDSComparisonError("DDS result not bound to verified full deal")
    request_sha = _sha(dds.get("request_sha256"), "DDS request_sha256")
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
            "operation": "position_all_moves", "request_sha256": request_sha,
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


__all__ = ["SCHEMA", "VideoDDSComparisonError", "build_offline_dds_comparison"]
