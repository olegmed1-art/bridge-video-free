"""Synthetic handoff across candidate, review command, and authority lanes.

No evidence in these fixtures is a real attestation. The guarded database
function must independently verify persisted source policy and checker roles.
"""
from copy import deepcopy
from pathlib import Path

import pytest

from bridge_contracts.book_material import InvalidBookMaterial
from bridge_contracts.book_world import build_world_publication
from bridge_contracts.video_canon_ai_promotion import (
    VideoCanonAIPromotionError,
    build_ai_canon_promotion,
)
from bridge_contracts.video_canon_evidence import build_video_canon_candidate
from tests.test_book_world import reviewed_fixture
from tests.test_video_canon_ai_promotion import _bundle, _candidate
from tests.test_video_canon_evidence import _assertion, _learning


def test_synthetic_candidate_to_sealed_command_is_repeatable_but_not_activation():
    candidate = _candidate()
    assert candidate["promotion_status"] == "STAGING_ONLY"
    assert candidate["authoritative_tables_modified"] is False
    assert candidate["payload"]["activation"]["school_canon_write_allowed"] is False

    review = _bundle(candidate)
    first = build_ai_canon_promotion(candidate, review)
    replay = build_ai_canon_promotion(deepcopy(candidate), deepcopy(review))
    assert first == replay
    assert first["status"] == "AUTO_PROMOTION_READY"
    assert first["promotion_command"]["operation"] == "ACTIVATE_AI_VERIFIED_VIDEO_CANON"
    assert first["safety"]["activation_on_conflict_allowed"] is False
    assert "canon_activation_id" not in first
    assert "knowledge_version_id" not in first


def test_changed_version_and_source_require_fresh_bound_verification():
    original = _candidate()
    stale_review = _bundle(original)

    assertion = _assertion()
    assertion["semantic_confidence"] = 0.98
    assertion["normalized_rule"]["action"] = {"call": "2D"}
    new_version = build_video_canon_candidate(_learning(), assertion)
    assert new_version["stable_key"] != original["stable_key"]
    with pytest.raises(VideoCanonAIPromotionError, match="not bound to candidate"):
        build_ai_canon_promotion(new_version, stale_review)
    refreshed = build_ai_canon_promotion(new_version, _bundle(new_version))
    assert refreshed["promotion_command"]["idempotency_key"] != build_ai_canon_promotion(
        original, stale_review
    )["promotion_command"]["idempotency_key"]

    learning = _learning()
    learning["source"]["source_sha256"] = "d" * 64
    assertion = _assertion()
    assertion["semantic_confidence"] = 0.98
    assertion["source_authorization"]["authorized_source_sha256"] = "d" * 64
    new_source = build_video_canon_candidate(learning, assertion)
    assert new_source["stable_key"] != original["stable_key"]
    with pytest.raises(VideoCanonAIPromotionError, match="not bound to candidate"):
        build_ai_canon_promotion(new_source, stale_review)


def test_unapproved_or_conflicting_video_and_world_evidence_cannot_issue_command():
    for field in ("contradictions", "ambiguities"):
        assertion = _assertion()
        assertion["semantic_confidence"] = 0.98
        assertion[field] = ["unresolved synthetic discrepancy"]
        candidate = build_video_canon_candidate(_learning(), assertion)
        assert candidate["quality_status"] == "EVIDENCE_ONLY"
        with pytest.raises(VideoCanonAIPromotionError, match="not eligible"):
            build_ai_canon_promotion(candidate, _bundle(candidate))

    for source_class in ("TEACHING_CONTEXT", "WORLD_EXTERNAL"):
        assertion = _assertion()
        assertion["semantic_confidence"] = 0.98
        assertion["source_class"] = source_class
        candidate = build_video_canon_candidate(_learning(), assertion)
        assert candidate["quality_status"] == "EVIDENCE_ONLY"
        with pytest.raises(VideoCanonAIPromotionError, match="not eligible"):
            build_ai_canon_promotion(candidate, _bundle(candidate))

    assertion = _assertion()
    assertion["semantic_confidence"] = 0.98
    assertion["source_authorization"] = {
        "status": "NOT_APPROVED", "decision_ref": "", "policy_version": "",
        "authorized_source_sha256": "", "authorized_video_file_id": "",
        "authorized_teacher_ids": [], "approved_semantic_scopes": [],
        "authorization_evidence_sha256": "",
    }
    candidate = build_video_canon_candidate(_learning(), assertion)
    assert candidate["quality_status"] == "EVIDENCE_ONLY"
    with pytest.raises(VideoCanonAIPromotionError, match="not eligible"):
        build_ai_canon_promotion(candidate, _bundle(candidate))


def test_reviewed_book_is_world_only_and_missing_rights_or_review_block_it():
    candidate, receipt = reviewed_fixture()
    with pytest.raises(InvalidBookMaterial):
        build_world_publication(candidate, {})
    no_rights = deepcopy(candidate)
    no_rights["objects"][0]["rights"] = "RESTRICTED"
    with pytest.raises(InvalidBookMaterial):
        build_world_publication(no_rights, receipt)

    publication = build_world_publication(candidate, receipt)
    assert publication["authority_class"] == "external"
    assert "promotion_command" not in publication
    assert "canon_activation_id" not in publication


def test_database_and_teacher_handoff_remain_explicit_separate_boundaries():
    root = Path(__file__).parents[1]
    migration = (root / "database/migrations/0329_workflow_video_canon_ai_promotion.sql").read_text()
    reader = (root / "bridge_school_api/knowledge.py").read_text()
    book_writer = (root / "database/book_world_persistence.py").read_text()
    assert "CREATE OR REPLACE FUNCTION bidding.activate_ai_verified_video_canon" in migration
    assert "VIDEO_CANON_SOURCE_POLICY_NOT_FOUND" in migration
    assert "VIDEO_CANON_STATE_CHECKS_STALE" in migration
    assert "TO bridge_school_canon_promoter" in migration
    assert "JOIN public.canon_activation activation" in reader
    assert "kv.authority_class = %s" in reader
    assert "canon_activation_performed\": False" in book_writer
    assert "INSERT INTO public.canon_activation" not in book_writer
