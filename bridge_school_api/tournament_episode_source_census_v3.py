from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from .tournament_duplicate_scoring_v3 import validate_tournament_fact_scores
from .tournament_structural_validation_v3 import validate_tournament_structure


class TournamentEpisodeSourceCensusError(ValueError):
    pass


def source_facts_sha256(source: Mapping[str, Any]) -> str:
    raw = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _rows(source: Mapping[str, Any]) -> tuple[list[str], list[dict[str, str]]]:
    if source.get("schema") != "bridge-tournament-facts-v1":
        raise TournamentEpisodeSourceCensusError("unsupported tournament facts schema")
    columns = source.get("columns")
    raw_rows = source.get("rows")
    if not isinstance(columns, Sequence) or isinstance(columns, (str, bytes)):
        raise TournamentEpisodeSourceCensusError("facts columns must be a sequence")
    if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, (str, bytes)):
        raise TournamentEpisodeSourceCensusError("facts rows must be a sequence")
    names = [str(value) for value in columns]
    if len(names) != len(set(names)):
        raise TournamentEpisodeSourceCensusError("facts columns must be unique")
    out: list[dict[str, str]] = []
    for raw in raw_rows:
        if not isinstance(raw, str):
            raise TournamentEpisodeSourceCensusError("facts row must be pipe-delimited text")
        values = raw.split("|")
        if len(values) != len(names):
            raise TournamentEpisodeSourceCensusError("facts row width does not match columns")
        out.append(dict(zip(names, values, strict=True)))
    return names, out


def _present(row: Mapping[str, str], field: str) -> bool:
    return bool(str(row.get(field, "")).strip())


def build_episode_source_census(source: Mapping[str, Any]) -> dict[str, Any]:
    """Account for observable non-DDS technical episode sources without inventing pedagogy.

    The census answers a narrow question: have the non-DDS evidence channels present in
    the normalized tournament facts been exhausted or explicitly marked unavailable?
    Structural and duplicate-score anomalies are independently checked. Missing actual
    auction/full-play data are recorded as source limitations, not reconstructed. If
    such richer evidence *is* present, this census stays incomplete until a dedicated
    analyzer consumes it.
    """
    names, rows = _rows(source)
    if not rows:
        raise TournamentEpisodeSourceCensusError("tournament facts contain no rows")
    tournament = source.get("tournament")
    if not isinstance(tournament, Mapping):
        raise TournamentEpisodeSourceCensusError("tournament metadata is required")
    provider_key = str(tournament.get("provider_native_key") or "").strip()
    if not provider_key:
        raise TournamentEpisodeSourceCensusError("provider_native_key is required")

    structure = validate_tournament_structure(source)
    scoring = validate_tournament_fact_scores(source)
    played = [row for row in rows if str(row.get("status") or "").strip().lower() == "played"]

    actual_auction_boards = 0
    if "auction" in names:
        actual_auction_boards = sum(_present(row, "auction") for row in played)
    actual_full_play_boards = 0
    if "play_record" in names:
        actual_full_play_boards = sum(_present(row, "play_record") for row in played)

    percentage_context_boards = sum(_present(row, "pair_percentage") for row in rows)
    opening_lead_boards = sum(_present(row, "opening_lead") for row in played)
    structural_anomaly_boards = [
        int(item["board_number"])
        for item in structure.get("checks", [])
        if isinstance(item, Mapping) and not item.get("passes")
    ]
    score_mismatch_boards = [
        int(item["board_number"])
        for item in scoring.get("mismatches", [])
        if isinstance(item, Mapping) and item.get("board_number") is not None
    ]

    channels = [
        {
            "channel": "SOURCE_STRUCTURE",
            "status": "CHECKED_NO_ANOMALY" if not structural_anomaly_boards else "ANOMALY_REQUIRES_REVIEW",
            "observed_board_count": len(rows),
            "candidate_board_numbers": structural_anomaly_boards,
            "automatic_pedagogical_attribution": False,
        },
        {
            "channel": "DUPLICATE_CONTRACT_SCORE",
            "status": "CHECKED_NO_MISMATCH" if not score_mismatch_boards else "MISMATCH_REQUIRES_REVIEW",
            "observed_board_count": int(scoring.get("played_scores_checked") or 0),
            "candidate_board_numbers": score_mismatch_boards,
            "automatic_pedagogical_attribution": False,
        },
        {
            "channel": "ACTUAL_AUCTION",
            "status": "UNAVAILABLE_NOT_GUESSED" if actual_auction_boards == 0 else "AVAILABLE_REQUIRES_DEDICATED_ANALYSIS",
            "observed_board_count": actual_auction_boards,
            "candidate_board_numbers": [],
            "automatic_pedagogical_attribution": False,
        },
        {
            "channel": "FULL_PLAY_RECORD",
            "status": "UNAVAILABLE_NOT_GUESSED" if actual_full_play_boards == 0 else "AVAILABLE_REQUIRES_DEDICATED_ANALYSIS",
            "observed_board_count": actual_full_play_boards,
            "candidate_board_numbers": [],
            "automatic_pedagogical_attribution": False,
        },
        {
            "channel": "OPENING_LEAD_SOURCE_FACT",
            "status": "SOURCE_FACT_ACCOUNTED_DDS_ANALYSIS_SEPARATE",
            "observed_board_count": opening_lead_boards,
            "candidate_board_numbers": [],
            "automatic_pedagogical_attribution": False,
        },
        {
            "channel": "TOURNAMENT_OUTCOME_CONTEXT",
            "status": "CONTEXT_ONLY_NO_CAUSAL_EPISODE",
            "observed_board_count": percentage_context_boards,
            "candidate_board_numbers": [],
            "automatic_pedagogical_attribution": False,
        },
    ]

    blockers: list[str] = []
    if not structure.get("all_structural_checks_pass"):
        blockers.append("STRUCTURAL_ANOMALIES_REQUIRE_REVIEW")
    if not scoring.get("all_published_scores_match"):
        blockers.append("DUPLICATE_SCORE_MISMATCHES_REQUIRE_REVIEW")
    if actual_auction_boards:
        blockers.append("ACTUAL_AUCTION_EVIDENCE_REQUIRES_EPISODE_ANALYSIS")
    if actual_full_play_boards:
        blockers.append("FULL_PLAY_EVIDENCE_REQUIRES_EPISODE_ANALYSIS")

    return {
        "schema": "tournament-episode-source-census-v1",
        "normative_algorithm_version": "1.4",
        "source_facts_sha256": source_facts_sha256(source),
        "provider_native_key": provider_key,
        "board_count": len(rows),
        "played_board_count": len(played),
        "channels": channels,
        "structural_anomaly_board_count": len(structural_anomaly_boards),
        "duplicate_score_mismatch_board_count": len(score_mismatch_boards),
        "actual_auction_board_count": actual_auction_boards,
        "actual_full_play_board_count": actual_full_play_boards,
        "opening_lead_source_board_count": opening_lead_boards,
        "outcome_context_board_count": percentage_context_boards,
        "non_dd_episode_source_census_complete": not blockers,
        "census_blockers": blockers,
        "unavailable_evidence_not_reconstructed": True,
        "automatic_episode_creation_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "interpretation": (
            "A complete census means every non-DDS source channel currently present in the normalized facts is either "
            "independently checked with no anomaly or explicitly unavailable and not guessed. It does not assert that "
            "missing auction/play evidence was analyzed, and it does not create a teaching episode."
        ),
    }
