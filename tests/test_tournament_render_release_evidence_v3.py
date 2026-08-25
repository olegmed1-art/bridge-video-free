import copy
import hashlib
import json

import pytest

from bridge_school_api.tournament_render_release_evidence_v3 import (
    TournamentRenderEvidenceError,
    build_evidence_bound_portfolio_release_gate,
    validate_render_release_evidence,
)


def _sha(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _base_inputs():
    pre = {
        "schema": "tournament-preanalysis-gate-v1",
        "run_id": "run",
        "tournament": {"provider_native_key": "bridge.co.il:event:30041:round:2"},
        "facts_only_analysis_ready": True,
        "full_causal_replay_ready": False,
        "hard_stop_conditions": [],
        "limitations": [],
    }
    coverage = {
        "schema": "tournament-coverage-manifest-v1",
        "normative_algorithm_version": "1.4",
        "provider_native_key": "bridge.co.il:event:30041:round:2",
        "episode_inventory_complete": True,
        "coverage_plan_release_ready": True,
        "release_blockers": [],
        "expected_slide_keys": ["deck-title", "deck-overview", "board-2-base", "deck-final"],
    }
    mp = {
        "schema": "tournament-mp-recalculation-availability-v1",
        "full_traveller_available": False,
        "status": "OFFICIAL_PERCENTAGE_NOT_INDEPENDENTLY_RECALCULATED",
    }
    review = {
        "schema": "tournament-event-teacher-review-release-gate-v1",
        "normative_algorithm_version": "1.4",
        "portfolio_id": "a" * 64,
        "event_id": "30041",
        "review_item_count": 11,
        "unresolved_review_count": 0,
        "teacher_review_release_ready": True,
        "release_blockers": [],
        "cross_category_causal_collapse_allowed": False,
        "automatic_episode_scoring_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "causal_error_attribution_allowed": False,
    }
    handoff = {
        "schema": "tournament-portfolio-episode-coverage-handoff-v1",
        "normative_algorithm_version": "1.4",
        "portfolio_id": review["portfolio_id"],
        "event_id": "30041",
        "event_review_item_count": 11,
        "coverage_manifest": coverage,
        "portfolio_complete_for_event": True,
        "teacher_decision_gate_enforced": True,
        "cross_category_causal_collapse_allowed": False,
        "automatic_teacher_decisions_used": False,
        "automatic_episode_scoring_used": False,
        "automatic_methodology_mapping_used": False,
        "automatic_student_error_attribution_used": False,
        "causal_error_attribution_allowed": False,
        "handoff_ready": True,
        "handoff_blockers": [],
    }
    coverage_sha = _sha(coverage)
    render_sha = "b" * 64
    render = {
        "schema": "tournament-render-evidence-v1",
        "normative_algorithm_version": "1.4",
        "provider_native_key": coverage["provider_native_key"],
        "coverage_manifest_sha256": coverage_sha,
        "artifact": {
            "sha256": render_sha,
            "size_bytes": 123456,
            "media_type": "application/pdf",
        },
        "slide_keys": coverage["expected_slide_keys"],
        "rendered_page_count": len(coverage["expected_slide_keys"]),
        "renderer": {"name": "LibreOffice", "version": "26.2"},
        "provenance": {"workflow_run_id": 123},
    }
    qa = {
        "schema": "tournament-visual-qa-evidence-v1",
        "normative_algorithm_version": "1.4",
        "provider_native_key": coverage["provider_native_key"],
        "coverage_manifest_sha256": coverage_sha,
        "render_artifact_sha256": render_sha,
        "checked_slide_count": len(coverage["expected_slide_keys"]),
        "status": "PASS",
        "pass": True,
        "hard_failures": [],
        "qa_engine": {"name": "pptx_artifact_qa", "version": "1"},
        "provenance": {"workflow_run_id": 123, "job": "visual-qa"},
    }
    return pre, coverage, mp, review, handoff, render, qa


def test_exact_render_and_qa_evidence_can_open_resolved_release():
    pre, coverage, mp, review, handoff, render, qa = _base_inputs()
    out = build_evidence_bound_portfolio_release_gate(
        preanalysis_gate=pre,
        coverage_manifest=coverage,
        mp_availability=mp,
        event_teacher_review_gate=review,
        portfolio_episode_coverage_handoff=handoff,
        render_evidence=render,
        visual_qa_evidence=qa,
    )
    assert out["schema"] == "tournament-v1.4-evidence-bound-portfolio-release-gate-v1"
    assert out["final_report_release_ready"] is True
    assert out["render_evidence_gate_enforced"] is True
    assert out["visual_qa_evidence_gate_enforced"] is True
    assert out["bare_visual_qa_boolean_accepted"] is False
    assert out["bare_rendered_slide_key_list_accepted"] is False
    assert out["render_release_evidence"]["render_artifact_sha256"] == "b" * 64


def test_render_must_bind_exact_coverage_manifest():
    _, coverage, _, _, _, render, qa = _base_inputs()
    bad = copy.deepcopy(render)
    bad["coverage_manifest_sha256"] = "c" * 64
    with pytest.raises(TournamentRenderEvidenceError, match="exact coverage manifest"):
        validate_render_release_evidence(
            coverage_manifest=coverage,
            render_evidence=bad,
            visual_qa_evidence=qa,
        )


def test_rendered_slide_order_must_match_coverage_exactly():
    _, coverage, _, _, _, render, qa = _base_inputs()
    bad = copy.deepcopy(render)
    bad["slide_keys"] = list(reversed(render["slide_keys"]))
    with pytest.raises(TournamentRenderEvidenceError, match="exactly match coverage order"):
        validate_render_release_evidence(
            coverage_manifest=coverage,
            render_evidence=bad,
            visual_qa_evidence=qa,
        )


def test_visual_qa_must_bind_same_render_artifact():
    _, coverage, _, _, _, render, qa = _base_inputs()
    bad = copy.deepcopy(qa)
    bad["render_artifact_sha256"] = "d" * 64
    with pytest.raises(TournamentRenderEvidenceError, match="rendered artifact"):
        validate_render_release_evidence(
            coverage_manifest=coverage,
            render_evidence=render,
            visual_qa_evidence=bad,
        )


def test_visual_qa_fail_cannot_be_promoted_by_caller_boolean():
    _, coverage, _, _, _, render, qa = _base_inputs()
    bad = copy.deepcopy(qa)
    bad["status"] = "FAIL"
    bad["pass"] = False
    bad["hard_failures"] = ["blank-page-3"]
    with pytest.raises(TournamentRenderEvidenceError, match="status is not PASS"):
        validate_render_release_evidence(
            coverage_manifest=coverage,
            render_evidence=render,
            visual_qa_evidence=bad,
        )


def test_visual_qa_must_check_every_rendered_page():
    _, coverage, _, _, _, render, qa = _base_inputs()
    bad = copy.deepcopy(qa)
    bad["checked_slide_count"] = qa["checked_slide_count"] - 1
    with pytest.raises(TournamentRenderEvidenceError, match="every rendered page"):
        validate_render_release_evidence(
            coverage_manifest=coverage,
            render_evidence=render,
            visual_qa_evidence=bad,
        )


def test_render_and_qa_provenance_are_mandatory():
    _, coverage, _, _, _, render, qa = _base_inputs()
    bad_render = copy.deepcopy(render)
    bad_render["provenance"] = {}
    with pytest.raises(TournamentRenderEvidenceError, match="render provenance"):
        validate_render_release_evidence(
            coverage_manifest=coverage,
            render_evidence=bad_render,
            visual_qa_evidence=qa,
        )

    bad_qa = copy.deepcopy(qa)
    bad_qa["provenance"] = {}
    with pytest.raises(TournamentRenderEvidenceError, match="visual QA provenance"):
        validate_render_release_evidence(
            coverage_manifest=coverage,
            render_evidence=render,
            visual_qa_evidence=bad_qa,
        )
