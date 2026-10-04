"""A bounded teacher decision path, with no DB, network, L1 or activation writes.

Points are supplied synthetic premises, never computed from card honors. This
experiment cannot establish a school point-counting method or production policy.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

PROFILE = "SCHOOL_TOURNAMENT_CURRENT_V1"
MODE = "TOURNAMENT_TEACHER_TEST_ONLY"
ENGINE = "tournament-teacher-test-v1"
RULES_PATH = Path(__file__).with_name("rules.json")


def decide(request: dict, *, enabled: bool = False) -> dict:
    """Assess a proposed call, or recommend only an explicitly confirmed choice.

    Exact full auctions include opponent passes. Only the current actor's hand
    is accepted. Unknown input keys fail closed rather than accepting hidden
    hands, inferred points, a caller-selected rule or a priority override.
    """
    result = {
        "engine": ENGINE, "mode": MODE, "system_profile": PROFILE,
        "status": "ABSTAIN", "action": None, "reason": None,
        "formal_db_activation_asserted": False, "production_ready": False,
        "fallback_performed": False, "checks": [],
    }

    def stop(reason: str) -> dict:
        result["reason"] = reason
        return result

    if not enabled:
        return stop("TEST_MODE_DISABLED")
    if not isinstance(request, dict):
        return stop("INVALID_REQUEST")
    allowed = {"mode", "system_profile", "task", "dealer", "actor", "auction",
               "hand", "proposed_call", "school_points"}
    if set(request) - allowed:
        return stop("UNSUPPORTED_CONTEXT_FIELDS")
    if request.get("mode") != MODE or request.get("system_profile") != PROFILE:
        return stop("MODE_OR_PROFILE_GAP")
    task = request.get("task")
    if task not in ("assess_call", "recommend"):
        return stop("UNSUPPORTED_TASK")
    if task == "recommend" and "proposed_call" in request:
        return stop("RECOMMENDATION_MUST_NOT_BE_CANDIDATE_FILTERED")
    if task == "assess_call" and not isinstance(request.get("proposed_call"), str):
        return stop("PROPOSED_CALL_REQUIRED")
    dealer, actor, auction = (request.get(k) for k in ("dealer", "actor", "auction"))
    if dealer not in ("N", "E", "S", "W") or actor not in ("N", "E", "S", "W"):
        return stop("SEAT_CONTEXT_REQUIRED")
    if not isinstance(auction, list) or not all(isinstance(c, str) for c in auction):
        return stop("FULL_AUCTION_REQUIRED")
    if actor != "NESW"[("NESW".index(dealer) + len(auction)) % 4]:
        return stop("NOT_ACTORS_TURN")
    supported = (["1NT", "PASS"], ["1H", "PASS", "1NT", "PASS"],
                 ["1S", "PASS", "1NT", "PASS"])
    if auction not in supported:
        return stop("UNSUPPORTED_AUCTION")
    hand = request.get("hand")
    deck = {s + r for s in "SHDC" for r in "23456789TJQKA"}
    if (not isinstance(hand, list) or len(hand) != 13
            or not all(isinstance(c, str) and c in deck for c in hand)
            or len(set(hand)) != 13):
        return stop("THIRTEEN_UNIQUE_OWN_CARDS_REQUIRED")
    shape = {s: sum(c[0] == s for c in hand) for s in "SHDC"}
    points = request.get("school_points")
    if points is not None and (
        not isinstance(points, dict) or set(points) != {"value", "basis"}
        or points.get("basis") != "SYNTHETIC_SCHOOL_POINTS"
        or type(points.get("value")) is not int or points["value"] < 0
    ):
        return stop("POINT_METHOD_UNRESOLVED_USE_ONLY_SYNTHETIC_PREMISE")
    rules = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    candidates = [r for r in rules if r["auction"] == auction]
    if task == "assess_call":
        candidates = [r for r in candidates if r["call"] == request["proposed_call"]]
    if not candidates:
        return stop("UNSUPPORTED_CALL")
    result["observed_shape"] = shape
    result["point_method"] = "UNRESOLVED_SCHOOL_POINT_METHOD"
    result["synthetic_point_premise"] = deepcopy(points)
    for rule in candidates:
        kind = rule["predicate"]
        evidence = {"shape": shape.copy()}
        if kind == "singleton_minors":
            fits = (shape[rule["call"][-1]] == 1
                    and sorted(shape.values()) == [1, 3, 4, 5]
                    and sorted([shape["C"], shape["D"]]) == [4, 5])
        elif kind == "stayman":
            # Five-card-major agreements are unresolved; no >=4 inference.
            if max(shape["H"], shape["S"]) > 4:
                fits = None
            elif shape["H"] != 4 and shape["S"] != 4:
                fits = False
            else:
                fits = None if points is None else points["value"] >= 8
        elif kind == "invite":
            fits = None if points is None else 8 <= points["value"] <= 9
        elif kind == "major_rebid":
            shape_fits = (shape[auction[0][-1]] == 5
                          and sorted(shape.values()) == [2, 3, 3, 5])
            fits = False if not shape_fits else (
                None if points is None else 15 <= points["value"] <= 17)
        else:
            return stop("UNSUPPORTED_RULE_PREDICATE")
        if kind != "singleton_minors":
            evidence["school_points"] = deepcopy(points)
        check = deepcopy(rule)
        check.update({"status": "UNKNOWN" if fits is None else (
            "CONSISTENT" if fits else "INCONSISTENT"), "observed": evidence})
        check["explanation"] = (rule["meaning"] + " " + {
            "UNKNOWN": "Недостаточно подтверждённого контекста для проверки условия.",
            "CONSISTENT": "Указанная рука соответствует проверенным условиям этой заявки.",
            "INCONSISTENT": "Указанная рука не соответствует проверенным условиям этой заявки.",
        }[check["status"]])
        result["checks"].append(check)
    if task == "assess_call":
        status = result["checks"][0]["status"]
        result["status"] = {"CONSISTENT": "SUPPORTED", "INCONSISTENT": "CONTRADICTED",
                            "UNKNOWN": "ABSTAIN"}[status]
        return stop("CONDITIONS_ONLY_NOT_UNIQUE_CHOICE" if status != "UNKNOWN"
                    else "MISSING_OR_UNRESOLVED_CONTEXT")
    matches = [c for c in result["checks"] if c["status"] == "CONSISTENT"]
    if any(c["status"] == "UNKNOWN" for c in result["checks"]):
        return stop("MISSING_OR_UNRESOLVED_CONTEXT")
    if len(matches) == 1 and matches[0]["explicit_choice"]:
        result.update(status="RECOMMEND", action=matches[0]["call"])
        return stop("EXPLICIT_TEACHER_CHOICE_UNDER_SYNTHETIC_PREMISES")
    return stop("NO_CONFIRMED_SELECTION_POLICY" if matches else "NO_SUPPORTED_CHOICE")
