import copy

import pytest

import bridge_school_api.tournament_teacher_review_portfolio_intake_v3 as mod


def _source_template(bundle_id, review_id, deal_id, category):
    return {
        "schema": "tournament-teacher-decision-intake-v1",
        "bundle_id": bundle_id,
        "queue_sha256": f"q-{bundle_id}",
        "automatic_decisions_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "required_decision_source": "EXPLICIT_TEACHER_DECISION",
        "decisions": [
            {
                "review_id": review_id,
                "event_id": "30041",
                "deal_id": deal_id,
                "category": category,
                "allowed_statuses": ["CONFIRMED_TECHNICAL_RELEVANCE", "DISMISSED", "NEEDS_CONTEXT"],
                "status": None,
                "decision_note": None,
                "decision_actor": None,
                "decision_reference": None,
                "explicit_teacher_decision": False,
            }
        ],
    }


def _setup(monkeypatch):
    bundles = [
        {"bundle_id": "bundle-a"},
        {"bundle_id": "bundle-b"},
    ]
    templates = {
        "bundle-a": _source_template("bundle-a", "r-a", "30041:round-2:2", "contract_result"),
        "bundle-b": _source_template("bundle-b", "r-b", "30041:round-2:2", "opening_lead_dds3"),
    }
    portfolio = {
        "portfolio_id": "p" * 64,
        "source_bundles": [{"bundle_id": "bundle-a"}, {"bundle_id": "bundle-b"}],
        "items": [
            {
                "source_bundle_id": "bundle-a",
                "review_id": "r-a",
                "event_id": "30041",
                "deal_id": "30041:round-2:2",
                "category": "contract_result",
            },
            {
                "source_bundle_id": "bundle-b",
                "review_id": "r-b",
                "event_id": "30041",
                "deal_id": "30041:round-2:2",
                "category": "opening_lead_dds3",
            },
        ],
    }
    monkeypatch.setattr(mod, "verify_teacher_review_portfolio", lambda p, b: None)
    monkeypatch.setattr(mod, "build_teacher_decision_template", lambda b: copy.deepcopy(templates[b["bundle_id"]]))

    def fake_apply(bundle, intake):
        decided = sum(1 for row in intake["decisions"] if row.get("status"))
        return {
            "decided_count": decided,
            "pending_count": len(intake["decisions"]) - decided,
            "ledger": {"bundle_id": bundle["bundle_id"], "rows": copy.deepcopy(intake["decisions"])},
        }

    monkeypatch.setattr(mod, "apply_teacher_decision_intake", fake_apply)
    return portfolio, bundles


def test_portfolio_template_keeps_source_bundle_and_multiple_categories(monkeypatch):
    portfolio, bundles = _setup(monkeypatch)
    template = mod.build_portfolio_teacher_decision_template(portfolio, bundles)
    assert template["decision_count"] == 2
    assert {row["source_bundle_id"] for row in template["decisions"]} == {"bundle-a", "bundle-b"}
    assert {row["category"] for row in template["decisions"]} == {"contract_result", "opening_lead_dds3"}
    assert all(row["status"] is None and row["explicit_teacher_decision"] is False for row in template["decisions"])


def test_untouched_portfolio_intake_leaves_everything_pending(monkeypatch):
    portfolio, bundles = _setup(monkeypatch)
    template = mod.build_portfolio_teacher_decision_template(portfolio, bundles)
    result = mod.apply_portfolio_teacher_decision_intake(portfolio, bundles, template)
    assert result["decided_count"] == 0
    assert result["pending_count"] == 2
    assert result["review_state"] == "PENDING_TEACHER_DECISIONS"
    assert len(result["bundle_results"]) == 2


def test_explicit_row_routes_only_to_its_original_bundle(monkeypatch):
    portfolio, bundles = _setup(monkeypatch)
    intake = mod.build_portfolio_teacher_decision_template(portfolio, bundles)
    intake["decisions"][1].update(
        status="CONFIRMED_TECHNICAL_RELEVANCE",
        explicit_teacher_decision=True,
        decision_actor="teacher",
    )
    result = mod.apply_portfolio_teacher_decision_intake(portfolio, bundles, intake)
    by_id = {row["source_bundle_id"]: row for row in result["bundle_results"]}
    assert by_id["bundle-a"]["decided_count"] == 0
    assert by_id["bundle-b"]["decided_count"] == 1
    assert result["decided_count"] == 1
    assert result["pending_count"] == 1


def test_intake_rejects_cross_bundle_rebinding(monkeypatch):
    portfolio, bundles = _setup(monkeypatch)
    intake = mod.build_portfolio_teacher_decision_template(portfolio, bundles)
    intake["decisions"][0]["source_bundle_id"] = "bundle-b"
    with pytest.raises(mod.TeacherReviewPortfolioIntakeError):
        mod.apply_portfolio_teacher_decision_intake(portfolio, bundles, intake)
