from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Iterable

SYSTEM_VERSION = "SCHOOL_L1_DB_V1"


@dataclass(frozen=True)
class RuleEvaluation:
    rule_id: str
    status: str
    action: Any = None
    priority: int = 100
    specificity: int = 0
    scope_rank: int = 0
    evidence: tuple[str, ...] = field(default_factory=tuple)
    reason: str | None = None

    @property
    def matched(self) -> bool:
        return self.status == "MATCH"


def _result(rule_id: str, status: str, action: Any = None, *, priority: int = 100,
            specificity: int = 1, scope_rank: int = 1, evidence: tuple[str, ...] = (),
            reason: str | None = None) -> RuleEvaluation:
    return RuleEvaluation(rule_id, status, action, priority, specificity, scope_rank, evidence, reason)


def _match(rule_id: str, action: Any, *, evidence: tuple[str, ...] = (), priority: int = 100,
           specificity: int = 1, scope_rank: int = 1) -> RuleEvaluation:
    return _result(rule_id, "MATCH", action, evidence=evidence, priority=priority,
                   specificity=specificity, scope_rank=scope_rank)


def _no(rule_id: str, reason: str) -> RuleEvaluation:
    return _result(rule_id, "NO_MATCH", reason=reason, specificity=0, scope_rank=0)


def _block(rule_id: str, action: str, reason: str) -> RuleEvaluation:
    return _result(rule_id, "BLOCK", action, reason=reason, specificity=0, scope_rank=0)


def _canon_conflict(rule_id: str, reason: str) -> RuleEvaluation:
    """Return the authority-bound conflict surface with no School action."""
    return _result(
        rule_id,
        "CANON_CONFLICT",
        action=None,
        reason=reason,
        specificity=0,
        scope_rank=0,
    )


def _hcp(c: dict[str, Any]) -> int:
    if "HCP" in c:
        return c["HCP"]
    counts = [c[rank] for rank in "AKQJ"]
    if sum(counts) > 13:
        raise _ContextRequired("honor count exceeds one hand")
    return sum(count * weight for count, weight in zip(counts, (4, 3, 2, 1)))


def _shape(c: dict[str, Any]) -> tuple[int, int, int, int]:
    if "shape" in c:
        shape = c["shape"]
        if len(shape) != 4 or any(x not in "0123456789" for x in shape):
            raise _ContextRequired("shape must contain four ASCII suit lengths")
        d = tuple(int(x) for x in shape)
        if sum(d) != 13:
            raise _ContextRequired("shape must total thirteen")
        return d  # type: ignore[return-value]
    lengths = tuple(c[s] for s in "SHDC")
    if sum(lengths) != 13:
        raise _ContextRequired("shape must total thirteen")
    return lengths


def _balanced(c: dict[str, Any]) -> bool:
    if "balanced" in c:
        return bool(c["balanced"])
    return sorted(_shape(c), reverse=True) in ([4, 3, 3, 3], [4, 4, 3, 2], [5, 3, 3, 2])


def _no5m(c: dict[str, Any]) -> bool:
    return bool(c["no_5_card_major"]) if "no_5_card_major" in c else int(c.get("S", 0)) < 5 and int(c.get("H", 0)) < 5


def _called_len(c: dict[str, Any]) -> int:
    if "called_suit_length" in c:
        return int(c["called_suit_length"])
    s = str(c.get("called_suit") or "").upper()
    return int(c.get(s, 0)) if s in "SHDC" else 0


def _card_suit(card: str) -> str:
    x = card.strip().upper()
    for glyph, suit in (("♠", "S"), ("♥", "H"), ("♦", "D"), ("♣", "C")):
        if x.startswith((glyph, suit)):
            return suit
    raise ValueError(f"unsupported card: {card}")


def _rank(card: str) -> int:
    value = card.strip().upper()
    _card_suit(value)
    rank = value[1:]
    return {"2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
            "T": 10, "10": 10, "J": 11, "Q": 12, "K": 13, "A": 14}[rank]


def _evaluate_unchecked(rule_id: str, context: dict[str, Any], *, system_version: str = SYSTEM_VERSION) -> RuleEvaluation:
    c = context
    if system_version != SYSTEM_VERSION:
        return _no(rule_id, f"system isolation: {system_version}")

    if rule_id == "RULE-L1-HCP-COUNT":
        h = _hcp(c)
        return _match(rule_id, h, evidence=("FACT-L1-HCP-VALUES",))
    if rule_id == "RULE-L1-CONTRACT-REQUIRED-TRICKS":
        level = int(c.get("contract_level", 0))
        return _match(rule_id, 6 + level, evidence=("FACT-L1-SKILL-0016",)) if 1 <= level <= 7 else _no(rule_id, "level 1..7 required")
    if rule_id == "RULE-L1-TRICK-WINNER":
        cards = list(c.get("cards") or [])
        if len(cards) != 4:
            return _no(rule_id, "four cards required")
        identities = [(_card_suit(card), _rank(card)) for card in cards]
        if len(set(identities)) != 4:
            return _no(rule_id, "four distinct cards required")
        lead = str(c["lead"]).upper() if "lead" in c else identities[0][0]
        if lead not in {"C", "D", "H", "S"} or lead != identities[0][0]:
            return _no(rule_id, "lead must match first played card")
        trump = str(c["trump"]).upper()
        if trump not in {"C", "D", "H", "S", "NT"}:
            return _no(rule_id, "valid trump or explicit NT required")
        trumps = [x for x in cards if trump != "NT" and _card_suit(x) == trump]
        pool = trumps or [x for x in cards if _card_suit(x) == lead]
        return _match(rule_id, max(pool, key=_rank), evidence=("FACT-L1-TRICK-WINNER-BASIC",))
    if rule_id == "RULE-L1-OPEN-1H":
        h = _hcp(c)
        return _match(rule_id, "1H", specificity=3, evidence=("FACT-L1-OPEN-1MAJOR",)) if 12 <= h <= 22 and int(c.get("H", 0)) >= 5 and int(c.get("S", 0)) < 5 else _no(rule_id, "12-22, H>=5, S<5")
    if rule_id == "RULE-L1-OPEN-1S":
        h = _hcp(c)
        return _match(rule_id, "1S", specificity=3, evidence=("FACT-L1-OPEN-1MAJOR",)) if 12 <= h <= 22 and int(c.get("S", 0)) >= 5 else _no(rule_id, "12-22, S>=5")
    if rule_id == "RULE-L1-OPEN-1NT":
        h = _hcp(c)
        return _match(rule_id, "1NT", specificity=4, evidence=("FACT-L1-SKILL-0047",)) if 15 <= h <= 17 and _balanced(c) and _no5m(c) else _no(rule_id, "15-17 balanced no 5M")
    if rule_id == "RULE-L1-OPEN-2NT":
        h = _hcp(c)
        return _match(rule_id, "2NT", specificity=4, evidence=("FACT-L1-SKILL-0106",)) if 20 <= h <= 22 and _balanced(c) and _no5m(c) else _no(rule_id, "20-22 balanced no 5M")
    if rule_id == "RULE-L1-WEAK2-OPEN":
        h = _hcp(c)
        s = str(c["called_suit"]).upper()
        if s not in {"C", "D", "H", "S"}:
            return _no(rule_id, "called suit required")
        return _match(rule_id, f"2{s}", specificity=5, evidence=("FACT-L1-SKILL-0056",)) if 7 <= h <= 11 and _called_len(c) == 6 else _no(rule_id, "7-11 and six-card suit")
    if rule_id == "RULE-L1-OPEN-2C-HCP-BRANCH":
        h = _hcp(c)
        return _match(rule_id, "2C", specificity=5, evidence=("FACT-L1-SKILL-0053",)) if h >= 23 else _no(rule_id, "23+")
    if rule_id == "RULE-L1-MAJOR-RAISE-BANDS":
        h = _hcp(c)
        if int(c.get("support", 0)) < 3 or h < 6:
            return _no(rule_id, "3+ support and 6+")
        major = str(c["major"] if "major" in c else c["partner_major"]).upper()
        if major not in {"H", "S"}:
            return _no(rule_id, "major H or S required")
        return _match(rule_id, f"{2 if h <= 10 else 3 if h <= 12 else 4}{major}", specificity=4, evidence=("FACT-L1-SKILL-0027",))
    if rule_id == "RULE-L1-SEARCH-4CARD-MAJOR-AFTER-MINOR":
        return _match(rule_id, f"1{str(c.get('major') or 'H').upper()}", specificity=3, evidence=("FACT-L1-SKILL-0037",)) if c.get("has_4_card_major") else _no(rule_id, "no 4-card major")
    if rule_id == "RULE-L1-NEW-SUIT-HCP-BY-LEVEL":
        h = _hcp(c)
        level = int(c.get("level", 0)); need = 6 if level == 1 else 11 if level == 2 else 999
        return _match(rule_id, "ALLOWED", evidence=("FACT-L1-SKILL-0039",)) if h >= need else _no(rule_id, f"requires {need}+")
    if rule_id == "RULE-L1-FALLBACK-1NT-6-10":
        h = _hcp(c)
        return _match(rule_id, "1NT", specificity=5, evidence=("FACT-L1-SKILL-0094",)) if 6 <= h <= 10 and c.get("no_fit") and c.get("own_suit_requires_level2") else _no(rule_id, "fallback conditions not met")
    if rule_id == "RULE-L1-JUMP-NEW-SUIT-13PLUS-5PLUS":
        h = _hcp(c)
        return _match(rule_id, "JUMP_NEW_SUIT", specificity=4, evidence=("FACT-L1-SKILL-0096",)) if c.get("jump_new_suit") and h >= 13 and int(c.get("suit_length", 0)) >= 5 else _no(rule_id, "13+/5+ jump required")
    if rule_id == "RULE-L1-1NT-NATURAL-RESPONSES":
        h = _hcp(c)
        if not c.get("no_4plus_major"):
            return _no(rule_id, "natural branch only")
        return _match(rule_id, "PASS" if h <= 7 else "2NT" if h <= 9 else "3NT", specificity=3, evidence=("FACT-L1-SKILL-0048",))
    if rule_id == "RULE-L1-1NT-STAYMAN":
        h = _hcp(c)
        return _match(rule_id, "2C_STAYMAN", specificity=5, evidence=("FACT-L1-1NT-STAYMAN-SEMANTICS",)) if h >= 8 and c.get("has_4_card_major") and not c.get("has_5plus_major") else _no(rule_id, "8+, 4M, no 5+M")
    if rule_id == "RULE-L1-1NT-TRANSFER-ENTRY-GENERIC":
        target = str(c.get("target_major") or "").upper()
        return _match(rule_id, f"TRANSFER_ONE_STEP_BELOW_{target};OPENER_MUST_BID_{target}", specificity=5, evidence=("FACT-L1-1NT-TRANSFER-SEMANTICS",)) if target in {"H", "S"} and int(c.get(target, 0)) >= 5 else _no(rule_id, "5+ target major")
    if rule_id == "RULE-L1-1NT-TRANSFER-SECOND-BID":
        h = _hcp(c)
        length = int(c.get("target_major_length", 0))
        if length < 5:
            return _no(rule_id, "5+ required")
        band = "STOP" if h <= 7 else "INVITE" if h <= 9 else "GAME"
        branch = "NT_CHOICE_BRANCH" if length == 5 and band != "STOP" else "MAJOR_BRANCH"
        return _match(rule_id, f"{band};{branch}", specificity=5, evidence=("FACT-L1-1NT-TRANSFER-CONTINUATION",))
    if rule_id == "RULE-L1-2NT-GAME-BALANCE":
        h = _hcp(c)
        if h <= 4 and c.get("no_major_action"):
            return _match(rule_id, "PASS", evidence=("FACT-L1-SKILL-0107",))
        return _match(rule_id, "GAME_BALANCE", evidence=("FACT-L1-SKILL-0107",)) if h >= 5 else _no(rule_id, "incomplete context")
    if rule_id == "RULE-L1-2NT-TRANSFER-STAYMAN-SHIFT":
        h = _hcp(c)
        if h >= 5 and c.get("has_4_card_major"):
            return _match(rule_id, "STAYMAN_AT_3_LEVEL", evidence=("FACT-L1-2NT-CONVENTION-SHIFT",))
        return _match(rule_id, "TRANSFER_AT_3_LEVEL", evidence=("FACT-L1-2NT-CONVENTION-SHIFT",)) if c.get("has_5plus_major") else _no(rule_id, "no convention branch")
    if rule_id == "RULE-L1-DIRECT-OVERCALL-1SUIT":
        h = _hcp(c)
        s = str(c.get("called_suit") or "S").upper()
        return _match(rule_id, f"1{s}", specificity=4, evidence=("FACT-L1-OVERCALL-1LEVEL",)) if int(c.get("legal_entry_level", 0)) == 1 and 10 <= h <= 17 and _called_len(c) >= 5 else _no(rule_id, "level1 10-17 5+")
    if rule_id == "RULE-L1-TAKEOUT-DOUBLE":
        h = _hcp(c)
        ok = c.get("opponent_opened") and 13 <= h <= 17 and int(c.get("each_unbid_suit", 0)) >= 3 and c.get("no_suitable_suit_overcall") and c.get("no_suitable_NT")
        return _match(rule_id, "DOUBLE_TAKEOUT", specificity=6, evidence=("FACT-L1-SKILL-0066",)) if ok else _no(rule_id, "takeout gate not met")
    if rule_id == "RULE-L1-STRONG-DOUBLE-18PLUS":
        h = _hcp(c)
        return _match(rule_id, "DOUBLE_STRONG", specificity=5, evidence=("FACT-L1-SKILL-0069",)) if c.get("opponent_opened") and h >= 18 else _no(rule_id, "opponent opening and 18+")
    if rule_id == "RULE-L1-DOUBLE-SUIT-RESPONSE-BANDS":
        h = _hcp(c)
        if h >= 12 and int(c.get("major_length", 0)) >= 5:
            return _match(rule_id, "4M", specificity=5, evidence=("FACT-L1-SKILL-0073",))
        if int(c.get("best_suit_length", 0)) < 4:
            return _no(rule_id, "4+ best suit required")
        return _match(rule_id, "SUIT_NEAREST" if h <= 8 else "JUMP_SUIT" if h <= 11 else "UNRESOLVED_12PLUS_NO5M", evidence=("FACT-L1-SKILL-0073",))
    if rule_id == "RULE-L1-DOUBLE-NT-RESPONSE-BANDS":
        h = _hcp(c)
        if not c.get("stopper") or h < 6:
            return _no(rule_id, "6+ and stopper")
        return _match(rule_id, "1NT" if h <= 9 else "2NT" if h <= 11 else "3NT", specificity=4, evidence=("FACT-L1-SKILL-0074",))
    if rule_id == "RULE-L1-TAKEOUT-DOUBLE-NT-OVER-MINOR":
        return _match(rule_id, "PREFER_NT_OVER_MINOR", priority=250, specificity=5, evidence=("FACT-L1-DOUBLE-NT-OVER-MINOR",)) if c.get("stopper") and c.get("minor_option") else _no(rule_id, "priority condition not met")
    if rule_id == "RULE-L1-DOUBLE-CUEBID-GAME-FORCE":
        h = _hcp(c)
        ok = h >= 12 and not c.get("has_5plus_major") and not c.get("stopper")
        return _match(rule_id, "CUEBID;FG", specificity=6, evidence=("FACT-L1-DOUBLE-CUEBID-BRANCH",)) if ok else _no(rule_id, "explicit strong cuebid branch not met")
    if rule_id == "RULE-L1-TAKEOUT-DOUBLE-FORCED-RESPONSE":
        return _match(rule_id, "PASS_FORBIDDEN", specificity=4, evidence=("FACT-L1-SKILL-0067",)) if c.get("partner_doubled_opening") and c.get("RHO_passed") else _no(rule_id, "not forced")
    if rule_id == "RULE-L1-TAKEOUT-DOUBLE-FREE-POSITION":
        ok = c.get("partner_doubled_opening") and c.get("RHO_made_meaningful_bid") and c.get("weak_unsuitable_hand")
        return _match(rule_id, "PASS_ALLOWED", specificity=4, evidence=("FACT-L1-SKILL-0068",)) if ok else _no(rule_id, "not free-position pass")
    if rule_id == "RULE-L1-NT-OVERCALL-15-17-STOPPER":
        h = _hcp(c)
        level = int(c.get("opponent_opening_level", 0))
        return _match(rule_id, f"{level}NT", specificity=5, evidence=("FACT-L1-SKILL-0089",)) if 1 <= level <= 3 and 15 <= h <= 17 and c.get("balanced") and c.get("stopper") else _no(rule_id, "NT overcall gate not met")
    if rule_id == "RULE-L1-WEAK2-RESPONSE-BANDS":
        h = _hcp(c)
        return _match(rule_id, "PASS" if h <= 14 else "INVITE" if h <= 17 else "MAXIMUM", evidence=("FACT-L1-SKILL-0097",))
    if rule_id == "RULE-L1-WEAK3-RESPONSE-BRANCHES":
        h = _hcp(c)
        action = ("PASS" if h <= 14 else "GAME") if c.get("fit") else ("PASS" if h <= 16 else "3NT")
        return _match(rule_id, action, evidence=("FACT-L1-SKILL-0099",))
    if rule_id == "RULE-L1-DISTRIBUTION-POINTS-AFTER-FIT":
        if not c.get("trump_fit") or int(c.get("combined_trumps", 0)) < 8:
            return _no(rule_id, "8+ fit required")
        role = str(c.get("hand_role") or "")
        table = {"long_trump": {"void": 3, "singleton": 1, "doubleton": 0}, "short_trump": {"void": 5, "singleton": 3, "doubleton": 1}}
        if role not in table:
            return _no(rule_id, "hand role required")
        points = sum(int(c.get(k, 0)) * v for k, v in table[role].items()) + max(0, int(c.get("combined_trumps", 0)) - 8)
        return _match(rule_id, points, evidence=("FACT-L1-SKILL-0110",))
    if rule_id == "RULE-L1-SCORING-UNDOUBLED-CONTRACT":
        level, strain = int(c.get("level", 0)), str(c.get("strain") or "").upper()
        if level < 1 or strain not in {"C", "D", "H", "S", "NT"}:
            return _no(rule_id, "level/strain required")
        score = 20 * level if strain in {"C", "D"} else 30 * level if strain in {"H", "S"} else 40 + 30 * (level - 1)
        return _match(rule_id, score, evidence=("FACT-L1-SKILL-0114",))
    if rule_id == "RULE-L1-SCORING-GAME-SLAM-CLASS":
        level, score = int(c.get("level", 0)), int(c.get("contract_trick_score", 0))
        return _match(rule_id, "GRAND_SLAM" if level == 7 else "SMALL_SLAM" if level == 6 else "GAME" if score >= 100 else "PARTSCORE", evidence=("FACT-L1-SKILL-0115",))
    if rule_id == "RULE-L1-SCORING-VUL-BONUS-UNDERTRICKS":
        vul = bool(c.get("vulnerable"))
        if "undertricks" in c:
            return _match(rule_id, int(c["undertricks"]) * (100 if vul else 50), evidence=("FACT-L1-SKILL-0116",))
        bonuses = {"PARTSCORE": (50, 50), "GAME": (300, 500), "SMALL_SLAM": (500, 750), "GRAND_SLAM": (1000, 1500)}
        cls = str(c.get("class") or "GAME").upper()
        return _match(rule_id, bonuses[cls][1 if vul else 0], evidence=("FACT-L1-SKILL-0116",)) if cls in bonuses else _no(rule_id, "unknown class")
    if rule_id == "RULE-L1-PLAY-NT-COUNT-TOP-TRICKS":
        return _match(rule_id, int(c.get("top_tricks", 0)), evidence=("FACT-L1-SKILL-0001",))
    if rule_id == "RULE-L1-PLAY-NT-FIND-EXTRA-TRICKS":
        return _match(rule_id, max(0, int(c.get("required_tricks", 9)) - int(c.get("top_tricks", 0))), evidence=("FACT-L1-SKILL-0002",))
    if rule_id == "RULE-L1-PLAY-TRUMP-COUNT-LOSERS":
        level = int(c.get("contract_level", 0))
        return _match(rule_id, 7 - level, evidence=("FACT-L1-SKILL-0008",)) if 1 <= level <= 7 else _no(rule_id, "level 1..7")
    if rule_id == "RULE-L1-PLAY-NT-PRESERVE-STOPPERS":
        return _match(rule_id, "PRESERVE_STOPPER_UNTIL_NEEDED", evidence=("FACT-L1-SKILL-0118",)) if c.get("defense_long_suit_threat") and c.get("stopper_identified") else _no(rule_id, "stopper/threat not established")
    if rule_id == "RULE-L1-MACHINE-COMPILER-GATE":
        if str(c.get("skill_status") or "") in {"PARTIAL_CANON_SCOPE", "BLOCKED_PENDING_TEACHER"}:
            return _block(rule_id, "AUTO_PROMOTION_FORBIDDEN", "auto-promotion forbidden")
        return _match(rule_id, "PROMOTION_MAY_BE_REVIEWED")
    if rule_id == "RULE-L1-DEFENSE-SIGNAL-GATE":
        return _block(rule_id, "BLOCKED_PENDING_TEACHER", "signaling code undefined") if c.get("derive_signals_from_vague_standard_phrase") else _no(rule_id, "no signal derivation")
    if rule_id == "RULE-L1-RUNTIME-CONFLICT-GATE":
        return _canon_conflict(rule_id, "unresolved same-rank conflict")
    return _block(rule_id, "REFERENCE_ONLY_OR_UNIMPLEMENTED", "not executable in L1 runtime v1")


def _actions_equal(left: Any, right: Any) -> bool:
    """Compare action meanings without coercing their concrete types.

    Dict insertion order is not semantic; sequence order and scalar/sequence
    types are. Unsupported objects and non-finite floats cannot prove agreement.
    """
    if type(left) is not type(right):
        return False
    if left is None:
        return True
    if type(left) in (str, bool, int):
        return left == right
    if type(left) is float:
        return isfinite(left) and isfinite(right) and left == right
    try:
        if type(left) in (list, tuple):
            return len(left) == len(right) and all(
                _actions_equal(a, b) for a, b in zip(left, right))
        if type(left) is dict:
            return len(left) == len(right) and all(
                any(_actions_equal(key, other_key) and _actions_equal(value, other_value)
                    for other_key, other_value in right.items())
                for key, value in left.items())
    except RecursionError:
        return False
    return False


def resolve(evaluations: Iterable[RuleEvaluation]) -> RuleEvaluation:
    items = list(evaluations)
    conflicts = [x for x in items if x.status == "CANON_CONFLICT"]
    if conflicts:
        return _canon_conflict(conflicts[0].rule_id, conflicts[0].reason or "canonical conflict")
    matched = [x for x in items if x.matched]
    nt_blocks = [x for x in items if x.status == "BLOCK"
                 and x.rule_id == "RULE-L1-NT-STOPPER-REQUIRED-COMPETITION"
                 and (x.action == "NT_FORBIDDEN_NO_STOPPER"
                      or (x.action is None and (x.reason or "").startswith("NT_CONSTRAINT_CONTEXT_UNKNOWN:")))]
    if nt_blocks:
        # Finite action classification: never use substring matching (COUNT,
        # CONTINUE and unrelated procedural actions are not NT bids).
        forbidden = {f"{level}NT" for level in range(1, 8)} | {
            "NT_NO_JUMP", "NT_JUMP", "NT_WITH_STOPPER", "PREFER_3NT_OVER_5MINOR",
            "PREFER_NT_OVER_MINOR", "INVITE_NT", "GAME_NT", "NT",
            "LESSON2_NT_BRANCH", "NT_CHOICE_BRANCH",
        }
        allowed = [x for x in matched if not (isinstance(x.action, str)
                   and any(part in forbidden for part in x.action.split(";")))]
        if matched and not allowed:
            return nt_blocks[0]
        matched = allowed
    if not matched:
        blocked = [x for x in items if x.status == "BLOCK"]
        return blocked[0] if blocked else _result("RULESET", "NO_MATCH", reason="no rule matched")
    key = max((x.specificity, x.scope_rank, x.priority) for x in matched)
    top = [x for x in matched if (x.specificity, x.scope_rank, x.priority) == key]
    if not all(_actions_equal(top[0].action, x.action) for x in top[1:]):
        return _canon_conflict("RULE-L1-RUNTIME-CONFLICT-GATE", "same-rank rules disagree")
    if len(top) == 1:
        return top[0]
    ev = tuple(dict.fromkeys(e for x in top for e in x.evidence))
    w = top[0]
    return RuleEvaluation("RULESET", "MATCH", w.action, w.priority, w.specificity, w.scope_rank, ev, "same-rank rules agree")


CONTEXT_CONTRACT_VERSION = "l1-explicit-observations-v1"


class _ContextRequired(ValueError):
    pass


class _ObservedContext(dict):
    """Missing is unknown. Defaults in legacy handlers are never observations.

    Validation occurs only when a field is actually read. A short-circuited
    branch does not require the facts used exclusively by another branch.
    No external authority, profile or approval is inferred from this class.
    """

    def __getitem__(self, key):
        if key not in self:
            raise _ContextRequired("missing observation: " + str(key))
        value = super().__getitem__(key)
        if key in _INTEGER_FIELDS:
            low, high = _INTEGER_RANGES.get(key, (0, 13))
            if type(value) is not int or not low <= value <= high:
                raise _ContextRequired("invalid integer observation: " + key)
        elif key in _BOOLEAN_FIELDS:
            if type(value) is not bool:
                raise _ContextRequired("invalid boolean observation: " + key)
        elif key in _STRING_FIELDS:
            if not isinstance(value, str) or not value:
                raise _ContextRequired("invalid text observation: " + key)
            allowed = _TEXT_ENUMS.get(key)
            if allowed is not None and value.upper() not in allowed:
                raise _ContextRequired("invalid denomination observation: " + key)
        elif key in {"cards", "calls", "winning_side"}:
            if not isinstance(value, (list, tuple)):
                raise _ContextRequired("invalid sequence observation: " + key)
        else:
            raise _ContextRequired("observation contract not defined: " + str(key))
        return value

    def get(self, key, default=None):
        return self[key]


def _evaluate_observed(rule_id, context, implementation, *, system_version):
    # Isolation precedes context conversion, numeric parsing and card parsing.
    if system_version != SYSTEM_VERSION:
        return _no(rule_id, f"system isolation: {system_version}")
    if not isinstance(context, dict):
        if rule_id == "RULE-L1-NT-STOPPER-REQUIRED-COMPETITION":
            return _result(rule_id, "BLOCK", reason="NT_CONSTRAINT_CONTEXT_UNKNOWN: invalid observation object",
                           specificity=0, scope_rank=0)
        return _no(rule_id, "context must be an observation object")
    try:
        return implementation(rule_id, _ObservedContext(context), system_version=system_version)
    except _ContextRequired as exc:
        if rule_id == "RULE-L1-NT-STOPPER-REQUIRED-COMPETITION":
            return _result(rule_id, "BLOCK", reason="NT_CONSTRAINT_CONTEXT_UNKNOWN: " + str(exc),
                           specificity=0, scope_rank=0)
        return _no(rule_id, str(exc))
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        if rule_id == "RULE-L1-NT-STOPPER-REQUIRED-COMPETITION":
            return _result(rule_id, "BLOCK", reason="NT_CONSTRAINT_CONTEXT_UNKNOWN: invalid observation structure",
                           specificity=0, scope_rank=0)
        return _no(rule_id, "invalid observation structure")

_INTEGER_FIELDS = frozenset(('A', 'C', 'D', 'H', 'HCP', 'J', 'K', 'Q', 'S', 'best_suit_length', 'called_suit_length', 'combined_HCP', 'combined_trumps', 'contract_level', 'contract_trick_score', 'declarer_tricks', 'distribution_points', 'doubleton', 'each_unbid_suit', 'legal_entry_level', 'lesson_stage', 'level', 'major_length', 'opening_suit_length', 'opponent_opening_level', 'own_suit_length', 'own_support_in_responder_suit', 'partner_major_length', 'passes_after_last_significant_call', 'required_tricks', 'responder_support', 'second_suit_length', 'singleton', 'suit_length', 'support', 'support_in_partner_minor', 'target_major_length', 'top_tricks', 'undertricks', 'void'))
_BOOLEAN_FIELDS = frozenset(('RHO_made_meaningful_bid', 'RHO_passed', 'artificial_call', 'auction_1NT_2C_STAYMAN', 'auction_2C_2D', 'balanced', 'called_suit_has_at_least_two_honors', 'considering_NT', 'considering_pass', 'contract_trump', 'current_L1_context', 'dealer_known', 'defender_trumps_remaining', 'defense_long_suit_threat', 'derive_signals_from_vague_standard_phrase', 'direct_double_of_opening', 'doubler_makes_contentful_rebid', 'entry_or_access_risk', 'expass_position_recognized', 'finesse_position_recognized', 'fit', 'fit_found', 'fit_not_found', 'fit_to_partner', 'game_balance_reached', 'game_force_active', 'has_4_card_major', 'has_5plus_major', 'initial_call_double', 'jump_new_suit', 'key_honor_with_relevant_defender', 'lesson_supported_exception', 'long_suit_can_produce_extra_tricks', 'loser_identified', 'minor_fit_context', 'minor_opening_path', 'minor_option', 'missing_honor_location_relevant', 'multiple_suitable_contracts', 'natural_call', 'new_opener_range_or_shape_info_available', 'no_4plus_major', 'no_5_card_major', 'no_fit', 'no_major_action', 'no_minor_fit', 'no_preferred_own_suit', 'no_preferred_own_suit_in_current_branch', 'no_second_4plus_suit', 'no_suitable_NT', 'no_suitable_suit_overcall', 'not_1NT_opening', 'not_2NT_opening', 'opener_rebid_received', 'opponent_opened', 'opponent_opened_in_responder_strong_suit', 'opponent_suit_known', 'ordinary_one_level_opening_model', 'own_5plus_suit', 'own_suit_requires_level2', 'partner_answered_cuebid', 'partner_doubled_opening', 'partner_invited', 'partner_just_doubled_opening', 'partner_made_preemptive_overcall', 'partner_natural_overcall_level_3', 'partner_opened_1M', 'partner_opened_2C', 'partner_overcalled_1NT', 'partner_overcalled_2NT', 'partner_rebid_seen', 'partner_responded', 'partner_response_known', 'partner_response_new_natural_suit', 'partner_response_new_suit', 'partner_response_range_known', 'partner_suit_fit', 'penalty_context', 'planned_target_hand_known', 'required_loss_elimination_first', 'ruff_gains_trick', 'short_trump_hand_has_available_trump', 'side_suit_winner_available_or_developable', 'stayman_response_received', 'stopper', 'stopper_identified', 'stopper_in_opponent_suit', 'strong2C_opener_rebid_suit', 'strong_suit_exception', 'sufficient_combined_balance', 'takeout_doubler', 'trump_fit', 'vulnerable', 'weak_hand_with_long_suit', 'weak_unsuitable_hand'))
_STRING_FIELDS = frozenset(('called_suit', 'class', 'doubler_range', 'final_denomination', 'hand_role', 'last_significant_call', 'lead', 'major', 'mode', 'opener_range', 'partner_major', 'partner_minor', 'partner_range', 'partner_response_type', 'shape', 'skill_status', 'strain', 'target_major', 'trump'))
_INTEGER_RANGES = {'HCP': (0, 37), 'combined_HCP': (0, 40), 'distribution_points': (0, 37), 'A': (0, 4), 'K': (0, 4), 'Q': (0, 4), 'J': (0, 4), 'void': (0, 4), 'singleton': (0, 4), 'doubleton': (0, 4), 'level': (1, 7), 'contract_level': (1, 7), 'legal_entry_level': (1, 7), 'opponent_opening_level': (1, 7), 'required_tricks': (7, 13), 'contract_trick_score': (0, 220), 'lesson_stage': (1, 100), 'passes_after_last_significant_call': (0, 3)}
_TEXT_ENUMS = {
    "called_suit": frozenset({"C", "D", "H", "S"}),
    "major": frozenset({"H", "S"}), "partner_major": frozenset({"H", "S"}),
    "partner_minor": frozenset({"C", "D"}), "target_major": frozenset({"H", "S"}),
    "lead": frozenset({"C", "D", "H", "S"}),
    "trump": frozenset({"C", "D", "H", "S", "NT"}),
    "strain": frozenset({"C", "D", "H", "S", "NT"}),
    "final_denomination": frozenset({"C", "D", "H", "S", "NT"}),
}


def evaluate(rule_id: str, context: dict[str, Any], *, system_version: str = SYSTEM_VERSION) -> RuleEvaluation:
    return _evaluate_observed(rule_id, context, _evaluate_unchecked, system_version=system_version)
