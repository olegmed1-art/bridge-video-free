from __future__ import annotations

from typing import Any, Iterable

from .l1_canonical_registry import SYSTEM_VERSION
from .l1_canonical_runtime import RuleEvaluation, resolve
from .l1_canonical_runtime_v3 import evaluate as _evaluate_v3

ENGINE_VERSION = "l1-canonical-runtime-v4"

# Final autonomous source-explicit wave. These rules are procedural, structural,
# or guidance rules whose action is already stated in the canonical rule table.
# The one remaining qualitative rule (competitive-strength principle) is intentionally
# not encoded because its source does not define a deterministic sufficiency test.
EXTRA_PROCEDURAL_RULE_IDS = frozenset(
    {
        "RULE-L1-AUCTION-ORDER-FINAL-CONTRACT",
        "RULE-L1-CUEBID-AFTER-TAKEOUT-CONTINUE",
        "RULE-L1-DEAL-STAGES",
        "RULE-L1-DECK-RANK-SUIT-HIERARCHY",
        "RULE-L1-DECLARER-ROLE",
        "RULE-L1-DIRECT-OVERCALL-1S",
        "RULE-L1-DOUBLE-INITIAL-AMBIGUITY",
        "RULE-L1-DOUBLE-MEANING-BY-CONTEXT",
        "RULE-L1-JUMP-OVERCALL-PREEMPT",
        "RULE-L1-LESSON2-RESPONDER-AFTER-1M",
        "RULE-L1-OPENER-BALANCE-AFTER-RESPONSE",
        "RULE-L1-OPENER-REBID-PURPOSE",
        "RULE-L1-OPENER-REPEAT-OWN-SUIT-6PLUS",
        "RULE-L1-OPENER-SUPPORT-RESPONDER-NEW-SUIT",
        "RULE-L1-OVERCALL-2PLUS-LEVEL",
        "RULE-L1-PLAY-AVOID-BLOCKING",
        "RULE-L1-PLAY-DISCARD-LOSER-ON-WINNER",
        "RULE-L1-PLAY-DRAW-TRUMPS-TIMING",
        "RULE-L1-PLAY-ESTABLISH-LONG-SUIT",
        "RULE-L1-PLAY-EXPASS-PLAN",
        "RULE-L1-PLAY-FINESSE-PLAN",
        "RULE-L1-PLAY-PRESERVE-ENTRIES",
        "RULE-L1-PLAY-RUFF-LOSER-SHORT-TRUMP",
        "RULE-L1-PREEMPT-PURPOSE",
        "RULE-L1-RESPONDER-REEVALUATE-AFTER-OPENER-REBID",
        "RULE-L1-SHAPE-CLASSIFICATION",
        "RULE-L1-STRAIN-PRIORITY",
        "RULE-L1-STRONG2C-SUIT-REBID-CONTINUE",
        "RULE-L1-TABLE-CARD-ORIENTATION",
        "RULE-L1-TAKEOUT-DOUBLE-REBID-BALANCE",
    }
)


def _result(
    rule_id: str,
    status: str,
    action: Any = None,
    *,
    reason: str | None = None,
    evidence: tuple[str, ...] = (),
    priority: int = 180,
    specificity: int = 2,
    scope_rank: int = 2,
) -> RuleEvaluation:
    return RuleEvaluation(
        rule_id=rule_id,
        status=status,
        action=action,
        priority=priority,
        specificity=specificity,
        scope_rank=scope_rank,
        evidence=evidence,
        reason=reason,
    )


def _match(rule_id: str, action: Any, *, evidence: tuple[str, ...] = ()) -> RuleEvaluation:
    return _result(rule_id, "MATCH", action, evidence=evidence)


def _no(rule_id: str, reason: str) -> RuleEvaluation:
    return _result(rule_id, "NO_MATCH", reason=reason, priority=0, specificity=0, scope_rank=0)


def _hcp(c: dict[str, Any]) -> int:
    if "HCP" in c:
        return int(c["HCP"])
    return 4 * int(c.get("A", 0)) + 3 * int(c.get("K", 0)) + 2 * int(c.get("Q", 0)) + int(c.get("J", 0))


def _called_suit(c: dict[str, Any]) -> str | None:
    value = str(c.get("called_suit") or "").upper()
    return value if value in {"C", "D", "H", "S"} else None


def _denomination(call: str) -> str | None:
    value = call.strip().upper()
    if value.endswith("NT"):
        return "NT"
    if value and value[-1] in "CDHS":
        return value[-1]
    return None


def _partner(seat: str) -> str | None:
    return {"N": "S", "S": "N", "E": "W", "W": "E"}.get(seat.upper())


def _evaluate_extra(rule_id: str, c: dict[str, Any]) -> RuleEvaluation:
    h = _hcp(c)

    if rule_id == "RULE-L1-AUCTION-ORDER-FINAL-CONTRACT":
        if not c.get("dealer_known"):
            return _no(rule_id, "dealer must be known")
        action: dict[str, Any] = {
            "dealer_starts": True,
            "clockwise": True,
            "bids_must_increase": True,
        }
        if int(c.get("passes_after_last_significant_call", 0)) == 3 and c.get("last_significant_call"):
            action["final_contract"] = c["last_significant_call"]
        return _match(rule_id, action, evidence=("FACT-L1-SKILL-0020",))

    if rule_id == "RULE-L1-CUEBID-AFTER-TAKEOUT-CONTINUE":
        if not c.get("partner_answered_cuebid") or not c.get("game_force_active"):
            return _no(rule_id, "takeout-double cuebid game force required")
        if c.get("has_4_card_major"):
            action = "SHOW_4CARD_MAJOR"
        elif c.get("stopper_in_opponent_suit"):
            action = "NT_WITH_STOPPER"
        else:
            action = "CONTINUE_TO_GAME"
        return _match(rule_id, action, evidence=("FACT-L1-SKILL-0101",))

    if rule_id == "RULE-L1-DEAL-STAGES":
        return _match(rule_id, ("BIDDING", "CONTRACT", "PLAY"), evidence=("FACT-L1-SKILL-0015",))

    if rule_id == "RULE-L1-DECK-RANK-SUIT-HIERARCHY":
        return _match(
            rule_id,
            {
                "deck_size": 52,
                "ranks": ("A", "K", "Q", "J", "10", "9", "8", "7", "6", "5", "4", "3", "2"),
                "suits": ("S", "H", "D", "C"),
                "majors": ("S", "H"),
                "minors": ("D", "C"),
            },
            evidence=("FACT-L1-SKILL-0112",),
        )

    if rule_id == "RULE-L1-DECLARER-ROLE":
        final_denomination = str(c.get("final_denomination") or "").upper()
        winning_side = {str(seat).upper() for seat in c.get("winning_side") or []}
        calls = list(c.get("calls") or [])
        if final_denomination not in {"C", "D", "H", "S", "NT"} or len(winning_side) != 2 or not calls:
            return _no(rule_id, "final denomination, winning side and calls required")
        declarer = None
        for item in calls:
            seat = str(item.get("seat") or "").upper()
            call = str(item.get("call") or "")
            if seat in winning_side and _denomination(call) == final_denomination:
                declarer = seat
                break
        if declarer is None:
            return _no(rule_id, "winning side never named final denomination in supplied calls")
        dummy = _partner(declarer)
        defenders = tuple(seat for seat in ("N", "E", "S", "W") if seat not in winning_side)
        return _match(rule_id, {"declarer": declarer, "dummy": dummy, "defenders": defenders}, evidence=("FACT-L1-SKILL-0017",))

    if rule_id == "RULE-L1-DIRECT-OVERCALL-1S":
        if int(c.get("legal_entry_level", c.get("level", 0))) != 1 or not (10 <= h <= 17) or int(c.get("S", 0)) < 5:
            return _no(rule_id, "level-1 direct 1S overcall requires 10-17 and S>=5")
        return _match(rule_id, "1S", evidence=("FACT-L1-OVERCALL-1LEVEL",))

    if rule_id == "RULE-L1-DOUBLE-INITIAL-AMBIGUITY":
        if not c.get("partner_just_doubled_opening") or c.get("partner_rebid_seen"):
            return _no(rule_id, "initial double before partner rebid required")
        return _match(rule_id, "RESPOND_COMMON_SCHEME_FIRST;DEFER_VARIANT_CLASSIFICATION", evidence=("FACT-L1-SKILL-0070",))

    if rule_id == "RULE-L1-DOUBLE-MEANING-BY-CONTEXT":
        if c.get("direct_double_of_opening"):
            return _match(rule_id, "TAKEOUT_OR_STRONG_L1", evidence=("FACT-L1-SKILL-0065",))
        if c.get("penalty_context"):
            return _match(rule_id, "PENALTY", evidence=("FACT-L1-SKILL-0065",))
        return _no(rule_id, "recognized double context required")

    if rule_id == "RULE-L1-JUMP-OVERCALL-PREEMPT":
        suit = _called_suit(c)
        length = int(c.get("called_suit_length", 0))
        if suit is None or not (6 <= h <= 9) or not c.get("called_suit_has_at_least_two_honors") or length not in {6, 7, 8}:
            return _no(rule_id, "6-9 HCP, 6/7/8-card suit and two honors required")
        level = {6: 2, 7: 3, 8: 4}[length]
        return _match(rule_id, f"{level}{suit}", evidence=("FACT-L1-SKILL-0061",))

    if rule_id == "RULE-L1-LESSON2-RESPONDER-AFTER-1M":
        if int(c.get("lesson_stage", 0)) != 2 or not c.get("partner_opened_1M"):
            return _no(rule_id, "lesson-stage-2 response to 1M required")
        if h <= 5:
            action = "PASS"
        elif int(c.get("support", 0)) >= 3:
            action = "RAISE"
        else:
            action = "LESSON2_NT_BRANCH"
        return _match(rule_id, action, evidence=("FACT-L1-SKILL-0024",))

    if rule_id == "RULE-L1-OPENER-BALANCE-AFTER-RESPONSE":
        if not c.get("partner_response_range_known") or "HCP" not in c:
            return _no(rule_id, "own HCP and partner response range required")
        return _match(rule_id, "COMBINE_RANGES_THEN_CHOOSE_STOP_OR_ACCEPT_INVITE_OR_GAME", evidence=("FACT-L1-SKILL-0029",))

    if rule_id == "RULE-L1-OPENER-REBID-PURPOSE":
        if not c.get("partner_response_known"):
            return _no(rule_id, "partner response required")
        return _match(rule_id, "DESCRIBE_ADDITIONAL_STRENGTH_AND_SHAPE_INFORMATION", evidence=("FACT-L1-SKILL-0041",))

    if rule_id == "RULE-L1-OPENER-REPEAT-OWN-SUIT-6PLUS":
        if not c.get("fit_not_found") or int(c.get("opening_suit_length", 0)) < 6:
            return _no(rule_id, "fit not found and opening suit 6+ required")
        return _match(rule_id, "REPEAT_OPENING_SUIT", evidence=("FACT-L1-SKILL-0044",))

    if rule_id == "RULE-L1-OPENER-SUPPORT-RESPONDER-NEW-SUIT":
        if not c.get("partner_response_new_suit") or int(c.get("own_support_in_responder_suit", 0)) < 4:
            return _no(rule_id, "responder new suit and opener 4+ support required")
        return _match(rule_id, "SHOW_4PLUS_FIT", evidence=("FACT-L1-SKILL-0042",))

    if rule_id == "RULE-L1-OVERCALL-2PLUS-LEVEL":
        suit = _called_suit(c)
        level = int(c.get("legal_entry_level", 0))
        length = int(c.get("called_suit_length", 0))
        if suit is None or length < 5 or level < 2:
            return _no(rule_id, "called 5+ suit and legal entry level >=2 required")
        if level == 2:
            ok = 12 <= h <= 17
        else:
            ok = 14 <= h <= 17 or (bool(c.get("strong_suit_exception")) and 12 <= h <= 17)
        if not ok:
            return _no(rule_id, "HCP band not met for entry level")
        return _match(rule_id, f"{level}{suit}", evidence=("FACT-L1-SKILL-0060",))

    if rule_id == "RULE-L1-PLAY-AVOID-BLOCKING":
        if not c.get("entry_or_access_risk"):
            return _no(rule_id, "entry/access risk required")
        return _match(rule_id, "CHOOSE_ACCESS_PRESERVING_ORDER", evidence=("FACT-L1-SKILL-0004",))

    if rule_id == "RULE-L1-PLAY-DISCARD-LOSER-ON-WINNER":
        if not c.get("loser_identified") or not c.get("side_suit_winner_available_or_developable"):
            return _no(rule_id, "loser and side-suit winner required")
        return _match(rule_id, "PLAN_DISCARD_LOSER_ON_SIDE_WINNER", evidence=("FACT-L1-SKILL-0009",))

    if rule_id == "RULE-L1-PLAY-DRAW-TRUMPS-TIMING":
        if not c.get("defender_trumps_remaining"):
            return _no(rule_id, "defender trumps remaining required")
        action = "DELAY_TRUMP_DRAW_FOR_REQUIRED_LOSS_ELIMINATION" if c.get("required_loss_elimination_first") else "DRAW_TRUMPS_WHEN_PLAN_SAFE"
        return _match(rule_id, action, evidence=("FACT-L1-SKILL-0011",))

    if rule_id == "RULE-L1-PLAY-ESTABLISH-LONG-SUIT":
        if not c.get("long_suit_can_produce_extra_tricks"):
            return _no(rule_id, "long suit extra-trick potential required")
        return _match(rule_id, "DEVELOP_LONG_SUIT_BEFORE_UNRELATED_CASHES_WHEN_REQUIRED", evidence=("FACT-L1-SKILL-0007",))

    if rule_id == "RULE-L1-PLAY-EXPASS-PLAN":
        if not c.get("expass_position_recognized") or not c.get("key_honor_with_relevant_defender"):
            return _no(rule_id, "expass position and relevant honor required")
        action = "PLAN_EXPASSE;ACCOUNT_FOR_POSSIBLE_RUFF" if c.get("contract_trump") else "PLAN_EXPASSE"
        return _match(rule_id, action, evidence=("FACT-L1-SKILL-0006",))

    if rule_id == "RULE-L1-PLAY-FINESSE-PLAN":
        if not c.get("finesse_position_recognized") or not c.get("missing_honor_location_relevant"):
            return _no(rule_id, "finesse position and relevant missing honor required")
        return _match(rule_id, "EVALUATE_FINESSE_DIRECTION_AND_ENTRIES", evidence=("FACT-L1-SKILL-0005",))

    if rule_id == "RULE-L1-PLAY-PRESERVE-ENTRIES":
        if not c.get("planned_target_hand_known"):
            return _no(rule_id, "planned target hand required")
        return _match(rule_id, "IDENTIFY_AND_PRESERVE_REQUIRED_ENTRY", evidence=("FACT-L1-SKILL-0003",))

    if rule_id == "RULE-L1-PLAY-RUFF-LOSER-SHORT-TRUMP":
        if not c.get("loser_identified") or not c.get("short_trump_hand_has_available_trump") or not c.get("ruff_gains_trick"):
            return _no(rule_id, "loser, short-hand trump and trick gain required")
        return _match(rule_id, "PLAN_RUFF_LOSER_IN_SHORT_TRUMP_HAND", evidence=("FACT-L1-SKILL-0010",))

    if rule_id == "RULE-L1-PREEMPT-PURPOSE":
        if not c.get("weak_hand_with_long_suit"):
            return _no(rule_id, "weak hand with long suit required")
        return _match(rule_id, "USE_PREEMPT_FOR_SPACE_DENIAL_WITHIN_DEFINED_CONSTRAINTS", evidence=("FACT-L1-SKILL-0057",))

    if rule_id == "RULE-L1-RESPONDER-REEVALUATE-AFTER-OPENER-REBID":
        if not c.get("opener_rebid_received") or not c.get("new_opener_range_or_shape_info_available"):
            return _no(rule_id, "opener rebid with new range/shape information required")
        return _match(rule_id, "RECOMPUTE_COMBINED_PICTURE_THEN_CHOOSE_STOP_INVITE_GAME", evidence=("FACT-L1-SKILL-0105",))

    if rule_id == "RULE-L1-SHAPE-CLASSIFICATION":
        lengths = [int(c.get(s, -1)) for s in "SHDC"]
        if any(value < 0 for value in lengths) or sum(lengths) != 13:
            return _no(rule_id, "complete 13-card SHDC lengths required")
        ordered = sorted(lengths, reverse=True)
        shortness = {
            "voids": lengths.count(0),
            "singletons": lengths.count(1),
            "doubletons": lengths.count(2),
        }
        if ordered in ([4, 3, 3, 3], [4, 4, 3, 2], [5, 3, 3, 2]):
            shape_class = "BALANCED"
        elif 0 in lengths or 1 in lengths:
            shape_class = "UNBALANCED_SHORTNESS"
        elif lengths.count(2) >= 2:
            shape_class = "SEMIBALANCED_TWO_DOUBLETONS"
        else:
            return _no(rule_id, "shape outside explicit L1 taxonomy examples")
        return _match(rule_id, {"shape_class": shape_class, **shortness}, evidence=("FACT-L1-SKILL-0109",))

    if rule_id == "RULE-L1-STRAIN-PRIORITY":
        if not c.get("multiple_suitable_contracts"):
            return _no(rule_id, "multiple suitable contracts required")
        if c.get("lesson_supported_exception"):
            return _no(rule_id, "explicit lesson-supported exception defers generic priority")
        return _match(rule_id, "MAJOR>NT>MINOR", evidence=("FACT-L1-SKILL-0040",))

    if rule_id == "RULE-L1-STRONG2C-SUIT-REBID-CONTINUE":
        if not c.get("strong2C_opener_rebid_suit"):
            return _no(rule_id, "strong 2C opener suit rebid required")
        if c.get("fit"):
            action = "SUPPORT"
        elif int(c.get("own_suit_length", 0)) >= 5:
            action = "SHOW_OWN_SUIT"
        elif c.get("balanced"):
            action = "NT"
        else:
            return _no(rule_id, "no explicit continuation branch")
        return _match(rule_id, action, evidence=("FACT-L1-SKILL-0058",))

    if rule_id == "RULE-L1-TABLE-CARD-ORIENTATION":
        return _match(
            rule_id,
            {"use_bidding_box": True, "won_trick": "VERTICAL", "lost_trick": "HORIZONTAL"},
            evidence=("FACT-L1-SKILL-0113",),
        )

    if rule_id == "RULE-L1-TAKEOUT-DOUBLE-REBID-BALANCE":
        if not c.get("takeout_doubler") or not c.get("partner_responded") or str(c.get("doubler_range") or "") != "13-17":
            return _no(rule_id, "ordinary takeout doubler 13-17 after partner response required")
        return _match(rule_id, "COMBINE_RANGES_THEN_PASS_OR_GAME", evidence=("FACT-L1-SKILL-0100",))

    return _no(rule_id, "v4 handler missing")


def evaluate(
    rule_id: str,
    context: dict[str, Any],
    *,
    system_version: str = SYSTEM_VERSION,
) -> RuleEvaluation:
    if system_version != SYSTEM_VERSION:
        return _evaluate_v3(rule_id, context, system_version=system_version)
    if rule_id in EXTRA_PROCEDURAL_RULE_IDS:
        return _evaluate_extra(rule_id, dict(context))
    return _evaluate_v3(rule_id, context, system_version=system_version)


def resolve_registered(evaluations: Iterable[RuleEvaluation]) -> RuleEvaluation:
    return resolve(evaluations)
