from __future__ import annotations

from typing import Any, Mapping, Sequence

from .tournament_teacher_decisions_v3 import (
    TeacherDecisionLedger,
    TeacherDecisionStatus,
    apply_explicit_teacher_decision,
    build_pending_teacher_decision_ledger,
    serialize_teacher_decision_ledger,
)
from .tournament_teacher_review_bundle_v3 import verify_teacher_review_bundle


class TeacherDecisionIntakeError(ValueError):
    pass


_DECIDED_STATUSES = (
    TeacherDecisionStatus.CONFIRMED_TECHNICAL_RELEVANCE.value,
    TeacherDecisionStatus.DISMISSED.value,
    TeacherDecisionStatus.NEEDS_CONTEXT.value,
)


def _bundle_parts(bundle: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    verify_teacher_review_bundle(bundle)
    components = bundle.get("components")
    if not isinstance(components, Mapping):
        raise TeacherDecisionIntakeError("portable bundle components are missing")
    queue = components.get("queue")
    dossier = components.get("dossier")
    if not isinstance(queue, Mapping) or not isinstance(dossier, Mapping):
        raise TeacherDecisionIntakeError("portable bundle queue/dossier are malformed")
    return queue, dossier


def build_teacher_decision_template(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Create an inert form: no decision is preselected and no teacher identity is invented."""
    _, dossier = _bundle_parts(bundle)
    items = dossier.get("items")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        raise TeacherDecisionIntakeError("portable dossier items are malformed")
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise TeacherDecisionIntakeError("portable dossier item must be a mapping")
        rows.append(
            {
                "review_id": str(item.get("review_id") or ""),
                "event_id": str(item.get("event_id") or ""),
                "deal_id": str(item.get("deal_id") or ""),
                "category": str(item.get("category") or ""),
                "allowed_statuses": list(_DECIDED_STATUSES),
                "status": None,
                "decision_note": None,
                "decision_actor": None,
                "decision_reference": None,
                "explicit_teacher_decision": False,
            }
        )
    if any(not row["review_id"] for row in rows) or len({row["review_id"] for row in rows}) != len(rows):
        raise TeacherDecisionIntakeError("portable dossier review identities are invalid")
    return {
        "schema": "tournament-teacher-decision-intake-v1",
        "bundle_id": bundle["bundle_id"],
        "queue_sha256": bundle["queue_sha256"],
        "automatic_decisions_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "required_decision_source": "EXPLICIT_TEACHER_DECISION",
        "decisions": rows,
        "instructions": (
            "This is an inert decision form. Leave a row unchanged to keep it PENDING. To decide a row, an explicit "
            "teacher must set one allowed status, decision_actor, explicit_teacher_decision=true, and may add a note/reference."
        ),
    }


def _decision_index_from_template(bundle: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    template = build_teacher_decision_template(bundle)
    return {str(row["review_id"]): row for row in template["decisions"]}


def _validate_intake_envelope(bundle: Mapping[str, Any], intake: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    if intake.get("schema") != "tournament-teacher-decision-intake-v1":
        raise TeacherDecisionIntakeError("unsupported teacher decision intake schema")
    if intake.get("bundle_id") != bundle.get("bundle_id"):
        raise TeacherDecisionIntakeError("teacher decision intake bundle_id mismatch")
    if intake.get("queue_sha256") != bundle.get("queue_sha256"):
        raise TeacherDecisionIntakeError("teacher decision intake queue digest mismatch")
    for field in (
        "automatic_decisions_allowed",
        "automatic_methodology_mapping_allowed",
        "automatic_student_error_attribution_allowed",
    ):
        if intake.get(field) is not False:
            raise TeacherDecisionIntakeError(f"teacher decision intake boundary was weakened: {field}")
    if intake.get("required_decision_source") != "EXPLICIT_TEACHER_DECISION":
        raise TeacherDecisionIntakeError("explicit teacher decision source contract was weakened")
    rows = intake.get("decisions")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise TeacherDecisionIntakeError("teacher decision intake rows must be a sequence")
    return rows


def apply_teacher_decision_intake(bundle: Mapping[str, Any], intake: Mapping[str, Any]) -> dict[str, Any]:
    """Apply only explicitly attested teacher rows; blank rows remain PENDING."""
    queue, _ = _bundle_parts(bundle)
    rows = _validate_intake_envelope(bundle, intake)
    expected = _decision_index_from_template(bundle)
    seen: set[str] = set()
    ledger: TeacherDecisionLedger = build_pending_teacher_decision_ledger(queue)
    decided_count = 0

    for raw in rows:
        if not isinstance(raw, Mapping):
            raise TeacherDecisionIntakeError("teacher decision intake row must be a mapping")
        review_id = str(raw.get("review_id") or "")
        if not review_id or review_id in seen:
            raise TeacherDecisionIntakeError("teacher decision review_id missing or duplicated")
        seen.add(review_id)
        identity = expected.get(review_id)
        if identity is None:
            raise TeacherDecisionIntakeError("teacher decision intake contains unknown review_id")
        for field in ("event_id", "deal_id", "category"):
            if str(raw.get(field) or "") != str(identity.get(field) or ""):
                raise TeacherDecisionIntakeError(f"teacher decision immutable identity changed: {field}")
        status = raw.get("status")
        explicit = raw.get("explicit_teacher_decision")
        actor = None if raw.get("decision_actor") is None else str(raw.get("decision_actor")).strip()
        note = None if raw.get("decision_note") is None else str(raw.get("decision_note")).strip() or None
        reference = None if raw.get("decision_reference") is None else str(raw.get("decision_reference")).strip() or None

        if status in (None, ""):
            if explicit is not False or actor is not None or note is not None or reference is not None:
                raise TeacherDecisionIntakeError("blank/PENDING row must not contain decision material")
            continue
        if str(status) not in _DECIDED_STATUSES:
            raise TeacherDecisionIntakeError(f"unsupported explicit teacher decision status: {status!r}")
        if explicit is not True or not actor:
            raise TeacherDecisionIntakeError("decided row requires explicit_teacher_decision=true and decision_actor")

        provenance: dict[str, Any] = {
            "decision_source": "EXPLICIT_TEACHER_DECISION",
            "decision_actor": actor,
            "bundle_id": bundle["bundle_id"],
            "queue_sha256": bundle["queue_sha256"],
        }
        if reference is not None:
            provenance["decision_reference"] = reference
        ledger = apply_explicit_teacher_decision(
            ledger,
            review_id=review_id,
            status=str(status),
            note=note,
            provenance=provenance,
        )
        decided_count += 1

    if seen != set(expected):
        raise TeacherDecisionIntakeError("teacher decision intake must preserve the exact review set")
    payload = serialize_teacher_decision_ledger(ledger)
    return {
        "schema": "tournament-teacher-decision-intake-result-v1",
        "bundle_id": bundle["bundle_id"],
        "queue_sha256": bundle["queue_sha256"],
        "decided_count": decided_count,
        "pending_count": len(payload["decisions"]) - decided_count,
        "automatic_decisions_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "ledger": payload,
    }
