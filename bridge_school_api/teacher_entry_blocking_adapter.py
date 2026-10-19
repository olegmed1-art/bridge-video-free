"""Test-only symbolic composition of the existing L1 entry/blocking rules.

No route registration, DB access, exercise lookup, opponent cards or solver.
The caller supplies a fresh eligible shortlist from its trusted knowledge reader.
Source-only preparation: native tests and I2 assurance have NOT_RUN.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import re
from typing import Any

from .l1_canonical_registry import SYSTEM_VERSION, ACTIVE_DOMAIN_RULE_IDS
from .l1_canonical_runtime_v4 import evaluate as evaluate_l1

VERSION = "teacher-entry-blocking-source-v3"
ADAPTER_REF = "bridge_school_api.teacher_entry_blocking_adapter:decide"
OBSERVATION_SCHEMA = "teacher-symbolic-entry-v1"
OBSERVATION_FIELDS = frozenset({
    "schema", "profile", "role", "phase", "trump", "declarer", "dummy",
    "dummy_visible", "target_suit", "hand_a", "hand_b",
    "side_entries_to_a", "leader", "current_trick", "request",
})
HAND_FIELDS = frozenset({"seat", "honors", "length", "low_cards"})
RECORD_FIELDS = frozenset({
    "item_id", "version_no", "system_profile", "review_status",
    "version_status", "dependency_generation", "provenance", "content",
})
CONTRACT_FIELDS = frozenset({
    "contract_version", "skill_id", "adapter_ref", "canonical_refs",
    "applicability", "actions", "effects", "required_resources",
    "consumed_resources", "threats", "exceptions",
})
SEATS = "NESW"
SUITS = "SHDC"
LOW_RANKS = "23456789"
RULE_BINDINGS = {
    "SKILL-0003": ("RULE-L1-PLAY-PRESERVE-ENTRIES", "FACT-L1-SKILL-0003"),
    "SKILL-0004": ("RULE-L1-PLAY-AVOID-BLOCKING", "FACT-L1-SKILL-0004"),
}
SOURCE_LOCATORS = {
    "SKILL-0003": "Навыки / SKILL-0003 / урок 5; далее урок 6 и последующие уровни",
    "SKILL-0004": "Навыки / SKILL-0004 / урок 5",
}


class OutsideScope(ValueError):
    def __init__(self, reason: str, missing: tuple[str, ...] = ()):
        super().__init__(reason)
        self.reason = reason
        self.missing = missing


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise OutsideScope(reason)


def _enum(value: Any, choices: tuple[str, ...]) -> bool:
    return type(value) is str and value in choices


def _digest(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)
    return sha256(text.encode("utf-8")).hexdigest()


def _validate_observation(observation: Any) -> dict[str, Any]:
    _require(type(observation) is dict, "OBSERVATION_TYPE")
    _require(not (set(observation) - OBSERVATION_FIELDS), "OBSERVATION_FIELDS")
    missing = tuple(sorted(OBSERVATION_FIELDS - set(observation)))
    if missing:
        raise OutsideScope("OBSERVATION_REQUIRED_FIELDS", missing)
    _require(observation["schema"] == OBSERVATION_SCHEMA, "OBSERVATION_SCHEMA")
    _require(observation["profile"] == SYSTEM_VERSION, "PROFILE_SCOPE")
    _require(observation["role"] == "declarer"
             and observation["phase"] == "suit_timing"
             and observation["trump"] == "NT", "ROLE_PHASE_CONTRACT_SCOPE")
    own, dummy = observation["declarer"], observation["dummy"]
    _require(_enum(own, tuple(SEATS))
             and dummy == SEATS[(SEATS.index(own) + 2) % 4], "FRIENDLY_SEATS")
    _require(observation["dummy_visible"] is True, "DUMMY_NOT_VISIBLE")
    seats, known_lows = [], []
    for name, honors, length in (("hand_a", ["A"], 3), ("hand_b", ["Q", "J"], 4)):
        hand = observation[name]
        _require(type(hand) is dict and set(hand) == HAND_FIELDS, "HAND_FIELDS")
        _require(_enum(hand["seat"], (own, dummy)), "HAND_VISIBILITY")
        _require(type(hand["length"]) is int and hand["length"] == length,
                 "HOLDING_PATTERN_SCOPE")
        given = hand["honors"]
        _require(type(given) is list and len(given) == len(honors)
                 and all(type(x) is str for x in given)
                 and sorted(given) == sorted(honors), "HOLDING_PATTERN_SCOPE")
        lows = hand["low_cards"]
        _require(lows is None or (
            type(lows) is list and len(lows) <= 2
            and all(_enum(x, tuple(LOW_RANKS)) for x in lows)
            and len(set(lows)) == len(lows)), "LOW_CARD_FORMAT")
        seats.append(hand["seat"])
        known_lows.extend(lows or [])
    _require(len(set(seats)) == 2, "FRIENDLY_HANDS_REQUIRED")
    _require(len(known_lows) == len(set(known_lows)), "DUPLICATE_CARD")
    suit = observation["target_suit"]
    _require(suit is None or _enum(suit, tuple(SUITS)), "TARGET_SUIT")
    _require(_enum(observation["side_entries_to_a"], ("NONE", "SUFFICIENT", "UNKNOWN")),
             "SIDE_ENTRY_CONTEXT")
    _require(_enum(observation["request"], ("SYMBOLIC_PLAN", "CONCRETE_CARD")),
             "REQUEST_SCOPE")
    leader = observation["leader"]
    _require(leader is None or _enum(leader, tuple(SEATS)), "LEADER_FORMAT")
    _require(leader is None or leader == observation["hand_a"]["seat"],
             "SUPPORTED_FIRST_STEP_REQUIRES_LEAD_FROM_A")
    _require(observation["current_trick"] is None
             or (type(observation["current_trick"]) is list
                 and observation["current_trick"] == []),
             "FIRST_STEP_ONLY_REPLAN_AFTER_PUBLIC_PLAY")
    # No private or unrecognized nested payload has been copied or interpreted.
    result = deepcopy(observation)
    for name in ("hand_a", "hand_b"):
        result[name]["honors"] = sorted(result[name]["honors"])
        if result[name]["low_cards"] is not None:
            result[name]["low_cards"] = sorted(result[name]["low_cards"])
    return result


def _expected_contract(skill_id: str) -> dict[str, Any]:
    rule_id, fact_id = RULE_BINDINGS[skill_id]
    reserve = skill_id == "SKILL-0003"
    return {
        "contract_version": 1,
        "skill_id": skill_id,
        "adapter_ref": ADAPTER_REF,
        "canonical_refs": {"rule_id": rule_id, "fact_id": fact_id},
        "applicability": {
            "system_profile": SYSTEM_VERSION, "role": "declarer",
            "phase": "suit_timing", "trump": "NT",
            "holding_pattern": "Axx_QJxx", "task_class": "SYMBOLIC_ENTRY_ORDER",
        },
        "actions": (["identify_required_entry", "preserve_required_entry"] if reserve
                    else ["reject_blocking_order", "choose_access_preserving_order"]),
        "effects": [{
            "kind": "RESERVE_RESOURCE" if reserve else "COMPARE_RESOURCE_ORDER",
            "resource": "ACE_ENTRY_A",
            "condition": "RETURN_TO_A_PLANNED" if reserve else "SIDE_ENTRIES_TO_A_NONE",
            "until": "RETURN_TO_A",
        }],
        "required_resources": ["ACE_ENTRY_A", "LOW_A", "LOW_B"],
        "consumed_resources": [],
        "threats": ["ENTRY_SPENT_BEFORE_RETURN", "Q_MAY_LOSE_TO_K"],
        "exceptions": ["SUFFICIENT_SIDE_ENTRIES_DO_NOT_RANK",
                       "LEAD_FROM_B_REQUIRES_REPLAN"],
    }


def _validate_records(eligible_records: Any) -> list[dict[str, Any]]:
    _require(type(eligible_records) is list and len(eligible_records) <= 2,
             "BOUNDED_ELIGIBLE_SHORTLIST_REQUIRED")
    found: dict[str, dict[str, Any]] = {}
    seen_item_ids: set[str] = set()
    for record in eligible_records:
        _require(type(record) is dict and set(record) == RECORD_FIELDS,
                 "ELIGIBLE_RECORD_FIELDS")
        _require(type(record["content"]) is dict
                 and set(record["content"]) == {"skill_contract"}, "CONTENT_FIELDS")
        contract = record["content"]["skill_contract"]
        _require(type(contract) is dict and set(contract) == CONTRACT_FIELDS,
                 "SKILL_CONTRACT_FIELDS")
        skill_id = contract["skill_id"]
        _require(_enum(skill_id, tuple(RULE_BINDINGS)), "SKILL_SCOPE")
        _require(skill_id not in found, "DUPLICATE_SKILL_VERSION")
        _require(type(contract["contract_version"]) is int
                 and contract == _expected_contract(skill_id), "UNSUPPORTED_SKILL_CONTRACT")
        _require(record["system_profile"] == SYSTEM_VERSION
                 and record["review_status"] == "approved"
                 and record["version_status"] == "active", "SKILL_NOT_ELIGIBLE")
        _require(type(record["version_no"]) is int and record["version_no"] >= 1,
                 "KNOWLEDGE_VERSION_REQUIRED")
        _require(type(record["dependency_generation"]) is int
                 and record["dependency_generation"] >= 1, "DEPENDENCY_GENERATION_REQUIRED")
        item_id = record["item_id"]
        _require(type(item_id) is str and (
            item_id == f"prepared:{skill_id}:v1"
            or re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", item_id) is not None),
                 "KNOWLEDGE_ITEM_ID_REQUIRED")
        # One immutable knowledge version cannot represent two skill contracts.
        # UUID hex casing does not create a different identity.
        identity = item_id.lower()
        _require(identity not in seen_item_ids, "DUPLICATE_KNOWLEDGE_ITEM_ID")
        seen_item_ids.add(identity)
        provenance = record["provenance"]
        expected_provenance = [{
            "source_id": "SRC-0022", "source_locator": SOURCE_LOCATORS[skill_id],
            "review_status": "APPROVED_SOURCE",
        }]
        _require(provenance == expected_provenance, "APPROVED_PROVENANCE_REQUIRED")
        _require(RULE_BINDINGS[skill_id][0] in ACTIVE_DOMAIN_RULE_IDS, "L1_REGISTRY_GATE")
        found[skill_id] = record
    _require(set(found) == set(RULE_BINDINGS), "MISSING_ELIGIBLE_SKILL")
    _require(len({r["dependency_generation"] for r in found.values()}) == 1,
             "MIXED_DEPENDENCY_GENERATIONS")
    return deepcopy([found[s] for s in sorted(found)])


def _concrete_missing(o: dict[str, Any]) -> list[str]:
    missing = []
    if o["target_suit"] is None:
        missing.append("target_suit")
    for name in ("hand_a", "hand_b"):
        if o[name]["low_cards"] is None or len(o[name]["low_cards"]) != 2:
            missing.append(f"{name}.low_cards")
    if o["leader"] is None:
        missing.append("leader")
    if o["current_trick"] is None:
        missing.append("current_trick")
    if o["side_entries_to_a"] == "UNKNOWN":
        missing.append("side_entries_to_a")
    return missing


def _candidates(o: dict[str, Any]) -> list[dict[str, Any]]:
    first_guard = ["LEAD_FROM_HAND_A", "EMPTY_CURRENT_TRICK"]
    return [
        {
            "plan_id": "LOW_TO_QJ", "first_step": {
                "kind": "LEAD_LOW", "from_hand": "HAND_A", "toward": "QJ_IN_HAND_B",
            },
            "premises": first_guard,
            "resources": {
                "required": ["ACE_ENTRY_A", "LOW_A", "LOW_B"],
                "reserved": ["ACE_ENTRY_A"], "planned_consumed_first": ["LOW_A"],
                "actually_consumed": [],
            },
            "continuations": [
                {"condition": "Q_WINS_TRICK",
                 "steps": ["LOW_FROM_B_TO_A", "WIN_WITH_A", "LOW_FROM_A_TOWARD_B"],
                 "effect": "KNOWN_ACE_ENTRY_AVAILABLE_UNTIL_RETURN"},
                {"condition": "K_CAPTURES_Q",
                 "steps": ["WAIT_FOR_PUBLIC_LEADER_AND_REPLAN"],
                 "effect": "NO_GUARANTEE_OF_RETURN_OR_THREE_TRICKS"},
                {"condition": "OTHER_PUBLIC_RESPONSE_INCLUDING_EARLY_K",
                 "steps": ["OBSERVE_RESPONSE_AND_REPLAN"],
                 "effect": "NO_QUEUED_PLAY_FROM_HAND_B"},
            ],
            "threats": ["Q_MAY_LOSE_TO_K", "PUBLIC_PLAY_REQUIRES_REPLAN"],
        },
        {
            "plan_id": "ACE_FIRST", "first_step": {
                "kind": "CASH_A", "from_hand": "HAND_A", "toward": None,
            },
            "premises": first_guard,
            "resources": {
                "required": ["ACE_ENTRY_A", "LOW_A", "LOW_B"],
                "reserved": [], "planned_consumed_first": ["ACE_ENTRY_A"],
                "actually_consumed": [],
            },
            "continuations": [
                {"condition": "RETURN_TO_A_NEEDED_AND_NO_SIDE_ENTRY",
                 "steps": ["NEEDS_ANOTHER_PUBLICLY_ESTABLISHED_ENTRY"],
                 "effect": "ONLY_CURRENTLY_KNOWN_ACE_ENTRY_ALREADY_SPENT"},
            ],
            "threats": ["ENTRY_SPENT_BEFORE_RETURN", "LOW_CARD_ENTRY_NOT_PROVEN"],
        },
    ]


def render_explanation(trace: list[dict[str, Any]]) -> dict[str, Any]:
    """All prose and citations derive from the same decision trace, not a key."""
    nodes = {n["node_id"]: n for n in trace}
    comparison, selection = nodes["comparison"], nodes["selection"]
    text = ["Сравниваются малая из руки A к QJ в руке B и туз сначала."]
    if comparison["side_entries_to_a"] == "SUFFICIENT":
        text.append("Боковых входов достаточно. Навыки 0003/0004 сами по себе "
                    "не ранжируют эти два порядка.")
    else:
        text.append("При отсутствии бокового входа малая к QJ сохраняет туз "
                    "как известный вход для возврата в руку A.")
        text.append("Если дама удержит взятку, можно рассмотреть возврат малой "
                    "из B к тузу A и повторный ход из A к B.")
        text.append("Если король возьмёт даму, нужна новая публичная ситуация; "
                    "возврат и три взятки не гарантированы.")
        if comparison["side_entries_to_a"] == "UNKNOWN":
            text.append("Предпочтение условно: доступность боковых входов "
                        "пока не определена.")
    if selection["immediate_action"] is not None:
        text.append("Первый конкретный ход: " + selection["immediate_action"]["card"]
                    + " из " + selection["immediate_action"]["seat"] + ".")
    if selection["missing_context"]:
        text.append("Для конкретной карты нужны: " + ", ".join(selection["missing_context"]) + ".")
    citations = [
        {"skill_id": n["skill_id"], "version_no": n["version_no"],
         "canonical_refs": n["canonical_refs"], "provenance": n["provenance"]}
        for n in trace if n["kind"] == "SKILL_EVALUATION"
    ]
    return {"selection_node": "selection", "comparison_node": "comparison",
            "paragraphs_ru": text, "citations": citations}


def _empty(reason: str, missing: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "adapter_version": VERSION, "test_only": True,
        "status": "NEEDS_CONTEXT" if missing else "ABSTAIN",
        "reason": reason, "selected_plan": None, "immediate_action": None,
        "alternatives": [], "missing_context": list(missing), "trace": [],
        "explanation": None, "binding": None, "success_probability": None,
        "comparison_confidence": "NOT_ASSESSED",
    }


def decide(observation: Any, eligible_records: Any, *, test_only: bool = False) -> dict[str, Any]:
    """Real decision seam: visible input -> validated skills -> L1 -> joint trace.

    No decision cache exists. Every call revalidates the fresh eligible shortlist.
    Eligible records are NOT accepted as proof of production canon activation.
    """
    if test_only is not True:
        return _empty("TEST_ONLY_OPT_IN_REQUIRED")
    try:
        o = _validate_observation(observation)
        records = _validate_records(eligible_records)
    except OutsideScope as exc:
        return _empty(exc.reason, exc.missing)
    trace = [{
        "node_id": "observation", "kind": "PUBLIC_OBSERVATION",
        "holding_pattern": "Axx_QJxx", "side_entries_to_a": o["side_entries_to_a"],
        "stage": "FIRST_SYMBOLIC_STEP", "entry_assessment": "SUPPLIED_PLAYER_VISIBLE_SUMMARY",
    }, {
        "node_id": "resources", "kind": "RESOURCE_LEDGER",
        "known_available": ["ACE_ENTRY_A", "LOW_A", "LOW_B"],
        "reserved_until_return": (["ACE_ENTRY_A"]
                                  if o["side_entries_to_a"] != "SUFFICIENT" else []),
        "reservation_condition": "SIDE_ENTRIES_TO_A_NONE", "actually_consumed": [],
    }]
    risk = o["side_entries_to_a"] != "SUFFICIENT"
    for record in records:
        contract = record["content"]["skill_contract"]
        rule_id = contract["canonical_refs"]["rule_id"]
        context = ({"planned_target_hand_known": True}
                   if contract["skill_id"] == "SKILL-0003"
                   else {"entry_or_access_risk": risk})
        evaluation = evaluate_l1(rule_id, context, system_version=SYSTEM_VERSION)
        expected_status = "MATCH" if contract["skill_id"] == "SKILL-0003" or risk else "NO_MATCH"
        expected_action = ("IDENTIFY_AND_PRESERVE_REQUIRED_ENTRY"
                           if contract["skill_id"] == "SKILL-0003"
                           else "CHOOSE_ACCESS_PRESERVING_ORDER")
        if evaluation.status != expected_status or (
            expected_status == "MATCH" and (
                evaluation.action != expected_action
                or contract["canonical_refs"]["fact_id"] not in evaluation.evidence)):
            return _empty("L1_RULE_EVALUATION_CHANGED")
        trace.append({
            "node_id": contract["skill_id"], "kind": "SKILL_EVALUATION",
            "skill_id": contract["skill_id"], "version_no": record["version_no"],
            "canonical_refs": contract["canonical_refs"],
            "provenance": record["provenance"],
            "actions": contract["actions"], "effects": contract["effects"],
            "required_resources": contract["required_resources"],
            "consumed_resources": contract["consumed_resources"],
            "threats": contract["threats"], "exceptions": contract["exceptions"],
            "derived_context": context,
            "rule_evaluation": {
                "rule_id": evaluation.rule_id, "status": evaluation.status,
                "action": evaluation.action, "evidence": list(evaluation.evidence),
                "reason": evaluation.reason,
            },
        })
    alternatives = _candidates(o)
    trace.extend({"node_id": p["plan_id"], "kind": "ALTERNATIVE", **p}
                 for p in alternatives)
    selected = "LOW_TO_QJ" if risk else None
    comparison = {
        "node_id": "comparison", "kind": "RESOURCE_ORDER_COMPARISON",
        "side_entries_to_a": o["side_entries_to_a"],
        "criterion": "PRESERVE_CURRENTLY_KNOWN_ENTRY_FOR_PLANNED_RETURN",
        "preference": selected,
        "applies_if": ["SIDE_ENTRIES_TO_A_NONE", "LEAD_FROM_HAND_A", "EMPTY_CURRENT_TRICK"]
                      if risk else [],
        "switches": {"SIDE_ENTRIES_SUFFICIENT": "UNRANKED_BY_THIS_SKILL_PAIR"},
        "claims": "COMMUNICATION_ORDER_ONLY_NOT_GLOBAL_OPTIMUM",
        "success_probability": None,
    }
    trace.append(comparison)
    missing = _concrete_missing(o) if o["request"] == "CONCRETE_CARD" else []
    status = ("NEEDS_CONTEXT" if missing else
              "CONDITIONAL_RECOMMENDATION" if selected else "ADMISSIBLE_ALTERNATIVES")
    action = None
    if not missing and o["request"] == "CONCRETE_CARD" and selected:
        card = o["target_suit"] + min(o["hand_a"]["low_cards"], key=LOW_RANKS.index)
        action = {"kind": "PLAY_CARD", "seat": o["hand_a"]["seat"], "card": card,
                  "allowed_low_cards": [o["target_suit"] + r for r in o["hand_a"]["low_cards"]],
                  "choice_basis": "REPRESENTATIVE_LOW_NOT_PROVEN_BEST_SPOT"}
    trace.append({
        "node_id": "selection", "kind": "SELECTION",
        "status": status, "selected_plan": selected, "immediate_action": action,
        "missing_context": missing,
        "premises": comparison["applies_if"],
    })
    binding = {
        "adapter_version": VERSION, "observation_sha256": _digest(o),
        "eligible_records_sha256": _digest(records),
        "dependency_generation": records[0]["dependency_generation"],
        "dependencies": [{
            "item_id": r["item_id"], "version_no": r["version_no"],
            "skill_id": r["content"]["skill_contract"]["skill_id"],
            "rule_id": r["content"]["skill_contract"]["canonical_refs"]["rule_id"],
        } for r in records],
    }
    return {
        "adapter_version": VERSION, "test_only": True,
        "status": status,
        "reason": ("CONCRETE_CONTEXT_MISSING" if missing else
                   "CONDITIONAL_ENTRY_PRESERVATION" if selected else "SKILL_PAIR_DOES_NOT_RANK"),
        "selected_plan": selected, "immediate_action": action,
        "alternatives": alternatives, "conditional_premises": comparison["applies_if"],
        "missing_context": missing, "trace": trace,
        "explanation": render_explanation(trace), "binding": binding,
        "success_probability": None, "comparison_confidence": "TENTATIVE_LIMITED_COVERAGE",
        "limits": [
            "SOURCE_ONLY_TEST_ADAPTER_NOT_PRODUCTION_READY",
            "NO_PROBABILITY_OR_THREE_TRICK_GUARANTEE",
            "ONLY_FIRST_STEP_REPLAN_AFTER_ANY_PUBLIC_PLAY",
            "SIDE_ENTRIES_ARE_A_SUPPLIED_PLAYER_VISIBLE_ASSESSMENT",
            "L1_PAIR_DOES_NOT_PROVE_GLOBAL_OPTIMUM",
        ],
    }


def next_action(decision: Any, observation: Any, eligible_records: Any,
                *, test_only: bool = False) -> dict[str, Any]:
    """Recompute before returning a card; never replay an old cached decision."""
    fresh = decide(observation, eligible_records, test_only=test_only)
    if fresh["binding"] is None:
        return {"status": fresh["status"], "reason": fresh["reason"], "action": None}
    if type(decision) is not dict or decision.get("binding") != fresh["binding"]:
        return {"status": "NEEDS_CONTEXT", "reason": "STALE_DECISION_RECOMPUTE", "action": None}
    if fresh["immediate_action"] is None:
        return {"status": fresh["status"], "reason": fresh["reason"],
                "missing_context": fresh["missing_context"], "action": None}
    return {"status": "ACTION", "reason": "FRESHLY_RECOMPUTED_TEST_ONLY_FIRST_STEP",
            "action": fresh["immediate_action"], "binding": fresh["binding"]}
