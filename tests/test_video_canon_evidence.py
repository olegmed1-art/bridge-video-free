from __future__ import annotations

from copy import deepcopy

import pytest

from bridge_contracts.video_canon_evidence import (
    VideoCanonEvidenceError,
    build_video_canon_candidate,
)
from tests.test_bridge_video_learning_candidate import _candidate


def _learning() -> dict:
    value = _candidate()
    value["transcript_evidence"][0]["speaker_id"] = "teacher:diana"
    value["transcript_evidence"][0]["speaker_identity_status"] = "VERIFIED"
    return value


def _assertion() -> dict:
    return {
        "assertion_id": "video-rule:diana:lesson-1:9-11",
        "statement": "После открытия один черва эта заявка форсирует.",
        "speaker_id": "teacher:diana",
        "transcript_locators": ["transcript.jsonl#segment=7"],
        "source_class": "SCHOOL_PRIMARY_EVIDENCE",
        "source_authorization": {
            "status": "APPROVED",
            "decision_ref": "director-decision:video-source:diana-v1",
            "approved_semantic_scopes": ["bidding/natural/v1/response-to-1h"],
        },
        "semantic_scope": "bidding/natural/v1/response-to-1h",
        "normalized_rule": {
            "auction_pattern": ["1H", "PASS", "?"],
            "action": "2C",
            "forcing": True,
        },
        "semantic_confidence": 0.91,
        "ambiguities": [],
        "contradictions": [],
        "tests": {
            "positive": [{"auction": ["1H", "PASS"], "expect": "2C"}],
            "negative": [{"auction": ["1S", "PASS"], "expect": "NO_MATCH"}],
            "boundary": [{"auction": ["1H", "X"], "expect": "NO_MATCH"}],
            "interference": [{"auction": ["1H", "2D"], "expect": "NO_MATCH"}],
        },
    }


def test_builds_review_eligible_staging_record_without_canon_write_power():
    result = build_video_canon_candidate(_learning(), _assertion())

    assert result["quality_status"] == "ELIGIBLE"
    assert result["promotion_status"] == "STAGING_ONLY"
    assert result["authoritative_tables_modified"] is False
    assert result["payload"]["activation"]["school_canon_write_allowed"] is False
    assert result["payload"]["activation"]["i2_review_required"] is True
    assert len(result["payload_hash"]) == 64


def test_unapproved_teaching_video_is_evidence_only():
    assertion = _assertion()
    assertion["source_class"] = "TEACHING_CONTEXT"
    assertion["source_authorization"] = {
        "status": "NOT_APPROVED", "decision_ref": "", "approved_semantic_scopes": []
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


def test_payload_hash_is_deterministic_and_source_bound():
    first = build_video_canon_candidate(_learning(), _assertion())
    second = build_video_canon_candidate(deepcopy(_learning()), deepcopy(_assertion()))
    assert first["payload_hash"] == second["payload_hash"]

    changed = _learning()
    changed["source"]["source_sha256"] = "d" * 64
    third = build_video_canon_candidate(changed, _assertion())
    assert third["payload_hash"] != first["payload_hash"]
