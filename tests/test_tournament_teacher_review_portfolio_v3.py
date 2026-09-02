import copy

import pytest

from bridge_school_api.tournament_teacher_decisions_v3 import (
    build_pending_teacher_decision_ledger,
    serialize_teacher_decision_ledger,
)
from bridge_school_api.tournament_teacher_review_bundle_v3 import build_teacher_review_bundle
from bridge_school_api.tournament_teacher_review_portfolio_v3 import (
    TeacherReviewPortfolioError,
    build_teacher_review_portfolio,
    verify_teacher_review_portfolio,
)


def _hands():
    ranks = "23456789TJQKA"
    cards = [rank + suit for suit in "CDHS" for rank in ranks]
    return {
        "N": cards[0:13],
        "E": cards[13:26],
        "S": cards[26:39],
        "W": cards[39:52],
    }


def _bundle(*, event_id: str, deal_id: str, category: str, marker: str):
    item = {
        "event_id": event_id,
        "deal_id": deal_id,
        "category": category,
        "causal_link": "NOT_ESTABLISHED",
        "student_error_attribution_allowed": False,
        "teacher_review_required": True,
        "marker": marker,
    }
    queue = {
        "schema": "tournament-teacher-review-queue-v1",
        "cross_event_numeric_ranking_allowed": False,
        "causal_error_attribution_allowed": False,
        "student_error_attribution_allowed": False,
        "lanes": [
            {
                "event_id": event_id,
                "ranking_scope": "WITHIN_EVENT_ONLY",
                "items": [item],
            }
        ],
    }
    ledger = serialize_teacher_decision_ledger(build_pending_teacher_decision_ledger(queue))
    decision = ledger["decisions"][0]
    dossier = {
        "schema": "tournament-teacher-review-dossier-v1",
        "queue_sha256": ledger["queue_sha256"],
        "automatic_decisions_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "cross_event_numeric_ranking_allowed": False,
        "items": [
            {
                "review_id": decision["review_id"],
                "event_id": event_id,
                "deal_id": deal_id,
                "category": category,
                "status": "PENDING",
                "teacher_decision_required": True,
                "causal_link": "NOT_ESTABLISHED",
                "automatic_methodology_mapping_allowed": False,
                "automatic_student_error_attribution_allowed": False,
                "methodology_mapping": None,
                "student_error_attribution": None,
                "deal_facts": {"hands": _hands()},
                "technical_finding": {
                    "evidence": [{"kind": "DDS_FACT", "message": marker}],
                },
            }
        ],
    }
    return build_teacher_review_bundle(queue, ledger, dossier)


def test_portfolio_preserves_batches_and_separate_signal_families():
    contract = _bundle(event_id="30041", deal_id="30041:round-2:2", category="contract_result", marker="c")
    lead = _bundle(event_id="30041", deal_id="30041:round-2:2", category="opening_lead_dds3", marker="l")

    portfolio = build_teacher_review_portfolio([lead, contract])
    verify_teacher_review_portfolio(portfolio, [lead, contract])

    assert portfolio["source_bundle_count"] == 2
    assert portfolio["item_count"] == 2
    assert portfolio["pending_decision_count"] == 2
    assert portfolio["event_counts"] == {"30041": 2}
    assert portfolio["category_counts"] == {"contract_result": 1, "opening_lead_dds3": 1}
    assert portfolio["multi_signal_deal_count"] == 1
    assert portfolio["multi_signal_deals"][0]["causal_collapse_allowed"] is False
    assert portfolio["review_state"] == "BLOCKED_PENDING_TEACHER_DECISIONS"
    assert portfolio["cross_batch_numeric_ranking_allowed"] is False
    assert portfolio["cross_category_causal_collapse_allowed"] is False
    assert all(row["status"] == "PENDING" for row in portfolio["items"])


def test_portfolio_is_deterministic_across_bundle_order():
    a = _bundle(event_id="29912", deal_id="29912:r1:5", category="contract_result", marker="a")
    b = _bundle(event_id="30041", deal_id="30041:round-2:8", category="opening_lead_dds3", marker="b")
    assert build_teacher_review_portfolio([a, b]) == build_teacher_review_portfolio([b, a])


def test_portfolio_rejects_duplicate_logical_review_across_batches():
    a = _bundle(event_id="30041", deal_id="30041:round-2:2", category="contract_result", marker="a")
    b = _bundle(event_id="30041", deal_id="30041:round-2:2", category="contract_result", marker="b")
    with pytest.raises(TeacherReviewPortfolioError):
        build_teacher_review_portfolio([a, b])


def test_portfolio_verification_rejects_tampering():
    a = _bundle(event_id="30041", deal_id="30041:round-2:10", category="opening_lead_dds3", marker="a")
    portfolio = build_teacher_review_portfolio([a])
    broken = copy.deepcopy(portfolio)
    broken["items"][0]["methodology_mapping"] = "invented"
    with pytest.raises(TeacherReviewPortfolioError):
        verify_teacher_review_portfolio(broken, [a])
