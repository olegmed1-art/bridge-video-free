from __future__ import annotations

from typing import Any, Mapping

from .tournament_duplicate_scoring_v3 import validate_tournament_fact_scores
from .tournament_input_manifest_v3 import build_input_manifest
from .tournament_structural_validation_v3 import validate_tournament_structure


def build_preanalysis_gate(
    source: Mapping[str, Any],
    *,
    normalized_facts_sha256: str,
    normalized_facts_size_bytes: int,
    normalized_facts_received_at: str,
    normalized_facts_commit: str,
    algorithm_revision_id: str,
) -> dict[str, Any]:
    """Combine independent source, structure and duplicate-score gates.

    This gate determines which *technical* analyses are supported by the current
    evidence. It does not create a bridge-methodology conclusion or student error.
    """
    manifest = build_input_manifest(
        source,
        normalized_facts_sha256=normalized_facts_sha256,
        normalized_facts_size_bytes=normalized_facts_size_bytes,
        normalized_facts_received_at=normalized_facts_received_at,
        normalized_facts_commit=normalized_facts_commit,
        algorithm_revision_id=algorithm_revision_id,
    )
    structure = validate_tournament_structure(source)
    scoring = validate_tournament_fact_scores(source)

    hard_stops: list[str] = []
    if not manifest["source_conflict_gate_pass"]:
        hard_stops.append("SOURCE_CONFLICT")
    if not structure["all_structural_checks_pass"]:
        hard_stops.append("STRUCTURAL_VALIDATION_FAILED")
    if not scoring["all_published_scores_match"]:
        hard_stops.append("DUPLICATE_SCORE_MISMATCH")

    records = manifest["records"]
    played = [record for record in records if record["status"] == "played"]
    actual_auction_boards = sum(record["auction_status"] == "actual" for record in played)
    actual_full_play_boards = sum(record["play_status"] == "actual" for record in played)
    partial_play_boards = sum(record["play_status"] == "partial" for record in played)

    facts_only_ready = not hard_stops
    all_played_have_actual_auction = bool(played) and actual_auction_boards == len(played)
    all_played_have_full_play = bool(played) and actual_full_play_boards == len(played)
    full_causal_replay_ready = facts_only_ready and all_played_have_actual_auction and all_played_have_full_play

    limitations = list(manifest["provenance_limitations"])
    if actual_auction_boards < len(played):
        limitations.append("ACTUAL_AUCTION_ABSENT_FOR_SOME_OR_ALL_PLAYED_BOARDS")
    if actual_full_play_boards < len(played):
        limitations.append("FULL_PLAY_RECORD_ABSENT_FOR_SOME_OR_ALL_PLAYED_BOARDS")

    allowed_analyses: list[str] = []
    if facts_only_ready:
        allowed_analyses.extend(
            [
                "CONTRACT_LEVEL_DD_OPPORTUNITY",
                "OPENING_LEAD_DD_WHERE_LEAD_IS_ACTUAL",
                "DUPLICATE_SCORE_VALIDATION",
                "TOURNAMENT_OUTCOME_CONTEXT",
                "EVIDENCE_BOUND_TEACHER_REVIEW",
            ]
        )
    if actual_auction_boards:
        allowed_analyses.append("AUCTION_ANALYSIS_ONLY_ON_BOARDS_WITH_ACTUAL_AUCTION")
    if actual_full_play_boards:
        allowed_analyses.append("CARD_BY_CARD_ANALYSIS_ONLY_ON_BOARDS_WITH_ACTUAL_FULL_PLAY")

    blocked_attributions = ["AUTOMATIC_STUDENT_ERROR_ATTRIBUTION"]
    if not all_played_have_actual_auction:
        blocked_attributions.append("BIDDING_DECISION_ATTRIBUTION_WITHOUT_ACTUAL_AUCTION")
    if not all_played_have_full_play:
        blocked_attributions.append("LATER_CARD_ATTRIBUTION_WITHOUT_FULL_PLAY_RECORD")

    return {
        "schema": "tournament-preanalysis-gate-v1",
        "run_id": manifest["run_id"],
        "normative_boundary": manifest["normative_boundary"],
        "tournament": manifest["tournament"],
        "facts_only_analysis_ready": facts_only_ready,
        "full_causal_replay_ready": full_causal_replay_ready,
        "hard_stop_conditions": hard_stops,
        "evidence_availability": {
            "played_boards": len(played),
            "actual_auction_boards": actual_auction_boards,
            "actual_full_play_boards": actual_full_play_boards,
            "partial_play_boards": partial_play_boards,
            "opening_leads_checked": structure["opening_leads_checked"],
            "opening_leads_legal": structure["opening_leads_legal"],
        },
        "allowed_analyses": allowed_analyses,
        "blocked_attributions": blocked_attributions,
        "limitations": sorted(set(limitations)),
        "gates": {
            "source_conflict_gate_pass": manifest["source_conflict_gate_pass"],
            "coverage_complete": manifest["coverage_complete"] and structure["coverage"]["complete"],
            "immediate_field_locators_complete": manifest["immediate_field_locators_complete"],
            "structure_pass": structure["all_structural_checks_pass"],
            "dealer_cycle_pass": structure["dealer_cycle_pass"],
            "vulnerability_cycle_pass": structure["vulnerability_cycle_pass"],
            "hands_13x4_pass": structure["hands_13x4_pass"],
            "cards_52_unique_pass": structure["cards_52_unique_pass"],
            "opening_lead_gate_pass": structure["opening_leads_checked"] == structure["opening_leads_legal"],
            "status_consistency_pass": structure["status_consistency_pass"],
            "duplicate_score_gate_pass": scoring["all_published_scores_match"],
            "played_scores_checked": scoring["played_scores_checked"],
            "administrative_rows_not_recalculated": scoring["administrative_results_recalculated"] is False,
        },
        "input_manifest": manifest,
        "structural_validation": structure,
        "duplicate_score_validation": scoring,
    }
