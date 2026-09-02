import copy
import json
from pathlib import Path

import pytest

from bridge_school_api.tournament_analyzer_v3 import (
    AnalysisFinding,
    Evidence,
    EvidenceKind,
    Observability,
    TournamentAnalysis,
)
from bridge_school_api.tournament_scoring_context_v3 import (
    TournamentScoringContextError,
    build_30041_mp_context,
    join_findings_with_mp_context,
    serialize_mp_context,
)


SOURCE = Path("data/tournaments/tournament_30041_round2_diana_facts_v1.json")


def _source():
    return json.loads(SOURCE.read_text(encoding="utf-8"))


def _analysis(*findings):
    return TournamentAnalysis(
        event_id="30041",
        findings=tuple(findings),
        ranked_findings=tuple(findings),
        category_totals={},
        student_summary=(),
        teacher_summary=(),
    )


def _finding(board, trick_loss):
    return AnalysisFinding(
        deal_id=f"30041:round-2:{board}",
        category="contract_result",
        summary="technical DDS3 observation",
        evidence=(Evidence(EvidenceKind.DDS_FACT, "dds"),),
        trick_loss=trick_loss,
        observability=Observability.NOT_OBSERVABLE,
        repeat_key="DDS3_PAIR_SAME_CONTRACT_DELTA_V1",
    )


def test_real_30041_mp_context_is_exact_and_arithmetic_only():
    context = build_30041_mp_context(_source())
    assert context.event_id == "30041"
    assert context.session_id == "round-2"
    assert context.final_percentage == 54.57
    assert context.rank == 8
    assert context.field_size == 23
    assert context.counted_results == 22
    assert len(context.outcomes) == 22
    assert context.total_below_neutral_mass == pytest.approx(247.5)
    assert context.counterfactual_final_percentage_if_all_below_neutral_were_neutral == pytest.approx(65.82)

    by_board = {item.board_number: item for item in context.outcomes}
    assert by_board[15].observed_pair_percentage == 0.5
    assert by_board[15].gap_to_neutral == 49.5
    assert by_board[15].final_percentage_uplift_if_neutral == pytest.approx(2.25)
    assert by_board[15].counterfactual_final_percentage_if_neutral == pytest.approx(56.82)
    assert by_board[9].observed_pair_percentage == 99.5
    assert by_board[9].gap_to_neutral == 0.0
    assert "not DDS3-to-MP conversions" in context.interpretation


def test_join_keeps_dd_and_mp_evidence_separate():
    context = build_30041_mp_context(_source())
    analysis = _analysis(_finding(15, 1.0), _finding(9, 2.0))
    joined = join_findings_with_mp_context(analysis, context)
    assert joined[0].deal_id == "30041:round-2:15"
    assert joined[0].observed_pair_percentage == 0.5
    assert joined[0].final_percentage_uplift_if_neutral == pytest.approx(2.25)
    assert joined[0].causal_link == "NOT_ESTABLISHED"
    assert joined[0].dd_to_mp_conversion_available is False

    payload = serialize_mp_context(context, joined)
    assert payload["schema"] == "tournament-mp-outcome-context-v1"
    assert payload["dd_to_mp_conversion_available"] is False
    assert payload["causal_error_attribution_allowed"] is False
    assert payload["pedagogically_recoverable_result_estimated"] is False


def test_scoring_context_rejects_policy_or_count_drift():
    wrong_scoring = copy.deepcopy(_source())
    wrong_scoring["tournament"]["scoring"] = "IMP"
    with pytest.raises(TournamentScoringContextError, match="must remain MP"):
        build_30041_mp_context(wrong_scoring)

    wrong_count = copy.deepcopy(_source())
    wrong_count["tournament"]["counted_results"] = 21
    with pytest.raises(TournamentScoringContextError, match="counted percentage rows"):
        build_30041_mp_context(wrong_count)


def test_scoring_context_rejects_boolean_numeric_metadata():
    source = copy.deepcopy(_source())
    source["tournament"]["rank"] = True
    with pytest.raises(TournamentScoringContextError, match="rank must be an integer"):
        build_30041_mp_context(source)
