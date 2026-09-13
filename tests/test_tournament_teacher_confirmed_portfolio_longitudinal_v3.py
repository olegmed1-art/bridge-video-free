import copy

import pytest

import bridge_school_api.tournament_teacher_confirmed_portfolio_longitudinal_v3 as mod
from bridge_school_api.tournament_teacher_decisions_v3 import TeacherDecisionStatus


def _status_counts(**overrides):
    out = {status.value: 0 for status in TeacherDecisionStatus}
    out.update(overrides)
    return out


def _setup(monkeypatch):
    portfolio = {"portfolio_id": "p" * 64}
    bundles = [{"bundle_id": "bundle-a"}, {"bundle_id": "bundle-b"}]
    monkeypatch.setattr(mod, "verify_teacher_review_portfolio", lambda p, b: None)

    reports = {
        "bundle-a": {
            "bundle_id": "bundle-a",
            "queue_sha256": "qa",
            "status_counts": _status_counts(CONFIRMED_TECHNICAL_RELEVANCE=1),
            "confirmed_items": [
                {
                    "review_id": "ra",
                    "event_id": "29912",
                    "deal_id": "29912:round-1:2",
                    "category": "contract_result",
                    "repeat_key": "same-contract-dds3",
                    "technical_trick_loss": 2,
                    "causal_link": "NOT_ESTABLISHED",
                    "student_error_attribution": None,
                    "methodology_mapping": None,
                }
            ],
        },
        "bundle-b": {
            "bundle_id": "bundle-b",
            "queue_sha256": "qb",
            "status_counts": _status_counts(CONFIRMED_TECHNICAL_RELEVANCE=1, PENDING=1),
            "confirmed_items": [
                {
                    "review_id": "rb",
                    "event_id": "30041",
                    "deal_id": "30041:round-2:10",
                    "category": "contract_result",
                    "repeat_key": "same-contract-dds3",
                    "technical_trick_loss": 1,
                    "causal_link": "NOT_ESTABLISHED",
                    "student_error_attribution": None,
                    "methodology_mapping": None,
                }
            ],
        },
    }
    monkeypatch.setattr(
        mod,
        "build_teacher_confirmed_longitudinal_report",
        lambda bundle, ledger: copy.deepcopy(reports[bundle["bundle_id"]]),
    )
    result = {
        "schema": "tournament-teacher-review-portfolio-decision-result-v1",
        "portfolio_id": portfolio["portfolio_id"],
        "source_bundle_count": 2,
        "decided_count": 2,
        "pending_count": 1,
        "automatic_decisions_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "bundle_results": [
            {"source_bundle_id": "bundle-a", "ledger": {"id": "la"}},
            {"source_bundle_id": "bundle-b", "ledger": {"id": "lb"}},
        ],
    }
    return portfolio, bundles, result


def test_aggregates_confirmed_items_without_losing_bundle_identity(monkeypatch):
    portfolio, bundles, result = _setup(monkeypatch)
    report = mod.build_portfolio_teacher_confirmed_longitudinal_report(portfolio, bundles, result)
    assert len(report["confirmed_items"]) == 2
    assert {row["source_bundle_id"] for row in report["confirmed_items"]} == {"bundle-a", "bundle-b"}
    assert report["status_counts"]["CONFIRMED_TECHNICAL_RELEVANCE"] == 2
    assert report["status_counts"]["PENDING"] == 1
    assert report["automatic_methodology_mapping_allowed"] is False
    assert report["automatic_student_error_attribution_allowed"] is False


def test_clusters_only_explicit_repeat_key_across_events(monkeypatch):
    portfolio, bundles, result = _setup(monkeypatch)
    report = mod.build_portfolio_teacher_confirmed_longitudinal_report(portfolio, bundles, result)
    assert len(report["persistent_clusters"]) == 1
    cluster = report["persistent_clusters"][0]
    assert cluster["repeat_key"] == "same-contract-dds3"
    assert cluster["event_ids"] == ["29912", "30041"]
    assert cluster["technical_trick_loss_mass"] == 3.0
    assert cluster["causal_link"] == "NOT_ESTABLISHED"
    assert cluster["student_error_attribution"] is None


def test_rejects_weakened_portfolio_decision_boundary(monkeypatch):
    portfolio, bundles, result = _setup(monkeypatch)
    result["automatic_student_error_attribution_allowed"] = True
    with pytest.raises(mod.TeacherConfirmedPortfolioLongitudinalError):
        mod.build_portfolio_teacher_confirmed_longitudinal_report(portfolio, bundles, result)


def test_rejects_decision_count_drift(monkeypatch):
    portfolio, bundles, result = _setup(monkeypatch)
    result["pending_count"] = 2
    with pytest.raises(mod.TeacherConfirmedPortfolioLongitudinalError):
        mod.build_portfolio_teacher_confirmed_longitudinal_report(portfolio, bundles, result)
