"""Offline-only link between a source-bound decision explanation and DDS3.

DDS3 may evaluate consequences on a verified full deal.  It never supplies
hidden cards to a live resolver and never proves a bidding rule.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from bridge_contracts.assistant_lab import LabContractError, verify_dds3_result


SCHEMA = "video-decision-logic-dds-v1"
_FORBIDDEN = {
    "pbn", "full_deal", "hidden_cards", "partner_hand", "opponent_hand",
    "opponent_hands", "north_hand", "east_hand", "south_hand", "west_hand",
    "all_hands", "actual_partner_hand", "actual_opponent_hand",
}


class VideoDDSComparisonError(ValueError):
    pass


def _text(value: Any, label: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise VideoDDSComparisonError(f"{label} required")
    return value


def _hidden(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(str(key).casefold() in _FORBIDDEN or _hidden(child) for key, child in value.items())
    return isinstance(value, list) and any(_hidden(child) for child in value)


def _sha(value: Any, label: str) -> str:
    value = _text(value, label).lower()
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise VideoDDSComparisonError(f"invalid {label}")
    return value


def build_offline_dds_comparison(observation: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and sanitize an offline DDS comparison for staging only."""
    expected = {"decision", "dds_result", "full_deal_evidence", "alternatives"}
    if not isinstance(observation, Mapping) or set(observation) != expected:
        raise VideoDDSComparisonError("DDS comparison fields mismatch")
    decision = observation["decision"]
    if not isinstance(decision, Mapping) or set(decision) != {
        "decision_id", "domain", "selected_action", "logic_candidate_id",
        "public_context", "evidence_refs"
    }:
        raise VideoDDSComparisonError("decision fields mismatch")
    if decision.get("domain") not in {"PLAY", "DEFENSE", "AUCTION_OUTCOME"}:
        raise VideoDDSComparisonError("unsupported decision domain")
    if _hidden(decision.get("public_context")):
        raise VideoDDSComparisonError("decision public context contains hidden information")
    refs = decision.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        raise VideoDDSComparisonError("decision evidence refs required")
    full_deal = observation["full_deal_evidence"]
    if not isinstance(full_deal, Mapping) or set(full_deal) != {
        "deal_pbn_sha256", "source_refs", "verified_full_board"
    }:
        raise VideoDDSComparisonError("full deal evidence fields mismatch")
    if full_deal.get("verified_full_board") is not True:
        raise VideoDDSComparisonError("DDS comparison requires verified full board")
    deal_sha = _sha(full_deal.get("deal_pbn_sha256"), "deal_pbn_sha256")
    source_refs = full_deal.get("source_refs")
    if not isinstance(source_refs, list) or not source_refs:
        raise VideoDDSComparisonError("full deal source refs required")
    try:
        dds = verify_dds3_result(observation["dds_result"])
    except LabContractError as exc:
        raise VideoDDSComparisonError(str(exc)) from exc
    if dds.get("deal_pbn_sha256") != deal_sha:
        raise VideoDDSComparisonError("DDS result not bound to verified full deal")
    alternatives = observation["alternatives"]
    if not isinstance(alternatives, list) or not alternatives:
        raise VideoDDSComparisonError("DDS alternatives required")
    sanitized_alternatives: list[dict[str, Any]] = []
    selected_seen = False
    for item in alternatives:
        if not isinstance(item, Mapping) or set(item) != {"action", "metric", "value", "selected"}:
            raise VideoDDSComparisonError("DDS alternative fields mismatch")
        if _hidden(item):
            raise VideoDDSComparisonError("DDS alternative contains hidden information")
        if not isinstance(item.get("value"), (int, float)) or isinstance(item.get("value"), bool):
            raise VideoDDSComparisonError("DDS alternative value invalid")
        action = _text(item.get("action"), "DDS alternative action")
        selected = item.get("selected") is True
        selected_seen = selected_seen or selected
        sanitized_alternatives.append({
            "action": action, "metric": _text(item.get("metric"), "DDS alternative metric"),
            "value": item["value"], "selected": selected,
        })
    if not selected_seen or sum(item["selected"] for item in sanitized_alternatives) != 1:
        raise VideoDDSComparisonError("exactly one selected DDS action required")
    if not any(item["action"] == decision["selected_action"] and item["selected"] for item in sanitized_alternatives):
        raise VideoDDSComparisonError("DDS selected action differs from player decision")
    safe_dds = {
        "engine": dds["engine"], "engine_version": dds["engine_version"],
        "operation": dds["operation"], "request_sha256": dds.get("request_sha256"),
        "deal_pbn_sha256": deal_sha,
    }
    payload = {
        "schema": SCHEMA,
        "offline_only": True,
        "live_resolver_input_allowed": False,
        "canon_evidence_allowed": False,
        "decision": {
            "decision_id": _text(decision.get("decision_id"), "decision_id"),
            "domain": decision["domain"],
            "selected_action": _text(decision.get("selected_action"), "selected_action"),
            "logic_candidate_id": _text(decision.get("logic_candidate_id"), "logic_candidate_id"),
            "public_context": dict(decision["public_context"]),
            "evidence_refs": [str(ref) for ref in refs],
        },
        "dds_provenance": safe_dds,
        "full_deal_evidence": {"source_refs": [str(ref) for ref in source_refs], "verified_full_board": True},
        "alternatives": sanitized_alternatives,
        "interpretation": "DDS evaluates post-hoc consequences, not the authority of the player's logic or bidding rule.",
    }
    payload["comparison_sha256"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


__all__ = ["SCHEMA", "VideoDDSComparisonError", "build_offline_dds_comparison"]
