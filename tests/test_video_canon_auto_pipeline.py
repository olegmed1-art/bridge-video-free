from __future__ import annotations

from bridge_contracts.video_canon_auto_pipeline import run_video_canon_auto_pipeline
from bridge_contracts.video_canon_ai_promotion import build_ai_canon_promotion
from bridge_contracts.video_canon_evidence import build_video_canon_candidate
from tests.test_video_canon_ai_promotion import _bundle
from tests.test_video_canon_evidence import _assertion, _learning


def test_pipeline_prepares_automatic_activation_without_human_review():
    assertion = _assertion()
    assertion["semantic_confidence"] = 0.99
    candidate = build_video_canon_candidate(_learning(), assertion)
    result = run_video_canon_auto_pipeline(
        _learning(), [assertion], {assertion["assertion_id"]: _bundle(candidate)}
    )

    assert result["status"] == "AUTO_PROMOTION_READY"
    assert result["human_approval_required"] is False
    assert result["authoritative_write_performed"] is False
    assert len(result["promotion_commands"]) == 1
    assert len(result["candidates"]) == 1
    assert result["gaps"] == []


def test_pipeline_keeps_missing_or_failed_verification_out_of_canon():
    assertion = _assertion()
    assertion["semantic_confidence"] = 0.99
    result = run_video_canon_auto_pipeline(_learning(), [assertion], {})
    assert result["status"] == "NO_PROMOTION_READY"
    assert result["gaps"][0]["status"] == "AI_VERIFICATION_PENDING"

    candidate = build_video_canon_candidate(_learning(), assertion)
    bundle = _bundle(candidate)
    bundle["checks"][0]["result"] = "FAIL"
    result = run_video_canon_auto_pipeline(
        _learning(), [assertion], {assertion["assertion_id"]: bundle}
    )
    assert result["status"] == "NO_PROMOTION_READY"
    assert result["gaps"][0]["status"] == "AI_VERIFICATION_FAILED"


def test_pipeline_never_calls_world_for_conflicting_video_rule():
    assertion = _assertion()
    assertion["semantic_confidence"] = 0.99
    assertion["contradictions"] = ["active Canon says non-forcing"]
    result = run_video_canon_auto_pipeline(_learning(), [assertion], {})
    assert result["promotion_commands"] == []
    assert result["world_lookup_performed"] is False
