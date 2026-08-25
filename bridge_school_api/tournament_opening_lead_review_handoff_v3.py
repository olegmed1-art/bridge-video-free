from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping, Sequence

from .tournament_analyzer_v3 import AnalysisFinding, Evidence, EvidenceKind, Observability
from .tournament_episode_inventory_v3 import build_evidence_episode_candidate_inventory
from .tournament_real_sources_v3 import EXPECTED_30041_PROVIDER_KEY, normalize_30041_facts
from .tournament_teacher_decisions_v3 import (
    build_pending_teacher_decision_ledger,
    serialize_teacher_decision_ledger,
)
from .tournament_teacher_review_dossier_v3 import (
    build_teacher_review_dossier,
    serialize_teacher_review_dossier,
)
from .tournament_teacher_review_queue_v3 import (
    CrossEventTeacherReviewQueue,
    TeacherReviewItem,
    TeacherReviewLane,
    serialize_teacher_review_queue,
)


class OpeningLeadReviewHandoffError(ValueError):
    pass


CATEGORY = "opening_lead_dds3"
REPEAT_KEY = "DDS3_OPENING_LEAD_REGRET_V1"


def _source_pair_percentages(source: Mapping[str, Any]) -> dict[int, float]:
    columns = source.get("columns")
    rows = source.get("rows")
    if not isinstance(columns, Sequence) or isinstance(columns, (str, bytes)):
        raise OpeningLeadReviewHandoffError("source columns must be a sequence")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise OpeningLeadReviewHandoffError("source rows must be a sequence")
    names = [str(value) for value in columns]
    required = {"board", "status", "pair_percentage"}
    if not required.issubset(names) or len(names) != len(set(names)):
        raise OpeningLeadReviewHandoffError("source scoring columns are malformed")

    out: dict[int, float] = {}
    for raw in rows:
        if not isinstance(raw, str):
            raise OpeningLeadReviewHandoffError("source rows must be pipe-delimited strings")
        values = raw.split("|")
        if len(values) != len(names):
            raise OpeningLeadReviewHandoffError("source row width mismatch")
        row = dict(zip(names, values, strict=True))
        if str(row["status"]).strip().lower() != "played":
            continue
        try:
            board = int(row["board"])
            percentage = float(row["pair_percentage"])
        except (TypeError, ValueError) as exc:
            raise OpeningLeadReviewHandoffError("played source row lacks numeric board percentage") from exc
        if board <= 0 or board in out or not 0.0 <= percentage <= 100.0:
            raise OpeningLeadReviewHandoffError("invalid or duplicate played-board percentage")
        out[board] = percentage
    return out


def _validate_report(report: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    if report.get("schema") != "tournament-opening-lead-dds3-v1":
        raise OpeningLeadReviewHandoffError("unsupported opening-lead report schema")
    if str(report.get("provider_native_key") or "") != EXPECTED_30041_PROVIDER_KEY:
        raise OpeningLeadReviewHandoffError("opening-lead report provider identity mismatch")
    if str(report.get("event_id") or "") != "30041" or str(report.get("session_id") or "") != "round-2":
        raise OpeningLeadReviewHandoffError("opening-lead report event/session mismatch")
    if report.get("engine") != "DDS3" or report.get("fallback_used") is not False:
        raise OpeningLeadReviewHandoffError("non-canonical DDS3 report rejected")

    candidates = report.get("teacher_review_candidates")
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
        raise OpeningLeadReviewHandoffError("teacher_review_candidates must be a sequence")
    expected_count = report.get("target_pair_positive_regret_candidates")
    if isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count != len(candidates):
        raise OpeningLeadReviewHandoffError("candidate count mismatch")

    seen: set[str] = set()
    for raw in candidates:
        if not isinstance(raw, Mapping):
            raise OpeningLeadReviewHandoffError("opening-lead candidate must be a mapping")
        deal_id = str(raw.get("deal_id") or "")
        if not deal_id.startswith("30041:round-2:") or deal_id in seen:
            raise OpeningLeadReviewHandoffError("candidate identity mismatch or duplicate")
        seen.add(deal_id)
        if raw.get("target_pair_made_opening_lead") is not True:
            raise OpeningLeadReviewHandoffError("non-target opening lead entered review handoff")
        regret = raw.get("regret_tricks")
        if isinstance(regret, bool) or not isinstance(regret, (int, float)) or float(regret) <= 0:
            raise OpeningLeadReviewHandoffError("review candidate must have positive DDS3 regret")
        if raw.get("engine") != "DDS3" or raw.get("fallback_used") is not False:
            raise OpeningLeadReviewHandoffError("candidate DDS3 provenance boundary failed")
        if raw.get("causal_error_attribution") != "NOT_ESTABLISHED":
            raise OpeningLeadReviewHandoffError("candidate causal boundary was weakened")
        if raw.get("student_error_attribution") is not None or raw.get("methodology_mapping") is not None:
            raise OpeningLeadReviewHandoffError("candidate contains forbidden pedagogical attribution")
        if raw.get("teacher_review_required") is not True or raw.get("coverage_eligible") is not False:
            raise OpeningLeadReviewHandoffError("candidate review/coverage boundary was weakened")
    return candidates


def findings_from_opening_lead_report(report: Mapping[str, Any]) -> tuple[AnalysisFinding, ...]:
    candidates = _validate_report(report)
    findings: list[AnalysisFinding] = []
    for raw in candidates:
        regret = float(raw["regret_tricks"])
        provenance = {
            "operation": "position_all_moves",
            "engine": raw.get("engine"),
            "engine_version": raw.get("engine_version"),
            "fallback_used": raw.get("fallback_used"),
            "position_sha256": raw.get("position_sha256"),
            "actual_lead": raw.get("actual_lead"),
            "optimal_leads": list(raw.get("optimal_leads") or []),
            "best_tricks_for_side_to_lead": raw.get("best_tricks_for_side_to_lead"),
            "actual_lead_tricks_for_side_to_lead": raw.get("actual_lead_tricks_for_side_to_lead"),
            "regret_tricks": regret,
        }
        findings.append(
            AnalysisFinding(
                deal_id=str(raw["deal_id"]),
                category=CATEGORY,
                summary=(
                    "Фактический первый ход уступает лучшему double-dummy первому ходу в этой позиции; "
                    "причина и педагогическая интерпретация не установлены."
                ),
                evidence=(
                    Evidence(
                        EvidenceKind.DDS_FACT,
                        "DDS3 opening-lead regret for the observed first card; not a student-error attribution.",
                        provenance=provenance,
                        confidence=1.0,
                    ),
                ),
                trick_loss=regret,
                score_loss=None,
                tournament_impact=None,
                observability=Observability.OBSERVABLE,
                repeat_key=REPEAT_KEY,
            )
        )
    return tuple(findings)


def build_opening_lead_review_queue(
    source: Mapping[str, Any], report: Mapping[str, Any]
) -> tuple[dict[str, Any], tuple[AnalysisFinding, ...]]:
    batch = normalize_30041_facts(source)
    if batch.event_id != "30041" or batch.session_id != "round-2":
        raise OpeningLeadReviewHandoffError("unexpected normalized source identity")
    percentages = _source_pair_percentages(source)
    findings = findings_from_opening_lead_report(report)

    items: list[TeacherReviewItem] = []
    for finding in findings:
        try:
            board = int(finding.deal_id.rsplit(":", 1)[1])
        except (IndexError, ValueError) as exc:
            raise OpeningLeadReviewHandoffError("candidate deal_id lacks board number") from exc
        if board not in percentages:
            raise OpeningLeadReviewHandoffError("opening-lead candidate has no official played-board percentage")
        pct = percentages[board]
        items.append(
            TeacherReviewItem(
                event_id="30041",
                deal_id=finding.deal_id,
                category=CATEGORY,
                technical_trick_loss=finding.trick_loss,
                outcome_scale="MP_PERCENTAGE",
                observed_outcome=pct,
                adverse_outcome_magnitude=max(0.0, 50.0 - pct),
                causal_link="NOT_ESTABLISHED",
                student_error_attribution_allowed=False,
                teacher_review_required=True,
            )
        )
    items.sort(
        key=lambda item: (
            item.adverse_outcome_magnitude,
            abs(float(item.technical_trick_loss or 0.0)),
            item.deal_id,
        ),
        reverse=True,
    )
    lane = TeacherReviewLane(
        event_id="30041",
        outcome_scale="MP_PERCENTAGE",
        ranking_scope="WITHIN_EVENT_ONLY",
        items=tuple(items),
    )
    queue = CrossEventTeacherReviewQueue(
        lanes=(lane,),
        cross_event_numeric_ranking_allowed=False,
        causal_error_attribution_allowed=False,
        student_error_attribution_allowed=False,
        interpretation=(
            "Additive teacher-review batch for observed target-pair opening leads with positive DDS3 regret. "
            "It is kept separate from historical review batches so their review IDs remain stable. MP percentage "
            "is used only as observed within-event context; no DDS3-to-MP causal conversion is made."
        ),
    )
    return serialize_teacher_review_queue(queue), findings


def build_opening_lead_review_handoff(
    source: Mapping[str, Any], report: Mapping[str, Any]
) -> dict[str, Any]:
    batch = normalize_30041_facts(source)
    queue_payload, findings = build_opening_lead_review_queue(source, report)
    ledger = build_pending_teacher_decision_ledger(queue_payload)
    ledger_payload = serialize_teacher_decision_ledger(ledger)
    dossier = build_teacher_review_dossier(
        queue_payload,
        ledger_payload,
        deals=batch.deals,
        findings=findings,
    )
    dossier_payload = serialize_teacher_review_dossier(dossier)
    inventory = build_evidence_episode_candidate_inventory(source, dossier_payload, event_id="30041")

    return {
        "schema": "tournament-opening-lead-review-handoff-v1",
        "event_id": "30041",
        "session_id": "round-2",
        "candidate_count": len(findings),
        "queue": queue_payload,
        "decision_ledger": ledger_payload,
        "dossier": dossier_payload,
        "episode_candidate_inventory": inventory,
        "automatic_teacher_decisions_allowed": False,
        "automatic_episode_scoring_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "historical_review_batch_mutation_allowed": False,
        "interpretation": (
            "Evidence-only handoff of opening-lead DDS3 candidates into the existing teacher-review pipeline. "
            "Every item remains pending teacher adjudication and coverage-ineligible until explicit downstream decisions exist."
        ),
    }
