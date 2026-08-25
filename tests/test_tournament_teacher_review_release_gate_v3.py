import pytest

import bridge_school_api.tournament_teacher_review_release_gate_v3 as gate


def _decision(review_id, event_id, deal_id, category, status):
    return {
        "review_id": review_id,
        "event_id": event_id,
        "deal_id": deal_id,
        "category": category,
        "status": status,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
    }


def _payload(status_a="PENDING", status_b="PENDING"):
    portfolio = {
        "portfolio_id": "p" * 64,
        "items": [
            {
                "source_bundle_id": "a" * 64,
                "review_id": "r1",
                "event_id": "30041",
                "deal_id": "30041:round-2:2",
                "category": "contract_result",
            },
            {
                "source_bundle_id": "b" * 64,
                "review_id": "r2",
                "event_id": "30041",
                "deal_id": "30041:round-2:2",
                "category": "opening_lead_dds3",
            },
            {
                "source_bundle_id": "a" * 64,
                "review_id": "r3",
                "event_id": "29912",
                "deal_id": "29912:round-1:5",
                "category": "contract_result",
            },
        ],
    }
    result = {
        "schema": "tournament-teacher-review-portfolio-decision-result-v1",
        "portfolio_id": portfolio["portfolio_id"],
        "bundle_results": [
            {
                "source_bundle_id": "a" * 64,
                "ledger": {
                    "schema": "tournament-teacher-decision-ledger-v1",
                    "decisions": [
                        _decision("r1", "30041", "30041:round-2:2", "contract_result", status_a),
                        _decision("r3", "29912", "29912:round-1:5", "contract_result", "PENDING"),
                    ],
                },
            },
            {
                "source_bundle_id": "b" * 64,
                "ledger": {
                    "schema": "tournament-teacher-decision-ledger-v1",
                    "decisions": [
                        _decision("r2", "30041", "30041:round-2:2", "opening_lead_dds3", status_b),
                    ],
                },
            },
        ],
    }
    return portfolio, result


def test_pending_later_batch_blocks_event_release(monkeypatch):
    portfolio, result = _payload()
    monkeypatch.setattr(gate, "verify_teacher_review_portfolio", lambda p, b: None)
    monkeypatch.setattr(
        gate,
        "build_portfolio_teacher_confirmed_longitudinal_report",
        lambda p, b, r: {"confirmed_items": []},
    )
    out = gate.build_event_teacher_review_release_gate(portfolio, [{}, {}], result, event_id="30041")
    assert out["review_item_count"] == 2
    assert out["unresolved_review_count"] == 2
    assert out["teacher_review_release_ready"] is False
    assert out["release_blockers"] == ["TEACHER_REVIEW_PORTFOLIO_UNRESOLVED"]
    assert out["cross_category_causal_collapse_allowed"] is False


def test_confirmed_and_dismissed_open_teacher_review_gate_only(monkeypatch):
    portfolio, result = _payload("CONFIRMED_TECHNICAL_RELEVANCE", "DISMISSED")
    monkeypatch.setattr(gate, "verify_teacher_review_portfolio", lambda p, b: None)
    monkeypatch.setattr(
        gate,
        "build_portfolio_teacher_confirmed_longitudinal_report",
        lambda p, b, r: {
            "confirmed_items": [
                {
                    "source_bundle_id": "a" * 64,
                    "review_id": "r1",
                    "event_id": "30041",
                }
            ]
        },
    )
    out = gate.build_event_teacher_review_release_gate(portfolio, [{}, {}], result, event_id="30041")
    assert out["confirmed_technical_count"] == 1
    assert out["dismissed_count"] == 1
    assert out["unresolved_review_count"] == 0
    assert out["teacher_review_release_ready"] is True
    assert out["automatic_episode_scoring_allowed"] is False
    assert out["automatic_methodology_mapping_allowed"] is False
    assert out["automatic_student_error_attribution_allowed"] is False


def test_missing_bundle_decision_fails_closed(monkeypatch):
    portfolio, result = _payload()
    result["bundle_results"][1]["ledger"]["decisions"] = []
    monkeypatch.setattr(gate, "verify_teacher_review_portfolio", lambda p, b: None)
    monkeypatch.setattr(
        gate,
        "build_portfolio_teacher_confirmed_longitudinal_report",
        lambda p, b, r: {"confirmed_items": []},
    )
    with pytest.raises(gate.TournamentTeacherReviewReleaseGateError, match="no matching teacher decision"):
        gate.build_event_teacher_review_release_gate(portfolio, [{}, {}], result, event_id="30041")


def test_confirmed_projection_count_must_match(monkeypatch):
    portfolio, result = _payload("CONFIRMED_TECHNICAL_RELEVANCE", "DISMISSED")
    monkeypatch.setattr(gate, "verify_teacher_review_portfolio", lambda p, b: None)
    monkeypatch.setattr(
        gate,
        "build_portfolio_teacher_confirmed_longitudinal_report",
        lambda p, b, r: {"confirmed_items": []},
    )
    with pytest.raises(gate.TournamentTeacherReviewReleaseGateError, match="confirmed event count disagrees"):
        gate.build_event_teacher_review_release_gate(portfolio, [{}, {}], result, event_id="30041")
