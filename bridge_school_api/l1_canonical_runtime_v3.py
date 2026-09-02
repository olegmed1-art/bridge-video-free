from __future__ import annotations

from typing import Any, Iterable

from .l1_canonical_registry import SYSTEM_VERSION
from .l1_canonical_runtime import RuleEvaluation, resolve
from .l1_canonical_runtime_v2 import evaluate as _evaluate_v2

ENGINE_VERSION = "l1-canonical-runtime-v3"

# Second bounded execution wave. Every rule here has an explicit trigger/condition/action
# in the canonical Drive rule table. Qualitative or incomplete rules remain fail-closed in v2.
EXTRA_SOURCE_EXPLICIT_RULE_IDS = frozenset(
    {
        "RULE-L1-CONTRACT-SUCCESS",
        "RULE-L1-OPEN-PASS-THRESHOLD",
        "RULE-L1-MAJOR-FIT-DETECT",
        "RULE-L1-GAME-BALANCE-25",
        "RULE-L1-RESPONDER-STRENGTH-BANDS",
        "RULE-L1-MINOR-OPEN-CHOICE",
        "RULE-L1-MINOR-SUPPORT-BANDS",
        "RULE-L1-MINOR-NT-RESPONSE-BANDS",
        "RULE-L1-SHOW-MAJOR-FIT-FIRST",
        "RULE-L1-NEW-SUIT-MIN-LENGTH",
        "RULE-L1-NEW-SUIT-FORCES-OPENER",
        "RULE-L1-2C-NEGATIVE-2D",
        "RULE-L1-WEAK3-OPEN",
        "RULE-L1-WEAK4-OPEN",
        "RULE-L1-REVALUE-AFTER-FIT",
        "RULE-L1-MINOR-OPENING-PATH",
        "RULE-L1-MINOR-RESPONSE-PRIORITY",
        "RULE-L1-OPENER-SECOND-SUIT-REBID",
        "RULE-L1-OPENER-NT-REBID",
        "RULE-L1-2C-NEGATIVE-OPENER-REBID",
        "RULE-L1-RESPONSE-TO-1LEVEL-OVERCALL-BANDS",
        "RULE-L1-RESPONSE-TO-2LEVEL-OVERCALL-BANDS",
        "RULE-L1-NT-STOPPER-REQUIRED-COMPETITION",
        "RULE-L1-STRONG-DOUBLE-REVEAL-REBID",
        "RULE-L1-STRONG-DOUBLE-RAISE-BANDS",
        "RULE-L1-1NT-OVERCALL-INHERITS-1NT-RESPONSES",
        "RULE-L1-2NT-OVERCALL-RESPONSE",
        "RULE-L1-PREEMPT-OVERCALL-RESPONSE",
        "RULE-L1-3LEVEL-OVERCALL-RESPONSE",
        "RULE-L1-STRONG-DOUBLE-OWN-SUIT-REBID",
        "RULE-L1-STRONG-DOUBLE-NT-REBID",
        "RULE-L1-HCP-CONTRACT-LEVEL-GUIDE",
        "RULE-L1-1NT-STAYMAN-OPENER-RESPONSE",
        "RULE-L1-1NT-STAYMAN-RESPONDER-CONTINUE",
        "RULE-L1-1M-NT-RESPONSE-BANDS-CONTEXT",
        "RULE-L1-PREFER-3NT-OVER-5MINOR-BASIC",
        "RULE-L1-OPENER-PASS-AFTER-RESPONSE-FORCING-CHECK",
        "RULE-L1-NATURAL-VS-ARTIFICIAL-ALERT",
        "RULE-L1-WEAK2-OPENER-ACCEPT-INVITE",
        "RULE-L1-PENALTY-PASS-ON-DOUBLE-CONTEXT",
    }
)


def _result(
    rule_id: str,
    status: str,
    action: Any = None,
    *,
    reason: str | None = None,
    evidence: tuple[str, ...] = (),
    priority: int = 200,
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


def _block(rule_id: str, action: str, reason: str) -> RuleEvaluation:
    return _result(rule_id, "BLOCK", action, reason=reason, priority=0, specificity=0, scope_rank=0)


def _hcp(c: dict[str, Any]) -> int:
    if "HCP" in c:
        return int(c["HCP"])
    return 4 * int(c.get("A", 0)) + 3 * int(c.get("K", 0)) + 2 * int(c.get("Q", 0)) + int(c.get("J", 0))


def _partner_minor(c: dict[str, Any]) -> str | None:
    value = str(c.get("partner_minor") or "").upper()
    return value if value in {"C", "D"} else None


def _called_suit(c: dict[str, Any]) -> str | None:
    value = str(c.get("called_suit") or "").upper()
    return value if value in {"C", "D", "H", "S"} else None


def _evaluate_extra(rule_id: str, c: dict[str, Any]) -> RuleEvaluation:
    h = _hcp(c)

    if rule_id == "RULE-L1-CONTRACT-SUCCESS":
        if "declarer_tricks" not in c or "required_tricks" not in c:
            return _no(rule_id, "declarer_tricks and required_tricks required")
        return _match(
            rule_id,
            bool(int(c["declarer_tricks"]) >= int(c["required_tricks"])),
            evidence=("FACT-L1-SKILL-0019",),
        )

    if rule_id == "RULE-L1-OPEN-PASS-THRESHOLD":
        if int(c.get("lesson_stage", 0)) != 2 or not c.get("ordinary_one_level_opening_model"):
            return _no(rule_id, "lesson_stage=2 ordinary model required")
        return _match(
            rule_id,
            "ONE_LEVEL_OPENING_CANDIDATE" if h >= 12 else "PASS_IN_LESSON2_BRANCH",
            evidence=("FACT-L1-SKILL-0021",),
        )

    if rule_id == "RULE-L1-MAJOR-FIT-DETECT":
        if int(c.get("partner_major_length", 0)) < 5:
            return _no(rule_id, "partner 5-card major required")
        return _match(
            rule_id,
            bool(int(c.get("responder_support", 0)) >= 3),
            evidence=("FACT-L1-SKILL-0023",),
        )

    if rule_id == "RULE-L1-GAME-BALANCE-25":
        if "combined_HCP" not in c:
            return _no(rule_id, "combined_HCP required")
        return _match(rule_id, bool(int(c["combined_HCP"]) >= 25), evidence=("FACT-L1-SKILL-0025",))

    if rule_id == "RULE-L1-RESPONDER-STRENGTH-BANDS":
        if h < 6:
            return _no(rule_id, "6+ HCP response classification")
        action = "MINIMUM" if h <= 10 else "INVITE" if h <= 12 else "MAXIMUM"
        return _match(rule_id, action, evidence=("FACT-L1-SKILL-0026",))

    if rule_id == "RULE-L1-MINOR-OPEN-CHOICE":
        if not c.get("minor_opening_path"):
            return _no(rule_id, "minor opening path required")
        clubs, diamonds = int(c.get("C", -1)), int(c.get("D", -1))
        if clubs < 0 or diamonds < 0:
            return _no(rule_id, "C and D lengths required")
        if clubs > diamonds:
            action = "1C"
        elif diamonds > clubs:
            action = "1D"
        elif clubs in {3, 4}:
            action = "1C"
        elif clubs == 5:
            action = "1D"
        else:
            return _no(rule_id, "tie outside explicit 3-3/4-4/5-5 cases")
        return _match(rule_id, action, evidence=("FACT-L1-SKILL-0031",))

    if rule_id == "RULE-L1-MINOR-SUPPORT-BANDS":
        minor = _partner_minor(c)
        if not minor or not c.get("no_preferred_own_suit") or int(c.get("support_in_partner_minor", 0)) < 5 or h < 6:
            return _no(rule_id, "explicit minor-support branch not met")
        action = f"2{minor}" if h <= 10 else f"3{minor}" if h <= 12 else "3NT"
        return _match(rule_id, action, evidence=("FACT-L1-SKILL-0033",))

    if rule_id == "RULE-L1-MINOR-NT-RESPONSE-BANDS":
        if not c.get("no_preferred_own_suit") or not c.get("no_minor_fit") or h < 6:
            return _no(rule_id, "explicit minor NT branch not met")
        action = "1NT" if h <= 10 else "2NT" if h <= 12 else "3NT"
        return _match(rule_id, action, evidence=("FACT-L1-SKILL-0034",))

    if rule_id == "RULE-L1-SHOW-MAJOR-FIT-FIRST":
        if int(c.get("responder_support", 0)) < 3:
            return _no(rule_id, "3+ support required")
        return _match(rule_id, "RAISE_PARTNER_MAJOR", evidence=("FACT-L1-SKILL-0036",))

    if rule_id == "RULE-L1-NEW-SUIT-MIN-LENGTH":
        if "called_suit_length" not in c:
            return _no(rule_id, "called_suit_length required")
        return _match(rule_id, "ALLOWED" if int(c["called_suit_length"]) >= 4 else "TOO_SHORT", evidence=("FACT-L1-SKILL-0038",))

    if rule_id == "RULE-L1-NEW-SUIT-FORCES-OPENER":
        if not c.get("current_L1_context") or not c.get("partner_response_new_natural_suit"):
            return _no(rule_id, "current L1 new-suit response required")
        return _match(rule_id, "OPENER_MUST_CONTINUE", evidence=("FACT-L1-SKILL-0095",))

    if rule_id == "RULE-L1-2C-NEGATIVE-2D":
        if not c.get("partner_opened_2C") or not (0 <= h <= 7):
            return _no(rule_id, "2C opening and 0-7 HCP required")
        return _match(rule_id, "2D", evidence=("FACT-L1-SKILL-0054",))

    if rule_id in {"RULE-L1-WEAK3-OPEN", "RULE-L1-WEAK4-OPEN"}:
        suit = _called_suit(c)
        required_length = 7 if rule_id.endswith("WEAK3-OPEN") else 8
        level = 3 if required_length == 7 else 4
        if suit is None or not (7 <= h <= 11) or int(c.get("called_suit_length", 0)) != required_length:
            return _no(rule_id, f"7-11 HCP and exactly {required_length}-card called suit required")
        return _match(rule_id, f"{level}{suit}", evidence=("FACT-L1-SKILL-0056",))

    if rule_id == "RULE-L1-REVALUE-AFTER-FIT":
        if not c.get("trump_fit") or "distribution_points" not in c:
            return _no(rule_id, "trump fit and distribution_points required")
        return _match(rule_id, h + int(c["distribution_points"]), evidence=("FACT-L1-SKILL-0111",))

    if rule_id == "RULE-L1-MINOR-OPENING-PATH":
        ok = (
            12 <= h <= 22
            and bool(c.get("no_5_card_major"))
            and bool(c.get("not_1NT_opening"))
            and bool(c.get("not_2NT_opening"))
        )
        return _match(rule_id, "MINOR_OPENING_PATH", evidence=("FACT-L1-SKILL-0030",)) if ok else _no(rule_id, "minor opening path conditions not met")

    if rule_id == "RULE-L1-MINOR-RESPONSE-PRIORITY":
        return _match(rule_id, "OWN_PREFERRED_SUIT>MINOR_FIT>NT", evidence=("FACT-L1-SKILL-0032",))

    if rule_id == "RULE-L1-OPENER-SECOND-SUIT-REBID":
        if not c.get("fit_not_found") or int(c.get("second_suit_length", 0)) < 4 or not (12 <= h <= 22):
            return _no(rule_id, "fit not found, second suit 4+, HCP 12-22 required")
        action = "SECOND_SUIT_NO_JUMP" if h <= 17 else "SECOND_SUIT_JUMP"
        return _match(rule_id, action, evidence=("FACT-L1-SKILL-0043",))

    if rule_id == "RULE-L1-OPENER-NT-REBID":
        if not c.get("no_fit") or not c.get("no_second_4plus_suit") or int(c.get("opening_suit_length", 99)) >= 6:
            return _no(rule_id, "NT rebid shape branch not met")
        if 12 <= h <= 14:
            action = "NT_NO_JUMP"
        elif 18 <= h <= 19:
            action = "NT_JUMP"
        else:
            return _no(rule_id, "only explicit 12-14 or 18-19 bands")
        return _match(rule_id, action, evidence=("FACT-L1-SKILL-0045",))

    if rule_id == "RULE-L1-2C-NEGATIVE-OPENER-REBID":
        if not c.get("auction_2C_2D"):
            return _no(rule_id, "2C-2D auction required")
        if int(c.get("own_suit_length", 0)) >= 5:
            action = "BID_OWN_5PLUS_SUIT"
        elif c.get("balanced") and 23 <= h <= 24:
            action = "2NT"
        elif c.get("balanced") and h >= 25:
            action = "3NT"
        else:
            return _no(rule_id, "no explicit opener rebid branch")
        return _match(rule_id, action, evidence=("FACT-L1-SKILL-0055",))

    if rule_id == "RULE-L1-RESPONSE-TO-1LEVEL-OVERCALL-BANDS":
        action = "PASS" if h <= 7 else "MINIMUM" if h <= 11 else "INVITE" if h <= 14 else "MAXIMUM"
        return _match(rule_id, action, evidence=("FACT-L1-SKILL-0062",))

    if rule_id == "RULE-L1-RESPONSE-TO-2LEVEL-OVERCALL-BANDS":
        action = "PASS" if h <= 10 else "INVITE" if h <= 12 else "MAXIMUM"
        return _match(rule_id, action, evidence=("FACT-L1-SKILL-0063",))

    if rule_id == "RULE-L1-NT-STOPPER-REQUIRED-COMPETITION":
        if not c.get("opponent_suit_known") or not c.get("considering_NT"):
            return _no(rule_id, "competitive NT choice context required")
        if not c.get("stopper_in_opponent_suit"):
            return _block(rule_id, "NT_FORBIDDEN_NO_STOPPER", "source requires a stopper")
        return _match(rule_id, "NT_ALLOWED_BY_STOPPER_GATE", evidence=("FACT-L1-SKILL-0064",))

    if rule_id == "RULE-L1-STRONG-DOUBLE-REVEAL-REBID":
        if not c.get("initial_call_double") or not c.get("partner_responded") or not c.get("doubler_makes_contentful_rebid"):
            return _no(rule_id, "contentful rebid after double/response required")
        return _match(rule_id, "INFER_STRONG_DOUBLE_18PLUS", evidence=("FACT-L1-SKILL-0071",))

    if rule_id == "RULE-L1-STRONG-DOUBLE-RAISE-BANDS":
        if not c.get("partner_suit_fit") or h < 18:
            return _no(rule_id, "strong doubler fit branch requires 18+")
        action = "RAISE_ONE_LEVEL" if h <= 21 else "JUMP_RAISE" if h <= 24 else "GAME"
        return _match(rule_id, action, evidence=("FACT-L1-SKILL-0072",))

    if rule_id == "RULE-L1-1NT-OVERCALL-INHERITS-1NT-RESPONSES":
        if not c.get("partner_overcalled_1NT") or str(c.get("partner_range") or "") != "15-17":
            return _no(rule_id, "partner 1NT overcall 15-17 required")
        return _match(rule_id, "INHERIT_OPENING_1NT_L1", evidence=("FACT-L1-SKILL-0090",))

    if rule_id == "RULE-L1-2NT-OVERCALL-RESPONSE":
        if not c.get("partner_overcalled_2NT") or str(c.get("partner_range") or "") != "15-17":
            return _no(rule_id, "partner 2NT overcall 15-17 required")
        return _match(rule_id, "PASS" if h <= 8 else "GAME_SEARCH", evidence=("FACT-L1-SKILL-0091",))

    if rule_id == "RULE-L1-PREEMPT-OVERCALL-RESPONSE":
        if not c.get("partner_made_preemptive_overcall"):
            return _no(rule_id, "partner preemptive overcall required")
        action = "PASS" if h <= 15 else "INVITE" if h <= 17 else "MAXIMUM"
        return _match(rule_id, action, evidence=("FACT-L1-SKILL-0092",))

    if rule_id == "RULE-L1-3LEVEL-OVERCALL-RESPONSE":
        if not c.get("partner_natural_overcall_level_3"):
            return _no(rule_id, "partner natural 3-level overcall required")
        return _match(rule_id, "PASS" if h <= 9 else "GAME_SEARCH", evidence=("FACT-L1-SKILL-0093",))

    if rule_id == "RULE-L1-STRONG-DOUBLE-OWN-SUIT-REBID":
        if c.get("fit_to_partner") or int(c.get("own_suit_length", 0)) < 5 or h < 18:
            return _no(rule_id, "no fit, own 5+ suit, 18+ required")
        return _match(rule_id, "OWN_SUIT_NO_JUMP" if h <= 21 else "OWN_SUIT_JUMP", evidence=("FACT-L1-SKILL-0102",))

    if rule_id == "RULE-L1-STRONG-DOUBLE-NT-REBID":
        if c.get("fit_to_partner") or c.get("own_5plus_suit") or not c.get("stopper_in_opponent_suit") or h < 18:
            return _no(rule_id, "no fit/no 5+ suit/st stopper/18+ required")
        action = "NT_NO_JUMP" if h <= 21 else "NT_JUMP" if h <= 24 else "3NT"
        return _match(rule_id, action, evidence=("FACT-L1-SKILL-0103",))

    if rule_id == "RULE-L1-HCP-CONTRACT-LEVEL-GUIDE":
        if "combined_HCP" not in c:
            return _no(rule_id, "combined_HCP required")
        points = int(c["combined_HCP"])
        mode = str(c.get("mode") or "").upper()
        trump_ranges = ((19, 20, "1"), (21, 22, "2"), (23, 24, "3"), (25, 28, "4"), (29, 32, "5"), (33, 35, "6"), (36, 40, "7"))
        nt_ranges = ((21, 22, "1NT"), (23, 24, "2NT"), (25, 28, "3NT"), (29, 31, "4NT"), (32, 33, "5NT"), (34, 36, "6NT"), (37, 40, "7NT"))
        ranges = trump_ranges if mode == "TRUMP" else nt_ranges if mode == "NT" else ()
        for low, high, action in ranges:
            if low <= points <= high:
                return _match(rule_id, action, evidence=("FACT-L1-SKILL-0117",))
        return _no(rule_id, "combined HCP outside explicit guide or mode missing")

    if rule_id == "RULE-L1-1NT-STAYMAN-OPENER-RESPONSE":
        if not c.get("auction_1NT_2C_STAYMAN"):
            return _no(rule_id, "1NT-2C Stayman auction required")
        if int(c.get("H", 0)) >= 4:
            action = "SHOW_LOWER_MAJOR"
        elif int(c.get("S", 0)) >= 4:
            action = "SHOW_SPADE_MAJOR"
        else:
            action = "DENY_4CARD_MAJOR"
        return _match(rule_id, action, evidence=("FACT-L1-1NT-STAYMAN-SEMANTICS",))

    if rule_id == "RULE-L1-1NT-STAYMAN-RESPONDER-CONTINUE":
        if not c.get("stayman_response_received") or h < 8 or "fit_found" not in c:
            return _no(rule_id, "Stayman response, 8+, and fit status required")
        if c.get("fit_found"):
            action = "INVITE_MAJOR" if h <= 9 else "GAME_MAJOR"
        else:
            action = "INVITE_NT" if h <= 9 else "GAME_NT"
        return _match(rule_id, action, evidence=("FACT-L1-1NT-STAYMAN-SEMANTICS",))

    if rule_id == "RULE-L1-1M-NT-RESPONSE-BANDS-CONTEXT":
        if not c.get("no_fit") or not c.get("no_preferred_own_suit_in_current_branch") or h < 6:
            return _no(rule_id, "contextual no-fit/no-preferred-suit branch required")
        action = "1NT" if h <= 10 else "2NT" if h <= 12 else "3NT"
        return _match(rule_id, action, evidence=("FACT-L1-SKILL-0028",))

    if rule_id == "RULE-L1-PREFER-3NT-OVER-5MINOR-BASIC":
        if not c.get("minor_fit_context") or not c.get("game_balance_reached"):
            return _no(rule_id, "basic minor game-choice context required")
        return _match(rule_id, "PREFER_3NT_OVER_5MINOR", evidence=("FACT-L1-SKILL-0035",))

    if rule_id == "RULE-L1-OPENER-PASS-AFTER-RESPONSE-FORCING-CHECK":
        response_type = str(c.get("partner_response_type") or "").upper()
        if response_type == "NEW_SUIT":
            return _match(rule_id, "PASS_FORBIDDEN", evidence=("FACT-L1-SKILL-0046", "FACT-L1-SKILL-0095"))
        if response_type in {"LIMITED_RAISE", "NT"}:
            return _match(rule_id, "PASS_MAY_BE_ALLOWED", evidence=("FACT-L1-SKILL-0046",))
        return _no(rule_id, "response type outside explicit forcing check")

    if rule_id == "RULE-L1-NATURAL-VS-ARTIFICIAL-ALERT":
        if c.get("artificial_call"):
            return _match(rule_id, "ALERT_IN_LESSON_SCOPE", evidence=("FACT-L1-SKILL-0052",))
        if c.get("natural_call"):
            return _match(rule_id, "NATURAL_CALL", evidence=("FACT-L1-SKILL-0052",))
        return _no(rule_id, "call classification required")

    if rule_id == "RULE-L1-WEAK2-OPENER-ACCEPT-INVITE":
        if not c.get("partner_invited") or str(c.get("opener_range") or "") != "7-11" or "sufficient_combined_balance" not in c:
            return _no(rule_id, "weak-two invite and balance decision required")
        return _match(rule_id, "ACCEPT_INVITE" if c.get("sufficient_combined_balance") else "STOP", evidence=("FACT-L1-SKILL-0098",))

    if rule_id == "RULE-L1-PENALTY-PASS-ON-DOUBLE-CONTEXT":
        if not c.get("considering_pass") or not c.get("opponent_opened_in_responder_strong_suit") or h < 6:
            return _no(rule_id, "explicit penalty-conversion context and 6+ required")
        return _match(rule_id, "PASS_CONVERTS_TO_PENALTY", evidence=("FACT-L1-SKILL-0104",))

    return _block(rule_id, "V3_HANDLER_MISSING", "registered v3 rule has no handler")


def evaluate(
    rule_id: str,
    context: dict[str, Any],
    *,
    system_version: str = SYSTEM_VERSION,
) -> RuleEvaluation:
    """Evaluate the v3 source-explicit expansion, otherwise delegate to fail-closed v2."""
    if system_version != SYSTEM_VERSION:
        return _evaluate_v2(rule_id, context, system_version=system_version)
    if rule_id in EXTRA_SOURCE_EXPLICIT_RULE_IDS:
        return _evaluate_extra(rule_id, dict(context))
    return _evaluate_v2(rule_id, context, system_version=system_version)


def runtime_status(rule_id: str) -> dict[str, Any]:
    base = {
        "rule_id": rule_id,
        "system_version": SYSTEM_VERSION,
        "engine_version": ENGINE_VERSION,
        "v3_source_explicit": rule_id in EXTRA_SOURCE_EXPLICIT_RULE_IDS,
        "v3_extra_rule_count": len(EXTRA_SOURCE_EXPLICIT_RULE_IDS),
    }
    base_result = _evaluate_v2(rule_id, {})
    base["v2_empty_context_status"] = base_result.status
    base["v2_empty_context_action"] = base_result.action
    return base


def resolve_registered(evaluations: Iterable[RuleEvaluation]) -> RuleEvaluation:
    return resolve(evaluations)
