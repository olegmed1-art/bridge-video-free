from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import Enum
import hashlib
import json
from typing import Any, Mapping, Sequence


class TeacherDecisionError(ValueError):
    pass


class TeacherDecisionStatus(str, Enum):
    PENDING = "PENDING"
    CONFIRMED_TECHNICAL_RELEVANCE = "CONFIRMED_TECHNICAL_RELEVANCE"
    DISMISSED = "DISMISSED"
    NEEDS_CONTEXT = "NEEDS_CONTEXT"


@dataclass(frozen=True)
class TeacherReviewDecision:
    review_id: str
    event_id: str
    deal_id: str
    category: str
    queue_item_sha256: str
    status: TeacherDecisionStatus = TeacherDecisionStatus.PENDING
    teacher_decision_required: bool = True
    automatic_methodology_mapping_allowed: bool = False
    automatic_student_error_attribution_allowed: bool = False
    decision_note: str | None = None
    decision_provenance: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class TeacherDecisionLedger:
    queue_sha256: str
    decisions: tuple[TeacherReviewDecision, ...]
    automatic_decisions_allowed: bool
    automatic_methodology_mapping_allowed: bool
    automatic_student_error_attribution_allowed: bool


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _queue_items(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if payload.get("schema") != "tournament-teacher-review-queue-v1":
        raise TeacherDecisionError("unsupported teacher review queue schema")
    if payload.get("cross_event_numeric_ranking_allowed") is not False:
        raise TeacherDecisionError("teacher review queue cross-event boundary was weakened")
    if payload.get("causal_error_attribution_allowed") is not False:
        raise TeacherDecisionError("teacher review queue causal boundary was weakened")
    if payload.get("student_error_attribution_allowed") is not False:
        raise TeacherDecisionError("teacher review queue student-attribution boundary was weakened")
    lanes = payload.get("lanes")
    if not isinstance(lanes, Sequence) or isinstance(lanes, (str, bytes)):
        raise TeacherDecisionError("teacher review queue lanes must be a sequence")
    items: list[Mapping[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for lane in lanes:
        if not isinstance(lane, Mapping):
            raise TeacherDecisionError("teacher review lane must be a mapping")
        if lane.get("ranking_scope") != "WITHIN_EVENT_ONLY":
            raise TeacherDecisionError("teacher review lane ranking scope was weakened")
        event_id = str(lane.get("event_id") or "")
        rows = lane.get("items")
        if not event_id or not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise TeacherDecisionError("teacher review lane is malformed")
        for item in rows:
            if not isinstance(item, Mapping):
                raise TeacherDecisionError("teacher review item must be a mapping")
            if str(item.get("event_id") or "") != event_id:
                raise TeacherDecisionError("teacher review item event mismatch")
            if item.get("causal_link") != "NOT_ESTABLISHED":
                raise TeacherDecisionError("teacher review item causal boundary was weakened")
            if item.get("student_error_attribution_allowed") is not False:
                raise TeacherDecisionError("teacher review item student-attribution boundary was weakened")
            if item.get("teacher_review_required") is not True:
                raise TeacherDecisionError("teacher review item must require teacher review")
            deal_id = str(item.get("deal_id") or "")
            category = str(item.get("category") or "")
            if not deal_id or not category:
                raise TeacherDecisionError("teacher review item missing identity/category")
            key = (event_id, deal_id, category)
            if key in seen:
                # Same deal may have multiple categories, but the same category identity must be unique.
                raise TeacherDecisionError(f"duplicate teacher review identity: {key}")
            seen.add(key)
            items.append(item)
    return items


def build_pending_teacher_decision_ledger(queue_payload: Mapping[str, Any]) -> TeacherDecisionLedger:
    items = _queue_items(queue_payload)
    queue_sha = _sha256(queue_payload)
    decisions: list[TeacherReviewDecision] = []
    for item in items:
        event_id = str(item["event_id"])
        deal_id = str(item["deal_id"])
        category = str(item["category"])
        item_sha = _sha256(item)
        review_id = hashlib.sha256(f"{queue_sha}|{event_id}|{deal_id}|{category}|{item_sha}".encode("utf-8")).hexdigest()
        decisions.append(
            TeacherReviewDecision(
                review_id=review_id,
                event_id=event_id,
                deal_id=deal_id,
                category=category,
                queue_item_sha256=item_sha,
            )
        )
    return TeacherDecisionLedger(
        queue_sha256=queue_sha,
        decisions=tuple(decisions),
        automatic_decisions_allowed=False,
        automatic_methodology_mapping_allowed=False,
        automatic_student_error_attribution_allowed=False,
    )


def apply_explicit_teacher_decision(
    ledger: TeacherDecisionLedger,
    *,
    review_id: str,
    status: TeacherDecisionStatus | str,
    note: str | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> TeacherDecisionLedger:
    try:
        parsed_status = status if isinstance(status, TeacherDecisionStatus) else TeacherDecisionStatus(str(status))
    except ValueError as exc:
        raise TeacherDecisionError(f"unsupported teacher decision status: {status!r}") from exc
    if parsed_status is TeacherDecisionStatus.PENDING:
        raise TeacherDecisionError("explicit decision cannot transition to PENDING")
    if not review_id:
        raise TeacherDecisionError("review_id is required")
    if provenance is None or provenance.get("decision_source") != "EXPLICIT_TEACHER_DECISION":
        raise TeacherDecisionError("explicit teacher decision provenance is required")
    clean_note = None if note is None else str(note).strip()
    if clean_note == "":
        clean_note = None

    found = False
    updated: list[TeacherReviewDecision] = []
    for decision in ledger.decisions:
        if decision.review_id != review_id:
            updated.append(decision)
            continue
        found = True
        if decision.status is not TeacherDecisionStatus.PENDING:
            raise TeacherDecisionError("teacher review decision is immutable after first explicit decision")
        updated.append(
            replace(
                decision,
                status=parsed_status,
                teacher_decision_required=False,
                decision_note=clean_note,
                decision_provenance=dict(provenance),
            )
        )
    if not found:
        raise TeacherDecisionError("review_id not found in ledger")
    return replace(ledger, decisions=tuple(updated))


def serialize_teacher_decision_ledger(ledger: TeacherDecisionLedger) -> dict[str, Any]:
    return {
        "schema": "tournament-teacher-decision-ledger-v1",
        "queue_sha256": ledger.queue_sha256,
        "automatic_decisions_allowed": ledger.automatic_decisions_allowed,
        "automatic_methodology_mapping_allowed": ledger.automatic_methodology_mapping_allowed,
        "automatic_student_error_attribution_allowed": ledger.automatic_student_error_attribution_allowed,
        "decisions": [
            {
                **asdict(decision),
                "status": decision.status.value,
            }
            for decision in ledger.decisions
        ],
    }
