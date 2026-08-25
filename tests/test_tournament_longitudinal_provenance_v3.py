from __future__ import annotations

from dataclasses import replace

import pytest

from bridge_school_api.tournament_analyzer_v3 import (
    AnalysisFinding,
    Evidence,
    EvidenceKind,
    Observability,
    TournamentAnalysis,
)
from bridge_school_api.tournament_longitudinal_v3 import (
    build_longitudinal_provenance_receipt,
    build_longitudinal_report,
)


def _analysis(event_id: str, *, impact: float) -> TournamentAnalysis:
    finding = AnalysisFinding(
        deal_id=f"{event_id}:round-1:1",
        category="result_level_dds3",
        summary="technical repeat signal",
        evidence=(
            Evidence(
                EvidenceKind.DDS_FACT,
                "same-contract DDS3 delta",
                provenance={"engine": "DDS3", "fallback_used": False},
                confidence=1.0,
            ),
        ),
        trick_loss=1.0,
        tournament_impact=impact,
        observability=Observability.NOT_OBSERVABLE,
        repeat_key="PAIR_SAME_CONTRACT_DDS3_DELTA",
    )
    return TournamentAnalysis(
        event_id=event_id,
        findings=(finding,),
        ranked_findings=(finding,),
        category_totals={
            "result_level_dds3": {
                "trick_loss": 1.0,
                "score_loss": 0.0,
                "tournament_impact": abs(impact),
            }
        },
        student_summary=(),
        teacher_summary=(),
    )


def test_longitudinal_receipt_binds_exact_analyses_and_preserves_safety_boundaries():
    analyses = (_analysis("30041", impact=2.5), _analysis("29912", impact=1.0))
    report = build_longitudinal_report(analyses)
    receipt = build_longitudinal_provenance_receipt(analyses, report)

    assert receipt["schema"] == "tournament-longitudinal-provenance-receipt-v1"
    assert receipt["analysis_count"] == 2
    assert receipt["persistent_cluster_count"] == 1
    assert receipt["event_ids"] == ("29912", "30041")
    assert len(receipt["report_sha256"]) == 64
    assert len(receipt["receipt_id"]) == 64
    assert [row["event_id"] for row in receipt["analysis_digests"]] == ["30041", "29912"]
    assert all(len(row["sha256"]) == 64 for row in receipt["analysis_digests"])
    assert receipt["automatic_methodology_mapping_used"] is False
    assert receipt["automatic_student_error_attribution_used"] is False
    assert receipt["causal_training_effect_claimed"] is False
    assert receipt["recoverable_loss_guarantee_claimed"] is False


def test_longitudinal_receipt_changes_when_upstream_evidence_changes():
    first = _analysis("30041", impact=2.5)
    second = _analysis("29912", impact=1.0)
    report = build_longitudinal_report((first, second))
    before = build_longitudinal_provenance_receipt((first, second), report)

    changed_finding = replace(
        second.findings[0],
        evidence=(
            Evidence(
                EvidenceKind.DDS_FACT,
                "same-contract DDS3 delta; revised source binding",
                provenance={"engine": "DDS3", "fallback_used": False, "source_sha256": "a" * 64},
                confidence=1.0,
            ),
        ),
    )
    changed = replace(second, findings=(changed_finding,), ranked_findings=(changed_finding,))
    changed_report = build_longitudinal_report((first, changed))
    after = build_longitudinal_provenance_receipt((first, changed), changed_report)

    assert before["report_sha256"] == after["report_sha256"]
    assert before["analysis_digests"][1]["sha256"] != after["analysis_digests"][1]["sha256"]
    assert before["receipt_id"] != after["receipt_id"]


def test_longitudinal_receipt_rejects_stale_or_caller_modified_report():
    analyses = (_analysis("30041", impact=2.5), _analysis("29912", impact=1.0))
    stale = build_longitudinal_report((analyses[0],))
    with pytest.raises(ValueError, match="does not match"):
        build_longitudinal_provenance_receipt(analyses, stale)
