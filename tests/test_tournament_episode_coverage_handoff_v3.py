import copy

import pytest

from bridge_school_api.tournament_episode_coverage_handoff_v3 import build_episode_coverage_handoff
from bridge_school_api.tournament_episode_scoring_authority_v3 import TournamentEpisodeScoringAuthorityError
from bridge_school_api.tournament_episode_scoring_intake_v3 import (
    TournamentEpisodeScoringIntakeError,
    build_episode_scoring_template,
)


QUEUE_SHA = "a" * 64


def _source():
    return {
        "schema": "bridge-tournament-facts-v1",
        "tournament": {"provider_native_key": "bridge.co.il:event:30041:round:2"},
        "columns": ["board", "status"],
        "rows": ["1|played", "2|unplayed"],
    }


def _candidate(candidate_id="candidate-1", review_id="review-1", board_number=1):
    return {
        "candidate_id": candidate_id,
        "review_id": review_id,
        "event_id": "30041",
        "deal_id": f"30041:round-2:{board_number}",
        "board_number": board_number,
        "category": "contract_result",
        "review_status": "PENDING_TEACHER_REVIEW",
        "coverage_eligible": False,
    }


def _inventory(candidates=None, *, release_blockers=None):
    candidates = candidates or [_candidate()]
    return {
        "schema": "tournament-evidence-episode-candidate-inventory-v1",
        "normative_algorithm_version": "1.4",
        "event_id": "30041",
        "queue_sha256": QUEUE_SHA,
        "technical_candidates": candidates,
        "evidence_candidate_inventory_complete": True,
        "automatic_episode_scoring_allowed": False,
        "automatic_transferability_judgment_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "release_blockers": list(release_blockers or []),
    }


def _decision(candidate, status="PENDING"):
    resolved = status != "PENDING"
    return {
        "review_id": candidate["review_id"],
        "event_id": candidate["event_id"],
        "deal_id": candidate["deal_id"],
        "category": candidate["category"],
        "queue_item_sha256": "b" * 64,
        "status": status,
        "teacher_decision_required": not resolved,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "decision_note": "explicit" if resolved else None,
        "decision_provenance": {"decision_source": "EXPLICIT_TEACHER_DECISION"} if resolved else None,
    }


def _ledger(candidates, statuses=None, *, queue_sha=QUEUE_SHA):
    statuses = statuses or ["PENDING"] * len(candidates)
    return {
        "schema": "tournament-teacher-decision-ledger-v1",
        "queue_sha256": queue_sha,
        "automatic_decisions_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "decisions": [_decision(candidate, status) for candidate, status in zip(candidates, statuses, strict=True)],
    }


def _score_row(row, *, impact, transferability, reliability):
    row["explicit_episode_adjudication"] = True
    row["impact_score"] = impact
    row["transferability_score"] = transferability
    row["reliability_score"] = reliability
    row["score_actor"] = "teacher:test"
    row["score_provenance"] = {"source": "explicit-test-adjudication"}
    row["status"] = "SCORED_EXPLICITLY"


def test_pending_teacher_decision_blocks_unscored_candidate():
    candidates = [_candidate()]
    inventory = _inventory(candidates)
    intake = build_episode_scoring_template(inventory)
    handoff = build_episode_coverage_handoff(_source(), inventory, intake, _ledger(candidates))

    assert handoff["candidate_count"] == 1
    assert handoff["pending_decision_count"] == 1
    assert handoff["authorized_scored_count"] == 0
    assert handoff["episode_adjudication_complete"] is False
    assert handoff["coverage_episode_count"] == 0
    assert handoff["handoff_ready"] is False
    assert handoff["teacher_decision_gate_enforced"] is True
    assert "TEACHER_DECISION_PENDING" in handoff["handoff_blockers"]
    assert "EPISODE_ADJUDICATION_NOT_COMPLETE" in handoff["handoff_blockers"]


def test_pending_teacher_decision_cannot_be_bypassed_with_explicit_score():
    candidates = [_candidate()]
    inventory = _inventory(candidates)
    intake = build_episode_scoring_template(inventory)
    _score_row(intake["rows"][0], impact=2, transferability=1, reliability=1)

    with pytest.raises(TournamentEpisodeScoringAuthorityError, match="forbidden"):
        build_episode_coverage_handoff(_source(), inventory, intake, _ledger(candidates))


def test_confirmed_and_explicit_significant_score_creates_deep_slide_plan():
    candidates = [_candidate()]
    inventory = _inventory(candidates)
    intake = build_episode_scoring_template(inventory)
    _score_row(intake["rows"][0], impact=2, transferability=1, reliability=1)

    handoff = build_episode_coverage_handoff(
        _source(), inventory, intake, _ledger(candidates, ["CONFIRMED_TECHNICAL_RELEVANCE"])
    )

    assert handoff["confirmed_decision_count"] == 1
    assert handoff["authorized_scored_count"] == 1
    assert handoff["confirmed_unscored_count"] == 0
    assert handoff["episode_adjudication_complete"] is True
    assert handoff["v1_4_episode_inventory_complete"] is True
    assert handoff["handoff_ready"] is True
    assert handoff["handoff_blockers"] == []
    manifest = handoff["coverage_manifest"]
    assert manifest["significant_episode_count"] == 1
    assert manifest["episodes"][0]["total_score"] == 4
    assert manifest["episodes"][0]["tier"] == "SIGNIFICANT_DEEP_SLIDE"
    assert manifest["episodes"][0]["required_separate_slide_key"] == "board-1-deep-1"


def test_confirmed_but_unscored_candidate_remains_blocked():
    candidates = [_candidate()]
    inventory = _inventory(candidates)
    intake = build_episode_scoring_template(inventory)
    handoff = build_episode_coverage_handoff(
        _source(), inventory, intake, _ledger(candidates, ["CONFIRMED_TECHNICAL_RELEVANCE"])
    )

    assert handoff["confirmed_unscored_count"] == 1
    assert handoff["episode_adjudication_complete"] is False
    assert handoff["handoff_ready"] is False
    assert "CONFIRMED_EPISODE_SCORING_NOT_COMPLETE" in handoff["handoff_blockers"]


def test_dismissed_candidate_resolves_without_episode_score():
    candidates = [_candidate()]
    inventory = _inventory(candidates)
    intake = build_episode_scoring_template(inventory)
    handoff = build_episode_coverage_handoff(_source(), inventory, intake, _ledger(candidates, ["DISMISSED"]))

    assert handoff["dismissed_count"] == 1
    assert handoff["authorized_scored_count"] == 0
    assert handoff["episode_adjudication_complete"] is True
    assert handoff["coverage_episode_count"] == 0
    assert handoff["handoff_ready"] is True


def test_dismissed_candidate_cannot_be_scored():
    candidates = [_candidate()]
    inventory = _inventory(candidates)
    intake = build_episode_scoring_template(inventory)
    _score_row(intake["rows"][0], impact=1, transferability=1, reliability=1)

    with pytest.raises(TournamentEpisodeScoringAuthorityError, match="forbidden"):
        build_episode_coverage_handoff(_source(), inventory, intake, _ledger(candidates, ["DISMISSED"]))


def test_needs_context_is_unresolved_and_cannot_be_scored():
    candidates = [_candidate()]
    inventory = _inventory(candidates)
    intake = build_episode_scoring_template(inventory)
    handoff = build_episode_coverage_handoff(_source(), inventory, intake, _ledger(candidates, ["NEEDS_CONTEXT"]))
    assert handoff["needs_context_count"] == 1
    assert handoff["handoff_ready"] is False
    assert "TEACHER_CONTEXT_REQUIRED" in handoff["handoff_blockers"]

    scored = build_episode_scoring_template(inventory)
    _score_row(scored["rows"][0], impact=1, transferability=1, reliability=1)
    with pytest.raises(TournamentEpisodeScoringAuthorityError, match="forbidden"):
        build_episode_coverage_handoff(_source(), inventory, scored, _ledger(candidates, ["NEEDS_CONTEXT"]))


def test_independent_upstream_inventory_gap_survives_completed_teacher_adjudication():
    candidates = [_candidate()]
    inventory = _inventory(candidates, release_blockers=["NON_DDS_EPISODE_COVERAGE_NOT_ESTABLISHED"])
    intake = build_episode_scoring_template(inventory)
    handoff = build_episode_coverage_handoff(_source(), inventory, intake, _ledger(candidates, ["DISMISSED"]))

    assert handoff["episode_adjudication_complete"] is True
    assert handoff["v1_4_episode_inventory_complete"] is False
    assert handoff["handoff_ready"] is False
    assert "NON_DDS_EPISODE_COVERAGE_NOT_ESTABLISHED" in handoff["handoff_blockers"]


def test_partial_resolution_preserves_pending_candidate():
    candidates = [_candidate("candidate-1", "review-1"), _candidate("candidate-2", "review-2")]
    inventory = _inventory(candidates)
    intake = build_episode_scoring_template(inventory)
    _score_row(intake["rows"][0], impact=2, transferability=2, reliability=2)

    handoff = build_episode_coverage_handoff(
        _source(), inventory, intake, _ledger(candidates, ["CONFIRMED_TECHNICAL_RELEVANCE", "PENDING"])
    )

    assert handoff["confirmed_decision_count"] == 1
    assert handoff["authorized_scored_count"] == 1
    assert handoff["pending_decision_count"] == 1
    assert handoff["coverage_episode_count"] == 1
    assert handoff["handoff_ready"] is False


def test_ledger_queue_or_identity_mismatch_fails_closed():
    candidates = [_candidate()]
    inventory = _inventory(candidates)
    intake = build_episode_scoring_template(inventory)

    with pytest.raises(TournamentEpisodeScoringAuthorityError, match="not bound"):
        build_episode_coverage_handoff(_source(), inventory, intake, _ledger(candidates, queue_sha="c" * 64))

    bad = _ledger(candidates)
    bad["decisions"][0]["deal_id"] = "30041:round-2:99"
    with pytest.raises(TournamentEpisodeScoringAuthorityError, match="identity mismatch"):
        build_episode_coverage_handoff(_source(), inventory, intake, bad)


def test_pending_scoring_row_cannot_smuggle_score():
    candidates = [_candidate()]
    inventory = _inventory(candidates)
    intake = build_episode_scoring_template(inventory)
    tampered = copy.deepcopy(intake)
    tampered["rows"][0]["impact_score"] = 2

    with pytest.raises(TournamentEpisodeScoringIntakeError):
        build_episode_coverage_handoff(_source(), inventory, tampered, _ledger(candidates))
