import copy

import pytest

from bridge_school_api.tournament_analyzer_v3 import (
    AnalysisFinding,
    Evidence,
    EvidenceKind,
    Observability,
    TournamentDeal,
)
from bridge_school_api.tournament_teacher_decisions_v3 import (
    build_pending_teacher_decision_ledger,
    serialize_teacher_decision_ledger,
)
from bridge_school_api.tournament_teacher_review_dossier_v3 import (
    TeacherReviewDossierError,
    build_teacher_review_dossier,
    serialize_teacher_review_dossier,
)


def _hands():
    ranks = "23456789TJQKA"
    return {
        "N": tuple(rank + "S" for rank in ranks),
        "E": tuple(rank + "H" for rank in ranks),
        "S": tuple(rank + "D" for rank in ranks),
        "W": tuple(rank + "C" for rank in ranks),
    }


def _queue():
    item = {
        "event_id": "30041",
        "deal_id": "30041:round-2:19",
        "category": "dds3_pair_same_contract_delta",
        "technical_trick_loss": 2.0,
        "outcome_scale": "MP_PERCENTAGE",
        "observed_outcome": 6.0,
        "adverse_outcome_magnitude": 44.0,
        "causal_link": "NOT_ESTABLISHED",
        "student_error_attribution_allowed": False,
        "teacher_review_required": True,
    }
    return {
        "schema": "tournament-teacher-review-queue-v1",
        "lanes": [
            {
                "event_id": "30041",
                "outcome_scale": "MP_PERCENTAGE",
                "ranking_scope": "WITHIN_EVENT_ONLY",
                "items": [item],
            }
        ],
        "cross_event_numeric_ranking_allowed": False,
        "causal_error_attribution_allowed": False,
        "student_error_attribution_allowed": False,
        "interpretation": "review only",
    }


def _deal():
    return TournamentDeal(
        event_id="30041",
        session_id="round-2",
        board_number=19,
        hands=_hands(),
        dealer="S",
        vulnerability="EW",
        contract="3H",
        declarer="N",
        opening_lead="AC",
        score=-140,
        play_record=None,
        source_provenance={"pair_percentage": 6.0, "slide": 21},
    )


def _finding():
    return AnalysisFinding(
        deal_id="30041:round-2:19",
        category="dds3_pair_same_contract_delta",
        summary="Result-level DDS3 technical difference.",
        evidence=(
            Evidence(
                EvidenceKind.DDS_FACT,
                "DDS3 same-contract comparison",
                provenance={"event": 30041, "round": 2, "board": 19, "delta": -2.0},
                confidence=1.0,
            ),
        ),
        trick_loss=2.0,
        observability=Observability.NOT_OBSERVABLE,
        repeat_key="DDS3_PAIR_SAME_CONTRACT_DELTA_V1",
    )


def _ledger(queue):
    return serialize_teacher_decision_ledger(build_pending_teacher_decision_ledger(queue))


def test_dossier_binds_pending_receipt_to_exact_facts_and_dds_evidence():
    queue = _queue()
    payload = serialize_teacher_review_dossier(
        build_teacher_review_dossier(queue, _ledger(queue), deals=[_deal()], findings=[_finding()])
    )
    assert payload["schema"] == "tournament-teacher-review-dossier-v1"
    assert payload["automatic_decisions_allowed"] is False
    assert payload["automatic_methodology_mapping_allowed"] is False
    assert payload["automatic_student_error_attribution_allowed"] is False
    assert payload["cross_event_numeric_ranking_allowed"] is False
    assert len(payload["items"]) == 1
    item = payload["items"][0]
    assert item["status"] == "PENDING"
    assert item["causal_link"] == "NOT_ESTABLISHED"
    assert item["methodology_mapping"] is None
    assert item["student_error_attribution"] is None
    assert item["deal_facts"]["hands"]["N"][0] == "2S"
    assert item["deal_facts"]["source_provenance"]["pair_percentage"] == 6.0
    assert item["technical_finding"]["evidence"][0]["kind"] == "DDS_FACT"
    assert len(item["review_id"]) == 64


def test_dossier_rejects_queue_changed_after_ledger_was_created():
    queue = _queue()
    ledger = _ledger(queue)
    changed = copy.deepcopy(queue)
    changed["lanes"][0]["items"][0]["observed_outcome"] = 7.0
    with pytest.raises(TeacherReviewDossierError, match="not bound to this queue"):
        build_teacher_review_dossier(changed, ledger, deals=[_deal()], findings=[_finding()])


def test_dossier_rejects_non_pending_decision():
    queue = _queue()
    ledger = _ledger(queue)
    ledger["decisions"][0]["status"] = "CONFIRMED_TECHNICAL_RELEVANCE"
    ledger["decisions"][0]["teacher_decision_required"] = False
    with pytest.raises(TeacherReviewDossierError, match="pending teacher decisions only"):
        build_teacher_review_dossier(queue, ledger, deals=[_deal()], findings=[_finding()])


def test_dossier_rejects_missing_exact_finding():
    queue = _queue()
    with pytest.raises(TeacherReviewDossierError, match="review evidence is incomplete"):
        build_teacher_review_dossier(queue, _ledger(queue), deals=[_deal()], findings=[])


def test_dossier_rejects_queue_finding_metric_drift():
    queue = _queue()
    drift = AnalysisFinding(
        **{**_finding().__dict__, "trick_loss": 1.0}
    )
    with pytest.raises(TeacherReviewDossierError, match="trick-loss mismatch"):
        build_teacher_review_dossier(queue, _ledger(queue), deals=[_deal()], findings=[drift])


def test_dossier_rejects_automatic_methodology_permission():
    queue = _queue()
    ledger = _ledger(queue)
    ledger["automatic_methodology_mapping_allowed"] = True
    with pytest.raises(TeacherReviewDossierError, match="boundary was weakened"):
        build_teacher_review_dossier(queue, ledger, deals=[_deal()], findings=[_finding()])
