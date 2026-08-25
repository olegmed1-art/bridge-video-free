from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence


class CommittedTeacherDecisionError(ValueError):
    pass


_DECISION_TO_PORTFOLIO_STATUS = {
    "ERROR_CONFIRMED_BY_TEACHER": "CONFIRMED_TECHNICAL_RELEVANCE",
    "NEEDS_CONTEXT": "NEEDS_CONTEXT",
    "NO_ERROR_CONFIRMED_BY_TEACHER": "DISMISSED",
}


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise CommittedTeacherDecisionError(f"missing required committed decision field: {field}")
    return text


def _validate_record(record: Mapping[str, Any]) -> tuple[str, str, int, str]:
    if record.get("schema") != "bridge-school-teacher-decision-v1":
        raise CommittedTeacherDecisionError("unsupported committed teacher decision schema")
    if record.get("scope") != "board-level teacher adjudication":
        raise CommittedTeacherDecisionError("committed decision scope must be board-level teacher adjudication")

    event_id = _required_text(record.get("event_id"), "event_id")
    session_id = _required_text(record.get("session_id"), "session_id")
    try:
        board_number = int(record.get("board_number"))
    except (TypeError, ValueError) as exc:
        raise CommittedTeacherDecisionError("board_number must be an integer") from exc
    if board_number <= 0:
        raise CommittedTeacherDecisionError("board_number must be positive")

    decision = _required_text(record.get("decision"), "decision")
    if decision not in _DECISION_TO_PORTFOLIO_STATUS:
        raise CommittedTeacherDecisionError(f"unsupported committed teacher decision: {decision!r}")
    _required_text(record.get("teacher_statement"), "teacher_statement")

    provenance = record.get("provenance")
    if not isinstance(provenance, Mapping):
        raise CommittedTeacherDecisionError("committed decision provenance is required")
    if provenance.get("source") != "interactive teacher review in ChatGPT":
        raise CommittedTeacherDecisionError("committed decision must retain explicit interactive teacher provenance")
    _required_text(provenance.get("recorded_date"), "provenance.recorded_date")

    if record.get("methodology_mapping") is not None:
        raise CommittedTeacherDecisionError("committed board decision cannot carry methodology mapping")
    if record.get("specific_card_or_cause_attribution") is not None:
        raise CommittedTeacherDecisionError("committed board decision cannot carry specific card/cause attribution")

    return event_id, session_id, board_number, decision


def sync_committed_board_decisions_into_portfolio_intake(
    intake: Mapping[str, Any],
    decision_records: Sequence[Mapping[str, Any]],
    *,
    decision_actor: str,
    decision_references: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Overlay explicit committed board-level teacher decisions onto an inert portfolio intake.

    The mapping is intentionally conservative. A board-level decision is applied only
    when it maps to exactly one portfolio review row, unless the committed record itself
    carries an exact ``review_id`` (and optionally ``source_bundle_id``). Multi-signal
    boards therefore fail closed instead of turning one board-level statement into
    several technical conclusions.

    ``ERROR_CONFIRMED_BY_TEACHER`` maps only to the existing portfolio status
    ``CONFIRMED_TECHNICAL_RELEVANCE``. The stronger board-level error statement remains
    in the committed source record; this adapter does not invent a specific technical
    cause, card, methodology category, or student-error attribution for a review item.
    """
    if intake.get("schema") != "tournament-teacher-review-portfolio-decision-intake-v1":
        raise CommittedTeacherDecisionError("unsupported portfolio decision intake schema")
    for field in (
        "automatic_decisions_allowed",
        "automatic_methodology_mapping_allowed",
        "automatic_student_error_attribution_allowed",
    ):
        if intake.get(field) is not False:
            raise CommittedTeacherDecisionError(f"portfolio decision boundary was weakened: {field}")
    if intake.get("required_decision_source") != "EXPLICIT_TEACHER_DECISION":
        raise CommittedTeacherDecisionError("portfolio intake no longer requires explicit teacher decisions")

    actor = str(decision_actor or "").strip()
    if not actor:
        raise CommittedTeacherDecisionError("decision_actor is required")
    if not isinstance(decision_records, Sequence) or isinstance(decision_records, (str, bytes)):
        raise CommittedTeacherDecisionError("decision_records must be a sequence")
    if decision_references is not None and len(decision_references) != len(decision_records):
        raise CommittedTeacherDecisionError("decision_references cardinality mismatch")

    result_intake = deepcopy(dict(intake))
    rows = result_intake.get("decisions")
    if not isinstance(rows, list):
        raise CommittedTeacherDecisionError("portfolio intake decisions must be a list")

    applied: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    used_rows: set[tuple[str, str]] = set()

    for index, raw_record in enumerate(decision_records):
        if not isinstance(raw_record, Mapping):
            raise CommittedTeacherDecisionError("committed decision record must be a mapping")
        event_id, session_id, board_number, decision = _validate_record(raw_record)
        deal_id = f"{event_id}:{session_id}:{board_number}"
        requested_review_id = str(raw_record.get("review_id") or "").strip() or None
        requested_bundle_id = str(raw_record.get("source_bundle_id") or "").strip() or None

        matches: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                raise CommittedTeacherDecisionError("portfolio decision row must be a mapping")
            if str(row.get("event_id") or "") != event_id or str(row.get("deal_id") or "") != deal_id:
                continue
            if requested_review_id is not None and str(row.get("review_id") or "") != requested_review_id:
                continue
            if requested_bundle_id is not None and str(row.get("source_bundle_id") or "") != requested_bundle_id:
                continue
            matches.append(row)

        if not matches:
            blocked.append(
                {
                    "event_id": event_id,
                    "deal_id": deal_id,
                    "decision": decision,
                    "reason": "NO_EXACT_PORTFOLIO_REVIEW_MATCH",
                }
            )
            continue
        if len(matches) != 1:
            blocked.append(
                {
                    "event_id": event_id,
                    "deal_id": deal_id,
                    "decision": decision,
                    "matching_review_count": len(matches),
                    "matching_review_ids": sorted(str(row.get("review_id") or "") for row in matches),
                    "reason": "AMBIGUOUS_MULTI_SIGNAL_BOARD_REQUIRES_EXACT_REVIEW_BINDING",
                }
            )
            continue

        row = matches[0]
        key = (str(row.get("source_bundle_id") or ""), str(row.get("review_id") or ""))
        if not all(key) or key in used_rows:
            raise CommittedTeacherDecisionError("committed decisions duplicate or lack exact portfolio review identity")
        used_rows.add(key)
        if row.get("status") not in (None, "") or row.get("explicit_teacher_decision") is not False:
            raise CommittedTeacherDecisionError("committed decision attempted to overwrite a non-inert portfolio row")

        target_status = _DECISION_TO_PORTFOLIO_STATUS[decision]
        allowed = row.get("allowed_statuses")
        if not isinstance(allowed, list) or target_status not in allowed:
            raise CommittedTeacherDecisionError("mapped committed decision status is not allowed by portfolio intake")

        reference = None
        if decision_references is not None:
            reference = str(decision_references[index] or "").strip() or None
        statement = str(raw_record.get("teacher_statement") or "").strip()
        note = (
            f"Committed board-level teacher statement: {statement}. "
            "Mapped only to the existing review-status gate; no specific technical cause, card, "
            "methodology mapping, or automatic student-error attribution is inferred."
        )
        row["status"] = target_status
        row["decision_note"] = note
        row["decision_actor"] = actor
        row["decision_reference"] = reference
        row["explicit_teacher_decision"] = True

        applied.append(
            {
                "event_id": event_id,
                "deal_id": deal_id,
                "source_bundle_id": key[0],
                "review_id": key[1],
                "source_decision": decision,
                "portfolio_status": target_status,
                "decision_reference": reference,
            }
        )

    return {
        "schema": "tournament-committed-teacher-decision-sync-v1",
        "portfolio_id": result_intake.get("portfolio_id"),
        "decision_record_count": len(decision_records),
        "applied_count": len(applied),
        "blocked_count": len(blocked),
        "applied": applied,
        "blocked": blocked,
        "automatic_decisions_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "cross_category_causal_collapse_allowed": False,
        "intake": result_intake,
    }
