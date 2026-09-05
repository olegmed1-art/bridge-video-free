"""Convert source-bound teacher video into AI-verifiable Canon candidates.

The adapter does not itself write authoritative tables.  It seals the exact
source, transcript assertion, teaching logic and tests that a separate guarded
AI promotion gate must verify before automatic Canon activation.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
from typing import Any, Mapping

from bridge_contracts.video_learning_candidate import (
    canonical_sha256 as learning_candidate_sha256,
    validate_learning_candidate,
)


SCHEMA = "video-canon-evidence-v2"
AUTHORITY_CLASS = "TEACHER_VIDEO"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_CLASSES = {"SCHOOL_PRIMARY_EVIDENCE", "TEACHING_CONTEXT", "WORLD_EXTERNAL"}
_SUIT_SYMBOL_TRANSLATION = str.maketrans({
    "♠": "S:", "♥": "H:", "♦": "D:", "♣": "C:",
})
_FORBIDDEN_KEYS = {
    "partner_hand", "opponent_hand", "opponent_hands", "north_hand",
    "east_hand", "south_hand", "west_hand", "lho_hand", "rho_hand",
    "full_deal", "hidden_cards",
    "actual_partner_hand", "actual_opponent_hand", "actual_opponent_hands",
    "partner_cards", "opponent_cards", "all_hands", "hidden_hand",
    "hidden_hands", "hidden_holding", "hidden_holdings", "concealed_hand",
    "concealed_hands", "concealed_holding", "concealed_holdings",
    "concealed_card", "concealed_cards", "hidden_deal", "hidden_deals",
    "concealed_deal", "concealed_deals",
}
_SUIT_PATTERN = r"(?:-|(?:(?:10)|[AKQJT2-9X]){0,13})"
_NONEMPTY_SUIT_PATTERN = r"(?:-|(?:(?:10)|[AKQJT2-9X]){1,13})"
_HAND_PATTERN = (
    _SUIT_PATTERN + r"\." + _SUIT_PATTERN + r"\."
    + _SUIT_PATTERN + r"\." + _SUIT_PATTERN
)
_PBN_DEAL = re.compile(
    r"(?:^|[^A-Za-z0-9])[NESW]\s*:\s*(?P<hand>" + _HAND_PATTERN + r")",
    re.IGNORECASE,
)
_LABELLED_HIDDEN_CARDS = re.compile(
    r"(?:(?<!\w)(?:partner|opponent|north|east|south|west|[lr](?:\s*\.\s*)?h(?:\s*\.\s*)?o\.?))\s*(?:['’]s)?[ _-]*(?:hand|cards)"
    r"\b[^;]*?(?P<hand>" + _HAND_PATTERN + r")"
    r"|(?:(?<!\w)(?:рука|карты))\s+(?:партн[её]ра|соперника)\b[^;]*?"
    r"(?P<ru_hand>" + _HAND_PATTERN + r")",
    re.IGNORECASE,
)
_SUIT_LABELLED_HIDDEN_CARDS = re.compile(
    r"(?:(?:(?<!\w)(?:partner|opponent|north|east|south|west|[lr](?:\s*\.\s*)?h(?:\s*\.\s*)?o\.?))\s*(?:['’]s)?[ _-]*"
    r"(?:hand|cards)\b|(?:(?<!\w)(?:рука|карты))\s+(?:партн[её]ра|соперника)\b"
    r"|(?:^|[^A-Za-z0-9])[NESW]\s*:)[^;]*?"
    r"S\s*:\s*(?P<spades>" + _SUIT_PATTERN + r")[\s,/]*"
    r"H\s*:\s*(?P<hearts>" + _SUIT_PATTERN + r")[\s,/]*"
    r"D\s*:\s*(?P<diamonds>" + _SUIT_PATTERN + r")[\s,/]*"
    r"C\s*:\s*(?P<clubs>" + _SUIT_PATTERN + r")",
    re.IGNORECASE,
)
_LABELLED_HAND_TAIL = re.compile(
    r"(?:(?:(?<!\w)(?:partner|opponent|north|east|south|west|[lr](?:\s*\.\s*)?h(?:\s*\.\s*)?o\.?))\s*(?:['’]s)?[ _-]*"
    r"(?:hand|cards)\b|(?:(?<!\w)(?:рука|карты))\s+(?:партн[её]ра|соперника)\b"
    r"|(?:(?<!\w)(?:partner|opponent|north|east|south|west|[lr](?:\s*\.\s*)?h(?:\s*\.\s*)?o\.?))\s*[:,;=\-]\s*"
    r"|(?:(?<!\w)(?:партн[её]р|соперник|оппонент))\s*[:,;=\-]\s*"
    r"|(?:^|[^A-Za-z0-9])[NESW]\s*:)(?P<tail>[^;]{0,512})",
    re.IGNORECASE,
)
_HOLDING_MODIFIER = (
    r"(?:(?:currently|still|now|already|actually|also|presently|temporarily|"
    r"usually|often|apparently|probably|clearly|just|not)\s+){0,2}"
)
_VERBAL_HIDDEN_TAIL = re.compile(
    r"(?:(?<!\w)(?:partner|opponent|north|east|south|west|[lr](?:\s*\.\s*)?h(?:\s*\.\s*)?o\.?)"
    r"|(?<![A-Za-z0-9])[NESW])(?:(?:\s+(?:(?:held|holds?|has|had|owns?|possesses?|retains?|carries?)|"
    r"(?:is|was)\s+" + _HOLDING_MODIFIER + r"holding))|"
    r"(?:['’]s(?:\s+" + _HOLDING_MODIFIER + r"holding)?))\b"
    r"(?P<tail>[^;]{0,512})",
    re.IGNORECASE,
)
_NEGATED_HIDDEN_HOLDING = re.compile(
    r"(?:(?<!\w)(?:partner|opponent|north|east|south|west|[lr](?:\s*\.\s*)?h(?:\s*\.\s*)?o\.?)"
    r"|(?<![A-Za-z0-9])[NESW])\s+"
    r"(?:(?:does\s+not|doesn['’]t)\s+(?:have|hold)(?:\s+any)?|"
    r"(?:has|holds?|had)\s+(?:(?:no|neither)|none\s+of)|"
    r"lacks?)\s+(?:any\s+)?"
    r"(?:the\s+)?(?:aces?|kings?|queens?|jacks?|tens?|twos?|threes?|fours?|fives?|sixes?|sevens?|eights?|nines?|spades?|hearts?|diamonds?|clubs?|"
    r"(?:ace|king|queen|jack|ten|two|three|four|five|six|seven|eight|nine)\s+of\s+(?:spades?|hearts?|diamonds?|clubs?)|"
    r"(?:spades?|hearts?|diamonds?|clubs?)\s+(?:ace|king|queen|jack|ten|two|three|four|five|six|seven|eight|nine|10|[AKQJT2-9])|"
    r"[SHDC]\s*:?\s*(?:10|[AKQJT2-9])|(?:10|[AKQJT2-9])\s*[SHDC])\b",
    re.IGNORECASE,
)
_VOID_HIDDEN_HOLDING = re.compile(
    r"(?:(?<!\w)(?:partner|opponent|north|east|south|west|[lr](?:\s*\.\s*)?h(?:\s*\.\s*)?o\.?)"
    r"|(?<![A-Za-z0-9])[NESW])\s+(?:is|was|remains?)\s+"
    r"void\s+(?:(?:in|of)\s+)?"
    r"(?:spades?|hearts?|diamonds?|clubs?|[SHDC]\s*:)(?:\b|(?=\s|$))",
    re.IGNORECASE,
)
_RUSSIAN_HIDDEN_POSSESSION = re.compile(
    r"(?:(?<!\w)у\s+(?:партн[её]ра|соперника|оппонента|противника)\s+|"
    r"(?<!\w)(?:партн[её]р|соперник|оппонент|противник)"
    r"(?:\s+(?:имеет|держит)\s+|\s*[:,;=\-]\s*))"
    r"(?:(?:есть|был[аио]?|имел[аио]?)\s+)?(?:"
    r"(?:туз\w*|корол\w*|дам\w*|валет\w*|десятк\w*|10|[AKQJT2-9X])"
    r"(?:\s+(?:пик|черв(?:ей|и|а)?|буб(?:ен|ны|на)?|треф(?:ы|а)?))?|"
    r"(?:пиков\w*|червов\w*|бубнов\w*|трефов\w*)\s+"
    r"(?:туз\w*|корол\w*|дам\w*|валет\w*|десятк\w*)|"
    r"(?:10|[AKQJT2-9X])\s*[SHDC]\s*:?|"
    r"[SHDC]\s*:?\s*(?:10|[AKQJT2-9X]))(?=$|[^\w])",
    re.IGNORECASE,
)
_LEADING_HOLDING_CARD_GROUP = re.compile(
    r"^\s*(?:[:,;=\-]\s*)?(?:(?:the|a|an)\s+)?(?:"
    r"(?:ace|king|queen|jack|ten)(?:\s+of\s+(?:spades?|hearts?|diamonds?|clubs?))?"
    r"|(?:two|three|four|five|six|seven|eight|nine)\s+of\s+(?:spades?|hearts?|diamonds?|clubs?)"
    r"|(?:spades?|hearts?|diamonds?|clubs?)\s+(?:ace|king|queen|jack|ten|two|three|four|five|six|seven|eight|nine|10|[AKQJT2-9X])"
    r"|[SHDC]\s*:?\s*(?:10|[AKQJT2-9X])|(?:10|[AKQJT2-9X])\s*[SHDC]|10|[AKQJT2-9X]|[kqjtx]|"
    r"(?:(?:10)|[AKQJT2-9Xakqjtx]){2,13})(?:$|[^A-Za-z0-9])",
    re.IGNORECASE,
)
_EXPLICIT_SUIT_LABEL = re.compile(
    r"(?<![A-Za-z0-9_])(?P<suit>[SHDC])\s*:", re.IGNORECASE
)
_SINGLE_SUIT_CARD_GROUP = re.compile(
    r"(?<![A-Za-z0-9])(?P<cards>10|[AKQJTX]|[kqjtx]|"
    r"(?:(?:10)|[AKQJT2-9Xakqjtx]){2,13})(?![A-Za-z0-9])"
)
_LEADING_SINGLE_DIGIT_CARD = re.compile(
    r"^\s*(?:(?:was|is)\s+|[:,;=\-]\s*)?[2-9](?:$|[\s,./;])",
    re.IGNORECASE,
)
_LEADING_LENGTH_DESCRIPTION = re.compile(
    r"^\s*(?:(?:was|is)\s+|[:,;=\-]\s*)?(?:10|[2-9])\s*(?:(?:(?:[-–—]|to)\s*\d{1,2}|\+)\s*)?(?:"
    r"cards?|hearts?|spades?|diamonds?|clubs?|trumps?|losers?|points?|hcp|"
    r"controls?|winners?|stoppers?|suits?|"
    r"карт\w*|черв\w*|пик\w*|буб\w*|треф\w*|козыр\w*|взят\w*|"
    r"очк\w*|пункт\w*|контрол\w*)\b",
    re.IGNORECASE,
)
_LEADING_NUMERIC_RANGE = re.compile(
    r"^\s*(?:(?:was|is)\s+|[:,;=\-]\s*)?(?:10|[2-9])\s*"
    r"(?:(?:[-–—]|to)\s*\d{1,2}|\+)(?:$|[^\w])",
    re.IGNORECASE,
)
_PARTIAL_SEPARATED_HAND = re.compile(
    r"(?<![A-Za-z0-9])(?:-|(?:(?:10)|[AKQJT2-9X]){1,13})"
    r"(?:[\s,/.]+(?:-|(?:(?:10)|[AKQJT2-9X]){1,13})){1,3}"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_SEPARATED_LABELLED_HIDDEN_CARDS = re.compile(
    r"(?:(?:(?<!\w)(?:partner|opponent|north|east|south|west|[lr](?:\s*\.\s*)?h(?:\s*\.\s*)?o\.?))\s*(?:['’]s)?[ _-]*"
    r"(?:hand|cards)\b|(?:(?<!\w)(?:рука|карты))\s+(?:партн[её]ра|соперника)\b"
    r"|(?:^|[^A-Za-z0-9])[NESW]\s*:)[^;]*?"
    r"(?P<spades>" + _NONEMPTY_SUIT_PATTERN + r")[\s,/]+"
    r"(?P<hearts>" + _NONEMPTY_SUIT_PATTERN + r")[\s,/]+"
    r"(?P<diamonds>" + _NONEMPTY_SUIT_PATTERN + r")[\s,/]+"
    r"(?P<clubs>" + _NONEMPTY_SUIT_PATTERN + r")",
    re.IGNORECASE,
)

_NORMALIZED_RULE_FIELDS = {
    "rule_key", "rule_kind", "auction_pattern", "hand_constraints",
    "public_context_constraints", "action", "meaning", "public_inference",
    "alert_semantics", "forcing_semantics", "priority", "specificity",
    "condition_schema_version", "compiled_payload", "method_version",
}
_RULE_JSON_FIELDS = {
    "auction_pattern", "hand_constraints", "public_context_constraints", "action",
    "meaning", "public_inference", "alert_semantics", "forcing_semantics",
    "compiled_payload",
}


class VideoCanonEvidenceError(ValueError):
    """The video evidence is unsafe, incomplete or authority-escalating."""


def _fail(message: str) -> None:
    raise VideoCanonEvidenceError(message)


def _text(value: Any, label: str) -> str:
    result = str(value or "").strip()
    if not result:
        _fail(f"{label} required")
    return result


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("invalid semantic confidence")
    try:
        result = float(value)
    except OverflowError:
        _fail("invalid semantic confidence")
    if not math.isfinite(result):
        _fail("invalid semantic confidence")
    if not 0 <= result <= 1:
        _fail("invalid semantic confidence")
    return result


def _has_forbidden_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).casefold() in _FORBIDDEN_KEYS
            or re.fullmatch(
                r"(?:actual)?(?:partner|opponent|north|east|south|west|leftopponent|rightopponent|lefthandopponent|righthandopponent|[lr](?:\s*\.\s*)?h(?:\s*\.\s*)?o\.?)s?"
                r"(?:hand|holding|cards?|deals?)+s?",
                re.sub(r"[^a-z0-9]", "", str(key).casefold()),
            ) is not None
            or re.fullmatch(
                r"(?:hidden|concealed)(?:hand|holding|cards?|deals?)+s?",
                re.sub(r"[^a-z0-9]", "", str(key).casefold()),
            ) is not None
            or re.fullmatch(
                r"(?:(?:рук[аи]|карт(?:а|ы|очки?)|расклад)"
                r"(?:партн[её]ра|соперника|оппонента|противника)"
                r"|(?:партн[её]ра|соперника|оппонента|противника)"
                r"(?:рук[аи]|карт(?:а|ы|очки?)|расклад))",
                re.sub(r"[\W_]", "", str(key).casefold()),
            ) is not None
            or _has_forbidden_key(child)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_has_forbidden_key(child) for child in value)
    return False


def _is_complete_hand_shape(hand: str) -> bool:
    suits = hand.upper().replace("10", "T").split(".")
    if len(suits) != 4:
        return False
    cards = 0
    for suit in suits:
        if suit == "-":
            suit = ""
        elif "-" in suit or len(set(suit.replace("X", ""))) != len(suit.replace("X", "")):
            return False
        cards += len(suit)
    return cards == 13


_ACTOR_CONTEXT_KEY = re.compile(
    r"(?:(?:actual)?(?:partner|opponent|north|east|south|west|leftopponent|rightopponent|lefthandopponent|righthandopponent|[lr](?:\s*\.\s*)?h(?:\s*\.\s*)?o\.?|n|e|s|w)s?"
    r"|партн[её]р|соперник|оппонент|противник)"
)


def _has_forbidden_value(value: Any, *, actor_context: bool = False) -> bool:
    if isinstance(value, Mapping):
        return any(
            _has_forbidden_value(
                child,
                actor_context=actor_context or _ACTOR_CONTEXT_KEY.fullmatch(
                    re.sub(r"[\W_]", "", str(key).casefold())
                ) is not None,
            )
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_has_forbidden_value(child, actor_context=actor_context) for child in value)
    if isinstance(value, str):
        normalized_value = value.translate(_SUIT_SYMBOL_TRANSLATION)
        if actor_context:
            normalized_value = f"Partner:{normalized_value}"
        if (
            _NEGATED_HIDDEN_HOLDING.search(normalized_value)
            or _VOID_HIDDEN_HOLDING.search(normalized_value)
            or _RUSSIAN_HIDDEN_POSSESSION.search(normalized_value)
        ):
            return True
        if any(_is_complete_hand_shape(match.group("hand")) for match in _PBN_DEAL.finditer(normalized_value)):
            return True
        # Any structurally explicit surface after a hidden actor/seat label is
        # forbidden. A disclosure remains hidden information when suits/cards
        # are omitted, unknown, partial, or written in another order.
        if any(
            _EXPLICIT_SUIT_LABEL.search(match.group("tail"))
            or (
                _LEADING_HOLDING_CARD_GROUP.search(match.group("tail"))
                and not (
                    _LEADING_LENGTH_DESCRIPTION.search(match.group("tail"))
                    or _LEADING_NUMERIC_RANGE.search(match.group("tail"))
                )
            )
            or (
                _SINGLE_SUIT_CARD_GROUP.search(match.group("tail"))
                and not (
                    _LEADING_LENGTH_DESCRIPTION.search(match.group("tail"))
                    or _LEADING_NUMERIC_RANGE.search(match.group("tail"))
                )
            )
            or (
                _LEADING_SINGLE_DIGIT_CARD.search(match.group("tail"))
                and not (
                    _LEADING_LENGTH_DESCRIPTION.search(match.group("tail"))
                    or _LEADING_NUMERIC_RANGE.search(match.group("tail"))
                )
            )
            or _PARTIAL_SEPARATED_HAND.search(match.group("tail"))
            for match in _LABELLED_HAND_TAIL.finditer(normalized_value)
        ):
            return True
        if any(
            _LEADING_HOLDING_CARD_GROUP.search(match.group("tail"))
            and not (
                    _LEADING_LENGTH_DESCRIPTION.search(match.group("tail"))
                    or _LEADING_NUMERIC_RANGE.search(match.group("tail"))
                )
            for match in _VERBAL_HIDDEN_TAIL.finditer(normalized_value)
        ):
            return True
        if any(
            _is_complete_hand_shape(".".join(match.group(
                "spades", "hearts", "diamonds", "clubs"
            )))
            for match in _SUIT_LABELLED_HIDDEN_CARDS.finditer(normalized_value)
        ):
            return True
        if any(
            _is_complete_hand_shape(".".join(match.group(
                "spades", "hearts", "diamonds", "clubs"
            )))
            for match in _SEPARATED_LABELLED_HIDDEN_CARDS.finditer(normalized_value)
        ):
            return True
        return any(
            _is_complete_hand_shape(match.group("hand") or match.group("ru_hand"))
            for match in _LABELLED_HIDDEN_CARDS.finditer(value)
        )
    return False


def contains_forbidden_hidden_information(value: Any) -> bool:
    """Return whether arbitrary candidate data carries hidden bridge holdings."""
    return _has_forbidden_key(value) or _has_forbidden_value(value)


def _digest(value: Any) -> str:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise VideoCanonEvidenceError("candidate value must be strict JSON") from exc
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _semantic_identity_sha256(*parts: str) -> str:
    component_hashes = "".join(
        hashlib.sha256(part.encode("utf-8")).hexdigest() for part in parts
    )
    return hashlib.sha256(component_hashes.encode("ascii")).hexdigest()


def _sha(value: Any, label: str) -> str:
    result = str(value or "").strip().lower()
    if not _SHA256.fullmatch(result):
        _fail(f"invalid {label}")
    return result


def _texts(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        _fail(f"{label} must be a list")
    result = [_text(item, label) for item in value]
    if len(result) != len(set(result)):
        _fail(f"duplicate {label}")
    return result


def build_video_canon_candidate(
    learning_candidate: Mapping[str, Any],
    assertion: Mapping[str, Any],
) -> dict[str, Any]:
    """Build an immutable staging record from a validated video observation.

    ``assertion`` must be an explicit, locally evidenced teacher statement.  A
    teaching example or model inference cannot be relabelled as a statement.
    Source authorization is source-level, not per-rule human approval.  A
    complete candidate remains non-authoritative until the separate AI gate
    proves every required check and atomically activates the sealed payload.
    """
    learning = validate_learning_candidate(learning_candidate)
    expected = {
        "assertion_id", "statement", "statement_sha256", "speaker_id", "transcript_locators",
        "source_class", "source_authorization", "semantic_scope",
        "system_profile", "learner_level",
        "normalized_rule", "semantic_confidence", "ambiguities",
        "contradictions", "explanation", "tests",
    }
    if not isinstance(assertion, Mapping) or set(assertion) != expected:
        _fail("assertion fields mismatch")

    assertion_id = _text(assertion.get("assertion_id"), "assertion_id")
    statement = _text(assertion.get("statement"), "statement")
    statement_sha = _sha(assertion.get("statement_sha256"), "statement_sha256")
    if hashlib.sha256(statement.encode("utf-8")).hexdigest() != statement_sha:
        _fail("statement_sha256 does not match statement")
    speaker_id = _text(assertion.get("speaker_id"), "speaker_id")
    source_class = assertion.get("source_class")
    if source_class not in _SOURCE_CLASSES:
        _fail("invalid source class")

    locators = assertion.get("transcript_locators")
    if not isinstance(locators, list) or len(locators) != 1:
        _fail("one exact transcript locator required per assertion")
    locators = [_text(value, "transcript locator") for value in locators]
    if len(locators) != len(set(locators)):
        _fail("duplicate transcript locator")
    transcript_by_locator = {
        row["locator"]: row for row in learning["transcript_evidence"]
    }
    if not set(locators) <= set(transcript_by_locator):
        _fail("assertion references evidence outside learning candidate")
    for locator in locators:
        transcript = transcript_by_locator[locator]
        if transcript["speaker_identity_status"] != "VERIFIED":
            _fail("teacher assertion requires verified speaker identity")
        if transcript["speaker_id"] != speaker_id:
            _fail("teacher assertion speaker mismatch")
        if transcript["text_sha256"] != statement_sha:
            _fail("teacher statement is not bound to transcript digest")

    authorization = assertion.get("source_authorization")
    authorization_fields = {
        "status", "decision_ref", "policy_version", "authorized_source_sha256",
        "authorized_video_file_id", "authorized_teacher_ids",
        "approved_semantic_scopes", "authorization_evidence_sha256",
    }
    if not isinstance(authorization, Mapping) or set(authorization) != authorization_fields:
        _fail("source authorization fields mismatch")
    status = authorization.get("status")
    if status not in {"APPROVED", "NOT_APPROVED"}:
        _fail("invalid source authorization status")
    decision_ref = str(authorization.get("decision_ref") or "").strip()
    policy_version = str(authorization.get("policy_version") or "").strip()
    authorized_source_sha = str(authorization.get("authorized_source_sha256") or "").strip().lower()
    authorized_video_file_id = str(authorization.get("authorized_video_file_id") or "").strip()
    authorization_evidence_sha = str(authorization.get("authorization_evidence_sha256") or "").strip().lower()
    teacher_ids = authorization.get("authorized_teacher_ids")
    scopes = authorization.get("approved_semantic_scopes")
    if not isinstance(scopes, list):
        _fail("approved semantic scopes must be a list")
    scopes = [_text(value, "approved semantic scope") for value in scopes]
    if len(scopes) != len(set(scopes)):
        _fail("duplicate approved semantic scope")
    semantic_scope = _text(assertion.get("semantic_scope"), "semantic_scope")
    if status == "APPROVED":
        if not decision_ref or not policy_version or semantic_scope not in scopes:
            _fail("approved source lacks exact semantic scope authorization")
        if not _SHA256.fullmatch(authorized_source_sha) or not _SHA256.fullmatch(authorization_evidence_sha):
            _fail("approved source lacks immutable authorization evidence")
        if authorized_source_sha != learning["source"]["source_sha256"]:
            _fail("authorization source sha256 mismatch")
        if authorized_video_file_id != learning["source"]["video_file_id"]:
            _fail("authorization video file mismatch")
        teacher_ids = _texts(teacher_ids, "authorized teacher id")
        if speaker_id not in teacher_ids:
            _fail("teacher is outside source authorization")
    if status == "NOT_APPROVED" and any((decision_ref, policy_version, authorized_source_sha,
                                           authorized_video_file_id, authorization_evidence_sha,
                                           scopes, teacher_ids)):
        _fail("unapproved source must not carry approval evidence")

    normalized_rule = assertion.get("normalized_rule")
    if not isinstance(normalized_rule, Mapping):
        _fail("normalized rule fields mismatch")
    if _has_forbidden_key(normalized_rule) or _has_forbidden_value(normalized_rule):
        _fail("normalized rule contains hidden information")
    if set(normalized_rule) != _NORMALIZED_RULE_FIELDS:
        _fail("normalized rule fields mismatch")
    rule_key = _text(normalized_rule.get("rule_key"), "normalized rule_key")
    if normalized_rule.get("rule_kind") not in {"bid", "inference", "priority", "exception", "fallback"}:
        _fail("invalid normalized rule_kind")
    for field in _RULE_JSON_FIELDS:
        if not isinstance(normalized_rule.get(field), Mapping):
            _fail(f"normalized {field} must be an object")
    for field in ("priority", "specificity"):
        value = normalized_rule.get(field)
        if isinstance(value, bool) or not isinstance(value, int):
            _fail(f"normalized {field} must be an integer")
    if normalized_rule["specificity"] < 0:
        _fail("normalized specificity must be non-negative")
    _text(normalized_rule.get("condition_schema_version"), "normalized condition_schema_version")
    _text(normalized_rule.get("method_version"), "normalized method_version")
    explanation = assertion.get("explanation")
    if not isinstance(explanation, Mapping) or set(explanation) != {
        "why_or_purpose", "consequences", "rejected_alternatives", "evidence_refs"
    }:
        _fail("explanation fields mismatch")
    why_or_purpose = _texts(explanation.get("why_or_purpose"), "why or purpose")
    consequences = _texts(explanation.get("consequences"), "consequence")
    rejected_alternatives = _texts(
        explanation.get("rejected_alternatives"), "rejected alternative", allow_empty=True
    )
    explanation_refs = _texts(explanation.get("evidence_refs"), "explanation evidence ref")
    if not set(explanation_refs) <= set(locators):
        _fail("explanation references evidence outside assertion")
    if _has_forbidden_key(explanation) or _has_forbidden_value(explanation):
        _fail("explanation contains hidden information")

    tests = assertion.get("tests")
    if not isinstance(tests, Mapping) or set(tests) != {
        "positive", "negative", "boundary", "interference"
    }:
        _fail("tests fields mismatch")
    if any(not isinstance(tests[kind], list) or not tests[kind] for kind in tests):
        _fail("all four test classes are required")
    if _has_forbidden_key(tests) or _has_forbidden_value(tests):
        _fail("tests contain hidden information")
    normalized_tests: dict[str, list[dict[str, Any]]] = {}
    for test_type, cases in tests.items():
        normalized_cases: list[dict[str, Any]] = []
        seen_cases: set[str] = set()
        for case in cases:
            if not isinstance(case, Mapping) or "expect" not in case or len(case) < 2:
                _fail("each test case requires fixture fields and expect")
            normalized_case = deepcopy(dict(case))
            case_digest = _digest(normalized_case)
            if case_digest in seen_cases:
                _fail("duplicate test case")
            seen_cases.add(case_digest)
            normalized_cases.append(normalized_case)
        normalized_tests[test_type] = normalized_cases

    ambiguities = assertion.get("ambiguities")
    contradictions = assertion.get("contradictions")
    if not isinstance(ambiguities, list) or not isinstance(contradictions, list):
        _fail("ambiguities and contradictions must be lists")
    ambiguities = [_text(value, "ambiguity") for value in ambiguities]
    contradictions = [_text(value, "contradiction") for value in contradictions]
    confidence = _confidence(assertion.get("semantic_confidence"))

    system_profile = _text(assertion.get("system_profile"), "system_profile")
    learner_level = _text(assertion.get("learner_level"), "learner_level")
    semantic_identity_sha256 = _semantic_identity_sha256(
        "video-canon-semantic-identity-v1",
        semantic_scope,
        system_profile,
        learner_level,
        rule_key,
    )
    ai_verification_eligible = (
        source_class == "SCHOOL_PRIMARY_EVIDENCE"
        and status == "APPROVED"
        and not ambiguities
        and not contradictions
        and confidence >= 0.95
    )
    payload = {
        "schema": SCHEMA,
        "authority_class": AUTHORITY_CLASS,
        "candidate_id": assertion_id,
        "semantic_identity_sha256": semantic_identity_sha256,
        "source": deepcopy(learning["source"]),
        "observed_episode": deepcopy(learning["observed_episode"]),
        "learning_candidate_id": learning["candidate_id"],
        "learning_candidate_sha256": learning_candidate_sha256(learning),
        "teacher_assertion": {
            "statement": statement,
            "statement_sha256": statement_sha,
            "speaker_id": speaker_id,
            "transcript_locators": locators,
        },
        "source_class": source_class,
        "source_authorization": {
            "status": status,
            "decision_ref": decision_ref or None,
            "policy_version": policy_version or None,
            "authorized_source_sha256": authorized_source_sha or None,
            "authorized_video_file_id": authorized_video_file_id or None,
            "authorized_teacher_ids": teacher_ids or [],
            "approved_semantic_scopes": scopes,
            "authorization_evidence_sha256": authorization_evidence_sha or None,
        },
        "semantic_scope": semantic_scope,
        "system_profile": system_profile,
        "learner_level": learner_level,
        "normalized_rule": deepcopy(dict(normalized_rule)),
        "semantic_confidence": confidence,
        "ambiguities": ambiguities,
        "contradictions": contradictions,
        "explanation": {
            "why_or_purpose": why_or_purpose,
            "consequences": consequences,
            "rejected_alternatives": rejected_alternatives,
            "evidence_refs": explanation_refs,
        },
        "tests": normalized_tests,
        "review_eligibility": (
            "AI_VERIFICATION_PENDING" if ai_verification_eligible else "EVIDENCE_ONLY"
        ),
        "activation": {
            "school_canon_write_allowed": False,
            "human_approval_required": False,
            "ai_verification_required": True,
            "regression_required": True,
            "integrity_required": True,
            "rollback_proof_required": True,
            "i2_review_required": True,
            "automatic_activation_after_all_gates": True,
        },
    }
    if _has_forbidden_key(payload) or _has_forbidden_value(payload):
        _fail("candidate payload contains hidden information")
    payload_hash = _digest(payload)
    return {
        "candidate_type": "video_school_canon_candidate",
        # One teacher assertion may be corrected over time. Content-address the
        # staging identity so every revision is preserved instead of colliding
        # with an older row that has the same logical assertion_id.
        "stable_key": f"{assertion_id}:sha256:{payload_hash}",
        "quality_status": payload["review_eligibility"],
        "promotion_status": "STAGING_ONLY",
        "payload": payload,
        "payload_hash": payload_hash,
        "evidence_refs": locators,
        "method_version": SCHEMA,
        "authoritative_tables_modified": False,
    }


__all__ = [
    "AUTHORITY_CLASS", "SCHEMA", "VideoCanonEvidenceError",
    "build_video_canon_candidate",
]
