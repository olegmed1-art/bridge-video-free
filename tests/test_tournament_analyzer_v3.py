from __future__ import annotations

import pytest

from bridge_school_api.tournament_analyzer_v3 import (
    AnalysisFinding,
    DealIntegrityError,
    Evidence,
    EvidenceKind,
    Observability,
    TournamentDeal,
    aggregate_categories,
    analysis_observability,
    analyze_tournament,
    attach_model_opinion,
    attach_system_rule,
    build_contract_baseline,
    finding_from_contract_baseline,
    validate_deal_integrity,
)


def full_hands():
    ranks = "23456789TJQKA"
    suits = "CDHS"
    cards = [r + s for s in suits for r in ranks]
    return {
        "N": cards[0:13],
        "E": cards[13:26],
        "S": cards[26:39],
        "W": cards[39:52],
    }


def make_deal(**kwargs):
    data = {
        "event_id": "30041",
        "session_id": "round-2",
        "board_number": 1,
        "hands": full_hands(),
        "play_record": None,
    }
    data.update(kwargs)
    return TournamentDeal(**data)


def test_integrity_requires_52_unique_cards_and_scoped_identity():
    deal = make_deal()
    validate_deal_integrity(deal)
    assert deal.deal_id == "30041:round-2:1"

    bad = dict(full_hands())
    bad["W"] = list(bad["W"])
    bad["W"][0] = bad["N"][0]
    with pytest.raises(DealIntegrityError, match="duplicate cards"):
        validate_deal_integrity(make_deal(hands=bad))

    with pytest.raises(DealIntegrityError, match="session_id"):
        validate_deal_integrity(make_deal(session_id=""))


def test_missing_play_record_is_not_observable_not_inferred():
    assert analysis_observability(make_deal(play_record=None)) is Observability.NOT_OBSERVABLE
    assert analysis_observability(make_deal(play_record=[])) is Observability.UNKNOWN
    assert analysis_observability(make_deal(play_record=["2C"])) is Observability.OBSERVABLE


def test_dds_baseline_is_fact_but_not_player_error_attribution():
    baseline = build_contract_baseline(
        par_score=420,
        dd_tricks_for_played_contract=10,
        played_tricks=8,
        actual_score=-100,
        comparison_score=420,
        tournament_impact=6.2,
        provenance={"engine": "DDS3", "fallback_used": False},
    )
    finding = finding_from_contract_baseline(make_deal(), baseline)
    assert finding is not None
    assert finding.trick_loss == 2
    assert finding.score_loss == 520
    assert finding.tournament_impact == 6.2
    assert finding.observability is Observability.NOT_OBSERVABLE
    assert finding.evidence[0].kind is EvidenceKind.DDS_FACT
    assert "not by itself a player-error attribution" in finding.evidence[0].message


def test_system_rule_and_model_opinion_remain_distinct_evidence_classes():
    base = AnalysisFinding(
        deal_id="30041:round-2:1",
        category="competitive_bidding",
        summary="Review auction decision",
        evidence=(Evidence(EvidenceKind.FACT, "Auction came from source"),),
    )
    with_rule = attach_system_rule(
        base,
        rule_id="RULE-L1-EXAMPLE",
        message="Canonical rule matched",
        provenance={"source": "SCHOOL_L1_DB_V1"},
    )
    final = attach_model_opinion(
        with_rule,
        message="BEN prefers an alternative bid",
        confidence=0.74,
        provenance={"engine": "BEN"},
    )
    assert [e.kind for e in final.evidence] == [
        EvidenceKind.FACT,
        EvidenceKind.SYSTEM_RULE,
        EvidenceKind.MODEL_OPINION,
    ]


def test_aggregate_and_rank_by_tournament_impact_before_raw_tricks():
    deal1 = make_deal(board_number=1)
    deal2 = make_deal(board_number=2)
    f1 = AnalysisFinding(
        deal_id=deal1.deal_id,
        category="play",
        summary="one",
        evidence=(Evidence(EvidenceKind.DDS_FACT, "dds"),),
        trick_loss=3,
        tournament_impact=1.0,
    )
    f2 = AnalysisFinding(
        deal_id=deal2.deal_id,
        category="bidding",
        summary="two",
        evidence=(Evidence(EvidenceKind.SYSTEM_RULE, "rule"),),
        trick_loss=1,
        tournament_impact=8.0,
    )
    result = analyze_tournament([deal1, deal2], [f1, f2])
    assert result.ranked_findings[0].deal_id == deal2.deal_id
    assert result.category_totals["play"]["trick_loss"] == 3
    assert result.category_totals["bidding"]["tournament_impact"] == 8


def test_duplicate_board_number_is_allowed_across_sessions_but_not_same_scope():
    round1 = make_deal(session_id="round-1", board_number=7)
    round2 = make_deal(session_id="round-2", board_number=7)
    analyze_tournament([round1, round2], [])

    duplicate = make_deal(session_id="round-1", board_number=7)
    with pytest.raises(DealIntegrityError, match="duplicate scoped deal identity"):
        analyze_tournament([round1, duplicate], [])


def test_finding_must_reference_ingested_deal():
    deal = make_deal()
    finding = AnalysisFinding(
        deal_id="30041:round-2:99",
        category="unknown",
        summary="bad reference",
        evidence=(Evidence(EvidenceKind.TEACHER_REVIEW, "review"),),
    )
    with pytest.raises(DealIntegrityError, match="unknown deals"):
        analyze_tournament([deal], [finding])


def test_aggregate_categories_is_abs_loss_not_directional_netting():
    findings = [
        AnalysisFinding("e:s:1", "x", "a", (), score_loss=100, tournament_impact=-2),
        AnalysisFinding("e:s:2", "x", "b", (), score_loss=-40, tournament_impact=3),
    ]
    totals = aggregate_categories(findings)
    assert totals["x"]["score_loss"] == 140
    assert totals["x"]["tournament_impact"] == 5
