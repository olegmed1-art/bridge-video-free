from bridge_school_api.tournament_adapters_v3 import (
    TournamentAdapterError,
    normalize_structured_batch,
)
from bridge_school_api.tournament_analyzer_v3 import (
    AnalysisFinding,
    Evidence,
    EvidenceKind,
    TournamentAnalysis,
)
from bridge_school_api.tournament_longitudinal_v3 import build_longitudinal_report


def _hands():
    ranks = "23456789TJQKA"
    cards = [r + s for s in "CDHS" for r in ranks]
    return {"N": cards[0:13], "E": cards[13:26], "S": cards[26:39], "W": cards[39:52]}


def test_structured_adapter_preserves_unknown_play_record():
    batch = normalize_structured_batch(
        [{"board_number": 1, "hands": _hands(), "contract": "3NT", "score": 400}],
        event_id="30041",
        session_id="round-2",
        scoring="MP",
        source={"kind": "audited_extract"},
    )
    assert batch.deals[0].deal_id == "30041:round-2:1"
    assert batch.deals[0].play_record is None
    assert batch.deals[0].source_provenance["kind"] == "audited_extract"


def test_structured_adapter_rejects_duplicate_scoped_board():
    rows = [
        {"board_number": 1, "hands": _hands()},
        {"board_number": 1, "hands": _hands()},
    ]
    try:
        normalize_structured_batch(rows, event_id="e", session_id="s")
    except TournamentAdapterError as exc:
        assert "duplicate scoped deal identity" in str(exc)
    else:
        raise AssertionError("duplicate board must fail closed")


def _analysis(event_id: str, impact: float) -> TournamentAnalysis:
    finding = AnalysisFinding(
        deal_id=f"{event_id}:s:1",
        category="teacher-supplied-category",
        summary="x",
        evidence=(Evidence(EvidenceKind.SYSTEM_RULE, "canonical"),),
        tournament_impact=impact,
        repeat_key="RULE-EXPLICIT",
    )
    return TournamentAnalysis(
        event_id=event_id,
        findings=(finding,),
        ranked_findings=(finding,),
        category_totals={"teacher-supplied-category": {"count": 1.0, "trick_loss": 0.0, "score_loss": 0.0, "tournament_impact": abs(impact)}},
        student_summary=(),
        teacher_summary=(),
    )


def test_longitudinal_clusters_only_explicit_repeat_keys():
    report = build_longitudinal_report([_analysis("30041", 4.0), _analysis("29912", 2.5)])
    assert len(report.persistent) == 1
    cluster = report.persistent[0]
    assert cluster.repeat_key == "RULE-EXPLICIT"
    assert cluster.tournament_count == 2
    assert cluster.total_tournament_impact == 6.5
    assert cluster.recoverable_loss == 6.5
