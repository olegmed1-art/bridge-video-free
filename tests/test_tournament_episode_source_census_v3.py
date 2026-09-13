import copy

from bridge_school_api.tournament_episode_source_census_v3 import build_episode_source_census


def _source(*, published_score="90", extra_columns=None, extra_values=None):
    columns = [
        "board",
        "dealer",
        "vulnerability",
        "N",
        "E",
        "S",
        "W",
        "pair_direction",
        "status",
        "contract",
        "declarer",
        "result_delta",
        "opening_lead",
        "pair_score",
        "pair_percentage",
    ]
    values = [
        "1",
        "N",
        "None",
        "AKQJT98765432...",
        ".AKQJT98765432..",
        "..AKQJT98765432.",
        "...AKQJT98765432",
        "N-S",
        "played",
        "1NT",
        "N",
        "0",
        "HA",
        published_score,
        "50.0",
    ]
    if extra_columns:
        columns.extend(extra_columns)
        values.extend(extra_values or [""] * len(extra_columns))
    return {
        "schema": "bridge-tournament-facts-v1",
        "source": {"drive_id": "x", "sha256": "f" * 64, "size_bytes": 1, "title": "fixture"},
        "tournament": {"provider_native_key": "bridge.co.il:event:30041:round:2", "scoring": "MP"},
        "policy": {"mode": "FACTS_ONLY"},
        "columns": columns,
        "rows": ["|".join(values)],
    }


def test_census_exhausts_observable_non_dd_channels_without_guessing_missing_evidence():
    census = build_episode_source_census(_source())

    assert census["non_dd_episode_source_census_complete"] is True
    assert census["census_blockers"] == []
    assert census["structural_anomaly_board_count"] == 0
    assert census["duplicate_score_mismatch_board_count"] == 0
    assert census["actual_auction_board_count"] == 0
    assert census["actual_full_play_board_count"] == 0
    assert census["opening_lead_source_board_count"] == 1
    assert census["unavailable_evidence_not_reconstructed"] is True
    statuses = {row["channel"]: row["status"] for row in census["channels"]}
    assert statuses["ACTUAL_AUCTION"] == "UNAVAILABLE_NOT_GUESSED"
    assert statuses["FULL_PLAY_RECORD"] == "UNAVAILABLE_NOT_GUESSED"
    assert statuses["TOURNAMENT_OUTCOME_CONTEXT"] == "CONTEXT_ONLY_NO_CAUSAL_EPISODE"


def test_actual_auction_present_requires_dedicated_analysis_before_census_completion():
    census = build_episode_source_census(
        _source(extra_columns=["auction"], extra_values=["1NT P P P"])
    )
    assert census["non_dd_episode_source_census_complete"] is False
    assert census["actual_auction_board_count"] == 1
    assert "ACTUAL_AUCTION_EVIDENCE_REQUIRES_EPISODE_ANALYSIS" in census["census_blockers"]


def test_full_play_present_requires_dedicated_analysis_before_census_completion():
    census = build_episode_source_census(
        _source(extra_columns=["play_record"], extra_values=["HA S2 H2 S3"])
    )
    assert census["non_dd_episode_source_census_complete"] is False
    assert census["actual_full_play_board_count"] == 1
    assert "FULL_PLAY_EVIDENCE_REQUIRES_EPISODE_ANALYSIS" in census["census_blockers"]


def test_duplicate_score_mismatch_is_not_silently_exhausted():
    census = build_episode_source_census(_source(published_score="100"))
    assert census["non_dd_episode_source_census_complete"] is False
    assert census["duplicate_score_mismatch_board_count"] == 1
    assert "DUPLICATE_SCORE_MISMATCHES_REQUIRE_REVIEW" in census["census_blockers"]


def test_source_fingerprint_changes_with_facts():
    first = build_episode_source_census(_source())
    changed_source = copy.deepcopy(_source())
    changed_source["rows"][0] = changed_source["rows"][0].replace("50.0", "51.0")
    second = build_episode_source_census(changed_source)
    assert first["source_facts_sha256"] != second["source_facts_sha256"]
