from __future__ import annotations

from copy import deepcopy
import hashlib

import pytest

from bridge_contracts.video_canon_evidence import (
    VideoCanonEvidenceError,
    build_video_canon_candidate,
)
from tests.test_bridge_video_learning_candidate import _candidate


def _learning() -> dict:
    value = _candidate()
    statement = "После открытия один черва эта заявка форсирует."
    value["transcript_evidence"][0]["text_sha256"] = hashlib.sha256(
        statement.encode("utf-8")
    ).hexdigest()
    value["transcript_evidence"][0]["speaker_id"] = "teacher:diana"
    value["transcript_evidence"][0]["speaker_identity_status"] = "VERIFIED"
    return value


def _assertion() -> dict:
    statement = "После открытия один черва эта заявка форсирует."
    return {
        "assertion_id": "video-rule:diana:lesson-1:9-11",
        "statement": statement,
        "statement_sha256": hashlib.sha256(statement.encode("utf-8")).hexdigest(),
        "speaker_id": "teacher:diana",
        "transcript_locators": ["transcript.jsonl#segment=7"],
        "source_class": "SCHOOL_PRIMARY_EVIDENCE",
        "source_authorization": {
            "status": "APPROVED",
            "decision_ref": "director-decision:video-source:diana-v1",
            "policy_version": "school-video-auto-canon-v1",
            "authorized_source_sha256": "a" * 64,
            "authorized_video_file_id": "drive-file-canary",
            "authorized_teacher_ids": ["teacher:diana"],
            "approved_semantic_scopes": ["bidding/natural/v1/response-to-1h"],
            "authorization_evidence_sha256": "e" * 64,
        },
        "semantic_scope": "bidding/natural/v1/response-to-1h",
        "system_profile": "natural-v1",
        "learner_level": "beginner-1",
        "normalized_rule": {
            "rule_key": "video-rule:diana:lesson-1:9-11",
            "rule_kind": "bid",
            "auction_pattern": {"calls": ["1H", "PASS", "?"]},
            "hand_constraints": {},
            "public_context_constraints": {},
            "action": {"call": "2C"},
            "meaning": {"description": "Форсирующий ответ."},
            "public_inference": {},
            "alert_semantics": {"alert": False},
            "forcing_semantics": {"forcing": True},
            "priority": 100,
            "specificity": 10,
            "condition_schema_version": "bidding-condition-v0",
            "compiled_payload": {},
            "method_version": "video-canon-evidence-v2",
        },
        "semantic_confidence": 0.91,
        "ambiguities": [],
        "contradictions": [],
        "explanation": {
            "why_or_purpose": ["Сохраняет пространство для описания рук."],
            "consequences": ["Партнёр обязан продолжить торговлю."],
            "rejected_alternatives": [],
            "evidence_refs": ["transcript.jsonl#segment=7"],
        },
        "tests": {
            "positive": [{"auction": ["1H", "PASS"], "expect": "2C"}],
            "negative": [{"auction": ["1S", "PASS"], "expect": "NO_MATCH"}],
            "boundary": [{"auction": ["1H", "X"], "expect": "NO_MATCH"}],
            "interference": [{"auction": ["1H", "2D"], "expect": "NO_MATCH"}],
        },
    }


def test_builds_ai_verification_candidate_without_direct_canon_write_power():
    assertion = _assertion()
    assertion["semantic_confidence"] = 0.97
    result = build_video_canon_candidate(_learning(), assertion)

    assert result["quality_status"] == "AI_VERIFICATION_PENDING"
    assert result["promotion_status"] == "STAGING_ONLY"
    assert result["authoritative_tables_modified"] is False
    assert result["payload"]["activation"]["school_canon_write_allowed"] is False
    assert result["payload"]["activation"]["i2_review_required"] is True
    assert result["payload"]["activation"]["human_approval_required"] is False
    assert result["payload"]["activation"]["automatic_activation_after_all_gates"] is True
    assert len(result["payload_hash"]) == 64


def test_unapproved_teaching_video_is_evidence_only():
    assertion = _assertion()
    assertion["source_class"] = "TEACHING_CONTEXT"
    assertion["source_authorization"] = {
        "status": "NOT_APPROVED", "decision_ref": "", "policy_version": "",
        "authorized_source_sha256": "", "authorized_video_file_id": "",
        "authorized_teacher_ids": [], "approved_semantic_scopes": [],
        "authorization_evidence_sha256": "",
    }

    result = build_video_canon_candidate(_learning(), assertion)
    assert result["quality_status"] == "EVIDENCE_ONLY"


def test_world_video_can_never_be_review_eligible_for_school_canon():
    assertion = _assertion()
    assertion["source_class"] = "WORLD_EXTERNAL"
    result = build_video_canon_candidate(_learning(), assertion)
    assert result["quality_status"] == "EVIDENCE_ONLY"


@pytest.mark.parametrize("mutation, match", [
    (lambda a: a.update(speaker_id="teacher:other"), "speaker mismatch"),
    (lambda a: a["source_authorization"].update(approved_semantic_scopes=[]), "exact semantic scope"),
    (lambda a: a["source_authorization"].update(authorized_source_sha256="d" * 64), "source sha256 mismatch"),
    (lambda a: a.update(statement="Подменённое утверждение"), "does not match statement"),
    (lambda a: a["normalized_rule"].update(partner_hand="AKQ"), "hidden information"),
    (lambda a: a["normalized_rule"].update(partner_holding="AKQ.JT9.876.5432"), "hidden information"),
    (lambda a: a["normalized_rule"].update(**{"North-Cards": "AKQ.JT9.876.5432"}), "hidden information"),
    (lambda a: a["normalized_rule"].update(**{"partner's_hand": "AKQ.JT9.876.5432"}), "hidden information"),
    (lambda a: a["normalized_rule"].update(partners_hand="AKQ.JT9.876.5432"), "hidden information"),
    (lambda a: a["normalized_rule"].update(**{"opponents-cards": "AKQ.JT9.876.5432"}), "hidden information"),
    (lambda a: a["normalized_rule"].update(**{"partnerDeal": "AKQ"}), "hidden information"),
    (lambda a: a["normalized_rule"].update(**{"north/deal": "AKQ"}), "hidden information"),
    (lambda a: a["normalized_rule"].update(hidden_hand="AKQ.JT9.876.5432"), "hidden information"),
    (lambda a: a["normalized_rule"].update(**{"concealed-holdings": "AKQ.JT9.876.5432"}), "hidden information"),
    (lambda a: a["normalized_rule"].update(hidden_deal="AKQ.JT9.876.5432"), "hidden information"),
    (lambda a: a["normalized_rule"].update(**{"concealed-deals": "AKQ.JT9.876.5432"}), "hidden information"),
    (lambda a: a["tests"].update(boundary=[]), "four test classes"),
])
def test_fails_closed_on_unproven_or_unsafe_assertions(mutation, match):
    assertion = _assertion()
    mutation(assertion)
    with pytest.raises(VideoCanonEvidenceError, match=match):
        build_video_canon_candidate(_learning(), assertion)


def test_ambiguity_or_conflict_prevents_review_eligibility():
    for field in ("ambiguities", "contradictions"):
        assertion = _assertion()
        assertion[field] = ["needs bridge review"]
        result = build_video_canon_candidate(_learning(), assertion)
        assert result["quality_status"] == "EVIDENCE_ONLY"


def test_low_confidence_remains_evidence_only():
    result = build_video_canon_candidate(_learning(), _assertion())
    assert result["quality_status"] == "EVIDENCE_ONLY"


def test_oversized_semantic_confidence_fails_closed():
    assertion = _assertion()
    assertion["semantic_confidence"] = 10**1000
    with pytest.raises(VideoCanonEvidenceError, match="semantic confidence"):
        build_video_canon_candidate(_learning(), assertion)


def test_hidden_information_is_rejected_inside_json_serializable_tuple():
    assertion = _assertion()
    assertion["normalized_rule"]["compiled_payload"] = {
        "nested": ({"partner_hand": "AKQ"},)
    }
    with pytest.raises(VideoCanonEvidenceError, match="hidden information"):
        build_video_canon_candidate(_learning(), assertion)


@pytest.mark.parametrize("field,value", [
    ("notes", "N:AKQJ.T98.765.432 E:T987.654.32.AKQ S:... W:..."),
    ("comment", "partner_hand = AKQJ.T98.765.432"),
    ("comment", "карты партнера: AKQJ.T98.765.432"),
])
def test_hidden_deal_is_rejected_inside_innocuous_allowed_value(field, value):
    assertion = _assertion()
    assertion["normalized_rule"]["compiled_payload"] = {field: value}
    with pytest.raises(VideoCanonEvidenceError, match="hidden information"):
        build_video_canon_candidate(_learning(), assertion)


@pytest.mark.parametrize("statement", [
    "N:AKQJ.T98.765.432 E:T987.654.32.AKQ S:... W:...",
    "deal=N:AKQJ.T98.765.432,E:JT9.AKQ.JT9.876,S:876.765.AKQ.JT9,W:5432.J432.432.AKQ",
    "partner_hand = AKQJ.T98.765.432",
    "North's hand: AKQJ.T98.765.432, East’s hand: JT9.AKQ.JT9.876, "
    "South's hand: 876.765.AKQ.JT9, West's hand: 5432.J432.432.AKQ",
    "North's hand is AKQJ.T98.765.432; East's hand is JT9.AKQ.JT9.876; "
    "South's hand is 876.765.AKQ.JT9; West's hand is 5432.J432.432.AKQ",
    "North's hand was AKQJ.T98.765.432; East's hand was JT9.AKQ.JT9.876; "
    "South's hand was 876.765.AKQ.JT9; West's hand was 5432.J432.432.AKQ",
    "North's hand, as it appeared clearly in the recorded diagram, was "
    "AKQJ.T98.765.432; East's hand, as it appeared clearly in the recorded diagram, was "
    "JT9.AKQ.JT9.876; South's hand was 876.765.AKQ.JT9; "
    "West's hand was 5432.J432.432.AKQ",
    "North's hand, as it appeared\nclearly in the recorded diagram, was "
    "AKQJ.T98.765.432; East's hand was JT9.AKQ.JT9.876; "
    "South's hand was 876.765.AKQ.JT9; West's hand was 5432.J432.432.AKQ",
    "North's hand was S:AKQJ H:T98 D:765 C:432",
    "карты партнера: S:AKQJ H:T98 D:765 C:432",
    "N:AKQJ109.876.54.32",
    "North's hand was S:AKQJ109 H:876 D:54 C:32",
    "N: S:AKQJ109 H:876 D:54 C:32",
    "North's hand was AKQJ109 T98 7 432",
    "N: AKQJ109/T98/7/432",
    "карты партнера: AKQJ109, T98, 7, 432",
    "North's hand was ♠AKQJ ♥T98 ♦765 ♣432",
    "North's hand was ♠AKQJ ♥T98 ♦765 ♣43",
    "N: ♠AKQJ ♥T98 ♦765 ♣432",
    "N: ♣432 ♦765 ♥T98 ♠AKQJ",
    "N: ♣43 ♦765 ♥T98",
    "N: AKQJ.T98",
    "N: AKQJ",
    "partner hand: AKQJ",
    "partner hand: Q",
    "N: 10",
    "partner hand: q",
    "N: t",
    "N: 2",
    "partner hand: 2",
    "North held Q",
    "partner holds AKQJ",
    "N held 2",
    "North is holding Q",
    "North is currently holding Q",
    "North is currently holding: Q",
    "partner was holding AKQJ",
    "partner was apparently still holding AKQJ",
    "partner's still holding, AKQJ",
    "North's holding Q",
    "North's still holding Q",
    "partner’s holding AKQJ",
    "partner’s currently holding AKQJ",
    "N's holding Q",
    "N’s now holding Q",
    "North has the Q",
    "North holds the ace of spades",
    "North has ♠Q",
    "North has ♥Q",
    "North has ♦10",
    "partner holds ♣A",
    "North has the spade queen",
    "partner has heart ten",
    "North holds AS",
    "North has 10-12я",
    "North has 10+é",
    "North does not have the ace of spades",
    "North doesn't hold ♥Q",
    "North has no aces",
    "partner holds no ♣A",
    "North does not have any aces",
    "North has none of the aces",
    "North lacks the ace of spades",
    "North has no spades",
    "North does not have any spades",
    "North lacks spades",
    "North is void in spades",
    "partner is void in hearts",
    "North is void of spades",
    "North is void in ♠",
    "У партнёра туз пик",
    "У соперника есть король червей",
    "Партнер имеет пикового туза",
    "Оппонент держит даму треф",
    "Partner has AKQx.Txx.xxx.xxx",
    "У партнёра есть туз",
    "Партнёр держит туза",
    "У партнёра A♠",
    "Partner's ace of spades is an entry",
    "Partner owns the ace of spades",
    "North possesses ♠A",
    "Partner: ace of spades",
    "North: AK",
    "Партнёр: туз пик",
    "Соперник: Q",
])
def test_hidden_deal_is_rejected_in_source_bound_teacher_statement(statement):
    learning = _learning()
    assertion = _assertion()
    assertion["statement"] = statement
    assertion["statement_sha256"] = hashlib.sha256(statement.encode("utf-8")).hexdigest()
    learning["transcript_evidence"][0]["text_sha256"] = assertion["statement_sha256"]
    with pytest.raises(VideoCanonEvidenceError, match="candidate payload contains hidden information"):
        build_video_canon_candidate(learning, assertion)


def test_rejects_incomplete_or_duplicate_candidate_test_definitions():
    invalid_tests = [
        {"positive": ["not-an-object"]},
        {"positive": [{"auction": ["1H", "PASS"]}]},
        {"positive": [
            {"auction": ["1H", "PASS"], "expect": "2C"},
            {"auction": ["1H", "PASS"], "expect": "2C"},
        ]},
    ]
    for patch in invalid_tests:
        learning = _learning()
        assertion = _assertion()
        assertion["tests"].update(patch)
        with pytest.raises(VideoCanonEvidenceError):
            build_video_canon_candidate(learning, assertion)


def test_payload_hash_is_deterministic_and_source_bound():
    first = build_video_canon_candidate(_learning(), _assertion())
    second = build_video_canon_candidate(deepcopy(_learning()), deepcopy(_assertion()))
    assert first["payload_hash"] == second["payload_hash"]

    changed = _learning()
    changed["source"]["source_sha256"] = "d" * 64
    changed_assertion = _assertion()
    changed_assertion["source_authorization"]["authorized_source_sha256"] = "d" * 64
    third = build_video_canon_candidate(changed, changed_assertion)
    assert third["payload_hash"] != first["payload_hash"]
    assert third["stable_key"] != first["stable_key"]
    assert third["payload"]["semantic_identity_sha256"] == first["payload"][
        "semantic_identity_sha256"
    ]
    assert first["stable_key"].endswith(first["payload_hash"])
    assert third["stable_key"].endswith(third["payload_hash"])


def test_correction_changes_content_hash_but_preserves_semantic_identity():
    first_assertion = _assertion()
    first = build_video_canon_candidate(_learning(), first_assertion)
    corrected_assertion = deepcopy(first_assertion)
    corrected_statement = "Исправление: после одного черва заявка не форсирует."
    corrected_assertion["statement"] = corrected_statement
    corrected_assertion["statement_sha256"] = hashlib.sha256(
        corrected_statement.encode("utf-8")
    ).hexdigest()
    corrected_assertion["normalized_rule"]["forcing_semantics"] = {"forcing": False}
    corrected_learning = _learning()
    corrected_learning["transcript_evidence"][0]["text_sha256"] = corrected_assertion[
        "statement_sha256"
    ]
    corrected = build_video_canon_candidate(corrected_learning, corrected_assertion)

    assert corrected["payload_hash"] != first["payload_hash"]
    assert corrected["stable_key"] != first["stable_key"]
    assert corrected["payload"]["semantic_identity_sha256"] == first["payload"][
        "semantic_identity_sha256"
    ]


def test_multiple_teachers_share_only_an_exact_profile_scoped_semantic_identity():
    diana = build_video_canon_candidate(_learning(), _assertion())

    alex_learning = _learning()
    alex_learning["transcript_evidence"][0]["speaker_id"] = "teacher:alex"
    alex_assertion = _assertion()
    alex_assertion["assertion_id"] = "video-rule:alex:lesson-1:9-11"
    alex_assertion["speaker_id"] = "teacher:alex"
    alex_assertion["source_authorization"]["authorized_teacher_ids"] = [
        "teacher:diana",
        "teacher:alex",
    ]
    alex = build_video_canon_candidate(alex_learning, alex_assertion)

    assert alex["payload_hash"] != diana["payload_hash"]
    assert alex["payload"]["teacher_assertion"]["speaker_id"] == "teacher:alex"
    assert diana["payload"]["teacher_assertion"]["speaker_id"] == "teacher:diana"
    assert alex["payload"]["semantic_identity_sha256"] == diana["payload"][
        "semantic_identity_sha256"
    ]

    precision_assertion = deepcopy(alex_assertion)
    precision_assertion["semantic_scope"] = "bidding/precision/v1/response-to-1h"
    precision_assertion["system_profile"] = "precision-v1"
    precision_assertion["source_authorization"]["approved_semantic_scopes"].append(
        precision_assertion["semantic_scope"]
    )
    precision = build_video_canon_candidate(alex_learning, precision_assertion)

    assert precision["payload"]["semantic_identity_sha256"] != alex["payload"][
        "semantic_identity_sha256"
    ]


def test_labelled_hand_prose_without_four_suit_encoding_is_not_a_false_positive():
    learning = _learning()
    assertion = _assertion()
    statement = "North's hand was strong."
    assertion["statement"] = statement
    assertion["statement_sha256"] = hashlib.sha256(statement.encode("utf-8")).hexdigest()
    learning["transcript_evidence"][0]["text_sha256"] = assertion["statement_sha256"]
    result = build_video_canon_candidate(learning, assertion)
    assert result["quality_status"] == "EVIDENCE_ONLY"


@pytest.mark.parametrize("statement", [
    "North's hand was strong...",
    "North's hand was a weak holding",
    "Explanation: Q is an abbreviation",
    "northeast hand: Q is the diagram label",
    "Порука партнера: Q — подпись поручителя",
    "North's hand was 5 hearts",
    "N: 3 trumps",
    "South hand: 7 losers",
    "North held 5 hearts",
    "North held 10 points",
    "North has 10 hearts",
    "partner was holding 10 cards",
    "North has 10-12 points",
    "North has 10 to 12 points",
    "North has 10+ points",
    "North has 10-12",
    "North has 10 to 12",
    "North has 10+",
    "North's holding 10-12 points",
    "North's still holding 10-12",
    "North's hand was 10 points",
    "North's hand was 10-12",
    "(North's hand was 10-12)",
    "[North's hand is 10+]",
    "North's hand is 10+",
    "N: was 10 to 12",
    "North's hand was 10-12 points",
    "North held the view that Q is conventional",
    "Partner's agreement is forcing",
    "North owns the decision process",
    "North: 10 points",
    "Партнёр: правило форсирует",
    "рука партнера: 5 карт",
])
def test_labelled_prose_without_thirteen_card_hand_is_allowed(statement):
    learning = _learning()
    assertion = _assertion()
    assertion["statement"] = statement
    assertion["statement_sha256"] = hashlib.sha256(statement.encode("utf-8")).hexdigest()
    learning["transcript_evidence"][0]["text_sha256"] = assertion["statement_sha256"]
    result = build_video_canon_candidate(learning, assertion)
    assert result["quality_status"] == "EVIDENCE_ONLY"
