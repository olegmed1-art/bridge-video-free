import pytest

from bridge_school_api.tournament_teacher_review_queue_v3 import (
    TournamentTeacherReviewQueueError,
    build_cross_event_teacher_review_queue,
    serialize_teacher_review_queue,
)


def _30041():
    return {
        "schema": "tournament-mp-outcome-context-v1",
        "event_id": "30041",
        "dd_to_mp_conversion_available": False,
        "causal_error_attribution_allowed": False,
        "technical_finding_context": [
            {
                "deal_id": "30041:round-2:3",
                "category": "dds3_pair_same_contract_delta",
                "technical_trick_loss": 2.0,
                "observed_pair_percentage": 22.5,
                "observed_gap_to_neutral": 27.5,
                "causal_link": "NOT_ESTABLISHED",
            },
            {
                "deal_id": "30041:round-2:8",
                "category": "dds3_pair_same_contract_delta",
                "technical_trick_loss": 1.0,
                "observed_pair_percentage": 88.5,
                "observed_gap_to_neutral": 0.0,
                "causal_link": "NOT_ESTABLISHED",
            },
        ],
    }


def _29912():
    return {
        "schema": "tournament-29912-source-score-context-v1",
        "event_id": "29912",
        "percentage_conversion_available": False,
        "dd_to_score_conversion_available": False,
        "causal_error_attribution_allowed": False,
        "student_error_attribution_allowed": False,
        "technical_finding_context": [
            {
                "deal_id": "29912:round-6:16",
                "category": "dds3_pair_same_contract_delta",
                "technical_trick_loss": 1.0,
                "source_pair_score_contribution": -13.0,
                "negative_score_contribution": 13.0,
                "source_consistency_ok": True,
                "causal_link": "NOT_ESTABLISHED",
            },
            {
                "deal_id": "29912:round-2:1",
                "category": "dds3_pair_same_contract_delta",
                "technical_trick_loss": 2.0,
                "source_pair_score_contribution": 5.0,
                "negative_score_contribution": 0.0,
                "source_consistency_ok": True,
                "causal_link": "NOT_ESTABLISHED",
            },
        ],
    }


def test_cross_event_queue_keeps_scales_in_separate_lanes():
    queue = build_cross_event_teacher_review_queue(_30041(), _29912(), per_event_limit=1)
    payload = serialize_teacher_review_queue(queue)
    assert payload["cross_event_numeric_ranking_allowed"] is False
    assert payload["causal_error_attribution_allowed"] is False
    assert payload["student_error_attribution_allowed"] is False
    assert [lane["event_id"] for lane in payload["lanes"]] == ["30041", "29912"]
    assert payload["lanes"][0]["outcome_scale"] == "MP_PERCENTAGE"
    assert payload["lanes"][1]["outcome_scale"] == "SIGNED_SOURCE_SCORE_CONTRIBUTION"
    assert payload["lanes"][0]["items"][0]["deal_id"] == "30041:round-2:3"
    assert payload["lanes"][1]["items"][0]["deal_id"] == "29912:round-6:16"


def test_29912_source_inconsistent_finding_fails_closed():
    bad = _29912()
    bad["technical_finding_context"] = [{**bad["technical_finding_context"][0], "source_consistency_ok": False}]
    with pytest.raises(TournamentTeacherReviewQueueError, match="source-inconsistent"):
        build_cross_event_teacher_review_queue(_30041(), bad)


def test_causal_boundary_cannot_be_weakened():
    bad = _30041()
    bad["technical_finding_context"] = [{**bad["technical_finding_context"][0], "causal_link": "ESTABLISHED"}]
    with pytest.raises(TournamentTeacherReviewQueueError, match="causal boundary"):
        build_cross_event_teacher_review_queue(bad, _29912())
