import copy
import hashlib
import json

import pytest

from bridge_school_api.tournament_release_provenance_v3 import (
    TournamentReleaseProvenanceError,
    build_release_provenance_receipt,
    verify_release_provenance_receipt,
)


def _inputs():
    coverage = {
        "schema": "tournament-coverage-manifest-v1",
        "normative_algorithm_version": "1.4",
        "provider_native_key": "bridge.co.il:event:30041:round:2",
        "episode_inventory_complete": True,
        "coverage_plan_release_ready": True,
        "release_blockers": [],
        "expected_slide_keys": ["deck-title", "board-2-base", "deck-final"],
    }
    pre = {
        "schema": "tournament-preanalysis-gate-v1",
        "tournament": {"provider_native_key": coverage["provider_native_key"]},
        "facts_only_analysis_ready": True,
        "hard_stop_conditions": [],
    }
    mp = {
        "schema": "tournament-mp-recalculation-availability-v1",
        "status": "OFFICIAL_PERCENTAGE_NOT_INDEPENDENTLY_RECALCULATED",
        "full_traveller_available": False,
    }
    portfolio_id = "a" * 64
    review = {
        "schema": "tournament-event-teacher-review-release-gate-v1",
        "normative_algorithm_version": "1.4",
        "event_id": "30041",
        "portfolio_id": portfolio_id,
        "review_item_count": 11,
        "unresolved_review_count": 0,
        "teacher_review_release_ready": True,
        "release_blockers": [],
    }
    handoff = {
        "schema": "tournament-portfolio-episode-coverage-handoff-v1",
        "normative_algorithm_version": "1.4",
        "event_id": "30041",
        "portfolio_id": portfolio_id,
        "event_review_item_count": 11,
        "coverage_manifest": coverage,
        "handoff_ready": True,
        "handoff_blockers": [],
    }
    release = {
        "schema": "tournament-v1.4-artifact-derived-portfolio-release-gate-v1",
        "event_id": "30041",
        "teacher_review_portfolio_id": portfolio_id,
        "teacher_review_item_count": 11,
        "final_report_release_ready": True,
        "artifact_derived_render_evidence_enforced": True,
        "caller_supplied_render_sha_accepted": False,
        "caller_supplied_render_size_accepted": False,
        "caller_supplied_slide_order_accepted": False,
        "caller_supplied_visual_qa_pass_accepted": False,
        "automatic_episode_scoring_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "automatic_methodology_invention_allowed": False,
        "render_release_evidence": {
            "render_evidence_gate_pass": True,
            "visual_qa_evidence_gate_pass": True,
            "render_artifact_sha256": "b" * 64,
            "render_artifact_size_bytes": 12345,
            "rendered_page_count": 3,
        },
    }
    return pre, coverage, mp, review, handoff, release


def _build(*, mutate=None):
    pre, coverage, mp, review, handoff, release = _inputs()
    if mutate:
        mutate(pre, coverage, mp, review, handoff, release)
    return build_release_provenance_receipt(
        preanalysis_gate=pre,
        coverage_manifest=coverage,
        mp_availability=mp,
        event_teacher_review_gate=review,
        portfolio_episode_coverage_handoff=handoff,
        artifact_derived_release_gate=release,
        provenance={"run_id": "release-run", "actor": "ci"},
    )


def _recalculate_release_id(receipt):
    identity = {
        key: value
        for key, value in receipt.items()
        if key not in {"release_id", "content_addressed_release_receipt"}
    }
    raw = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def test_builds_deterministic_content_addressed_release_receipt():
    first = _build()
    second = _build()

    assert first == second
    assert first["content_addressed_release_receipt"] is True
    assert first["render_artifact"]["sha256"] == "b" * 64
    verified = verify_release_provenance_receipt(first)
    assert verified["status"] == "PASS"
    assert verified["release_id"] == first["release_id"]
    assert verified["release_safety_boundaries_verified"] is True


def test_rejects_unready_release():
    def mutate(_pre, _coverage, _mp, _review, _handoff, release):
        release["final_report_release_ready"] = False

    with pytest.raises(TournamentReleaseProvenanceError, match="not release-ready"):
        _build(mutate=mutate)


def test_rejects_coverage_handoff_mismatch():
    def mutate(_pre, coverage, _mp, _review, handoff, _release):
        handoff["coverage_manifest"] = {**coverage, "expected_slide_keys": ["different"]}

    with pytest.raises(TournamentReleaseProvenanceError, match="exact release coverage"):
        _build(mutate=mutate)


def test_rejects_weakened_automatic_methodology_boundary():
    def mutate(_pre, _coverage, _mp, _review, _handoff, release):
        release["automatic_methodology_invention_allowed"] = True

    with pytest.raises(TournamentReleaseProvenanceError, match="boundary weakened"):
        _build(mutate=mutate)


def test_detects_tampered_receipt():
    receipt = _build()
    tampered = copy.deepcopy(receipt)
    tampered["render_artifact"]["size_bytes"] += 1

    with pytest.raises(TournamentReleaseProvenanceError, match="digest mismatch"):
        verify_release_provenance_receipt(tampered)


def test_verifier_rejects_self_consistent_but_weakened_receipt():
    forged = copy.deepcopy(_build())
    forged["automatic_methodology_mapping_used"] = True
    forged["release_id"] = _recalculate_release_id(forged)

    with pytest.raises(TournamentReleaseProvenanceError, match="boundary weakened"):
        verify_release_provenance_receipt(forged)
