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
    assert first["stable_key"].endswith(first["payload_hash"])
    assert third["stable_key"].endswith(third["payload_hash"])
