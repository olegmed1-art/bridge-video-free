from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Sequence

from .tournament_analyzer_v3 import AnalysisFinding, TournamentDeal
from .tournament_teacher_decisions_v3 import build_pending_teacher_decision_ledger


class TeacherReviewDossierError(ValueError):
    pass


@dataclass(frozen=True)
class TeacherReviewDossier:
    queue_sha256: str
    items: tuple[Mapping[str, Any], ...]
    automatic_decisions_allowed: bool = False
    automatic_methodology_mapping_allowed: bool = False
    automatic_student_error_attribution_allowed: bool = False
    cross_event_numeric_ranking_allowed: bool = False


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _queue_items(queue_payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if queue_payload.get("schema") != "tournament-teacher-review-queue-v1":
        raise TeacherReviewDossierError("unsupported teacher review queue schema")
    for field in (
        "cross_event_numeric_ranking_allowed",
        "causal_error_attribution_allowed",
        "student_error_attribution_allowed",
    ):
        if queue_payload.get(field) is not False:
            raise TeacherReviewDossierError(f"teacher review queue boundary was weakened: {field}")
    lanes = queue_payload.get("lanes")
    if not isinstance(lanes, Sequence) or isinstance(lanes, (str, bytes)):
        raise TeacherReviewDossierError("teacher review queue lanes must be a sequence")
    out: list[Mapping[str, Any]] = []
    for lane in lanes:
        if not isinstance(lane, Mapping) or lane.get("ranking_scope") != "WITHIN_EVENT_ONLY":
            raise TeacherReviewDossierError("teacher review lane must remain within-event only")
        event_id = str(lane.get("event_id") or "")
        rows = lane.get("items")
        if not event_id or not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise TeacherReviewDossierError("malformed teacher review lane")
        for item in rows:
            if not isinstance(item, Mapping):
                raise TeacherReviewDossierError("teacher review item must be a mapping")
            if str(item.get("event_id") or "") != event_id:
                raise TeacherReviewDossierError("teacher review item event mismatch")
            if item.get("causal_link") != "NOT_ESTABLISHED":
                raise TeacherReviewDossierError("causal boundary was weakened")
            if item.get("student_error_attribution_allowed") is not False:
                raise TeacherReviewDossierError("student-error attribution boundary was weakened")
            if item.get("teacher_review_required") is not True:
                raise TeacherReviewDossierError("teacher review requirement was weakened")
            out.append(item)
    return out


def _decision_index(queue_payload: Mapping[str, Any], ledger_payload: Mapping[str, Any]) -> dict[tuple[str, str, str], Mapping[str, Any]]:
    if ledger_payload.get("schema") != "tournament-teacher-decision-ledger-v1":
        raise TeacherReviewDossierError("unsupported teacher decision ledger schema")
    for field in (
        "automatic_decisions_allowed",
        "automatic_methodology_mapping_allowed",
        "automatic_student_error_attribution_allowed",
    ):
        if ledger_payload.get(field) is not False:
            raise TeacherReviewDossierError(f"teacher decision boundary was weakened: {field}")

    expected = build_pending_teacher_decision_ledger(queue_payload)
    if str(ledger_payload.get("queue_sha256") or "") != expected.queue_sha256:
        raise TeacherReviewDossierError("decision ledger is not bound to this queue")
    decisions = ledger_payload.get("decisions")
    if not isinstance(decisions, Sequence) or isinstance(decisions, (str, bytes)):
        raise TeacherReviewDossierError("decision ledger decisions must be a sequence")
    if len(decisions) != len(expected.decisions):
        raise TeacherReviewDossierError("decision ledger cardinality does not match queue")

    expected_by_id = {d.review_id: d for d in expected.decisions}
    index: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for raw in decisions:
        if not isinstance(raw, Mapping):
            raise TeacherReviewDossierError("decision ledger row must be a mapping")
        review_id = str(raw.get("review_id") or "")
        expected_decision = expected_by_id.get(review_id)
        if expected_decision is None:
            raise TeacherReviewDossierError("decision ledger contains unknown review_id")
        if raw.get("status") != "PENDING" or raw.get("teacher_decision_required") is not True:
            raise TeacherReviewDossierError("dossier may contain pending teacher decisions only")
        if raw.get("automatic_methodology_mapping_allowed") is not False:
            raise TeacherReviewDossierError("automatic methodology mapping was enabled")
        if raw.get("automatic_student_error_attribution_allowed") is not False:
            raise TeacherReviewDossierError("automatic student-error attribution was enabled")
        if str(raw.get("queue_item_sha256") or "") != expected_decision.queue_item_sha256:
            raise TeacherReviewDossierError("decision row queue-item digest mismatch")
        key = (
            str(raw.get("event_id") or ""),
            str(raw.get("deal_id") or ""),
            str(raw.get("category") or ""),
        )
        expected_key = (expected_decision.event_id, expected_decision.deal_id, expected_decision.category)
        if key != expected_key or key in index:
            raise TeacherReviewDossierError("decision row identity mismatch or duplicate")
        index[key] = raw
    return index


def _deal_index(deals: Sequence[TournamentDeal]) -> dict[str, TournamentDeal]:
    index: dict[str, TournamentDeal] = {}
    for deal in deals:
        if deal.deal_id in index:
            raise TeacherReviewDossierError(f"duplicate deal identity: {deal.deal_id}")
        index[deal.deal_id] = deal
    return index


def _finding_index(findings: Sequence[AnalysisFinding]) -> dict[tuple[str, str], AnalysisFinding]:
    index: dict[tuple[str, str], AnalysisFinding] = {}
    for finding in findings:
        key = (finding.deal_id, finding.category)
        if key in index:
            raise TeacherReviewDossierError(f"ambiguous finding identity: {key}")
        index[key] = finding
    return index


def _serialize_evidence(finding: AnalysisFinding) -> list[dict[str, Any]]:
    return [
        {
            "kind": evidence.kind.value,
            "message": evidence.message,
            "provenance": dict(evidence.provenance),
            "confidence": evidence.confidence,
        }
        for evidence in finding.evidence
    ]


def build_teacher_review_dossier(
    queue_payload: Mapping[str, Any],
    ledger_payload: Mapping[str, Any],
    *,
    deals: Sequence[TournamentDeal],
    findings: Sequence[AnalysisFinding],
) -> TeacherReviewDossier:
    queue_items = _queue_items(queue_payload)
    decisions = _decision_index(queue_payload, ledger_payload)
    deal_by_id = _deal_index(deals)
    finding_by_id = _finding_index(findings)

    items: list[Mapping[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for queue_item in queue_items:
        event_id = str(queue_item["event_id"])
        deal_id = str(queue_item.get("deal_id") or "")
        category = str(queue_item.get("category") or "")
        key = (event_id, deal_id, category)
        if key in seen:
            raise TeacherReviewDossierError(f"duplicate queue review identity: {key}")
        seen.add(key)

        decision = decisions.get(key)
        deal = deal_by_id.get(deal_id)
        finding = finding_by_id.get((deal_id, category))
        if decision is None or deal is None or finding is None:
            raise TeacherReviewDossierError(f"review evidence is incomplete for {key}")
        if deal.event_id != event_id:
            raise TeacherReviewDossierError(f"deal event mismatch for {key}")
        if _sha256(queue_item) != str(decision["queue_item_sha256"]):
            raise TeacherReviewDossierError(f"queue item digest mismatch for {key}")

        queue_trick = queue_item.get("technical_trick_loss")
        finding_trick = finding.trick_loss
        if queue_trick is None and finding_trick is not None:
            raise TeacherReviewDossierError(f"queue/finding trick-loss mismatch for {key}")
        if queue_trick is not None and finding_trick is None:
            raise TeacherReviewDossierError(f"queue/finding trick-loss mismatch for {key}")
        if queue_trick is not None and float(queue_trick) != float(finding_trick):
            raise TeacherReviewDossierError(f"queue/finding trick-loss mismatch for {key}")

        items.append(
            {
                "review_id": str(decision["review_id"]),
                "event_id": event_id,
                "deal_id": deal_id,
                "category": category,
                "status": "PENDING",
                "teacher_decision_required": True,
                "automatic_methodology_mapping_allowed": False,
                "automatic_student_error_attribution_allowed": False,
                "causal_link": "NOT_ESTABLISHED",
                "queue_context": {
                    "outcome_scale": queue_item.get("outcome_scale"),
                    "observed_outcome": queue_item.get("observed_outcome"),
                    "adverse_outcome_magnitude": queue_item.get("adverse_outcome_magnitude"),
                    "technical_trick_loss": queue_item.get("technical_trick_loss"),
                },
                "deal_facts": {
                    "dealer": deal.dealer,
                    "vulnerability": deal.vulnerability,
                    "hands": {seat: list(deal.hands[seat]) for seat in ("N", "E", "S", "W")},
                    "auction": list(deal.auction) if deal.auction is not None else None,
                    "contract": deal.contract,
                    "declarer": deal.declarer,
                    "opening_lead": deal.opening_lead,
                    "score": deal.score,
                    "play_record": list(deal.play_record) if deal.play_record is not None else None,
                    "source_provenance": dict(deal.source_provenance),
                },
                "technical_finding": {
                    "summary": finding.summary,
                    "trick_loss": finding.trick_loss,
                    "score_loss": finding.score_loss,
                    "tournament_impact": finding.tournament_impact,
                    "observability": finding.observability.value,
                    "repeat_key": finding.repeat_key,
                    "evidence": _serialize_evidence(finding),
                },
                "methodology_mapping": None,
                "student_error_attribution": None,
            }
        )

    if len(items) != len(decisions):
        raise TeacherReviewDossierError("not every pending decision received an evidence dossier item")
    return TeacherReviewDossier(queue_sha256=str(ledger_payload["queue_sha256"]), items=tuple(items))


def serialize_teacher_review_dossier(dossier: TeacherReviewDossier) -> dict[str, Any]:
    return {
        "schema": "tournament-teacher-review-dossier-v1",
        "queue_sha256": dossier.queue_sha256,
        "automatic_decisions_allowed": dossier.automatic_decisions_allowed,
        "automatic_methodology_mapping_allowed": dossier.automatic_methodology_mapping_allowed,
        "automatic_student_error_attribution_allowed": dossier.automatic_student_error_attribution_allowed,
        "cross_event_numeric_ranking_allowed": dossier.cross_event_numeric_ranking_allowed,
        "items": list(dossier.items),
        "interpretation": (
            "Evidence package for explicit teacher review only. It binds each pending review receipt to exact deal facts, "
            "observed tournament outcome context, and technical DDS evidence. It does not establish causality, a student "
            "error, a teaching category, or a methodology mapping."
        ),
    }
