from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

from .tournament_teacher_decisions_v3 import TeacherDecisionStatus
from .tournament_teacher_review_bundle_v3 import verify_teacher_review_bundle


class TeacherConfirmedLongitudinalError(ValueError):
    pass


def _require_false(payload: Mapping[str, Any], fields: Sequence[str], *, label: str) -> None:
    for field in fields:
        if payload.get(field) is not False:
            raise TeacherConfirmedLongitudinalError(f"{label} boundary was weakened: {field}")


def _validate_decision_ledger(
    *,
    pending_ledger: Mapping[str, Any],
    decision_ledger: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    if decision_ledger.get("schema") != "tournament-teacher-decision-ledger-v1":
        raise TeacherConfirmedLongitudinalError("unsupported teacher decision ledger schema")
    _require_false(
        decision_ledger,
        (
            "automatic_decisions_allowed",
            "automatic_methodology_mapping_allowed",
            "automatic_student_error_attribution_allowed",
        ),
        label="decision ledger",
    )
    if str(decision_ledger.get("queue_sha256") or "") != str(pending_ledger.get("queue_sha256") or ""):
        raise TeacherConfirmedLongitudinalError("decision ledger is not bound to the portable bundle queue")

    pending_rows = pending_ledger.get("decisions")
    decision_rows = decision_ledger.get("decisions")
    if not isinstance(pending_rows, Sequence) or isinstance(pending_rows, (str, bytes)):
        raise TeacherConfirmedLongitudinalError("portable bundle pending ledger is malformed")
    if not isinstance(decision_rows, Sequence) or isinstance(decision_rows, (str, bytes)):
        raise TeacherConfirmedLongitudinalError("decision ledger rows must be a sequence")
    if len(pending_rows) != len(decision_rows):
        raise TeacherConfirmedLongitudinalError("decision ledger cardinality changed")

    expected: dict[str, Mapping[str, Any]] = {}
    for raw in pending_rows:
        if not isinstance(raw, Mapping):
            raise TeacherConfirmedLongitudinalError("portable pending decision row is malformed")
        review_id = str(raw.get("review_id") or "")
        if not review_id or review_id in expected:
            raise TeacherConfirmedLongitudinalError("portable pending ledger has invalid review_id")
        expected[review_id] = raw

    actual: dict[str, Mapping[str, Any]] = {}
    valid_statuses = {status.value for status in TeacherDecisionStatus}
    for raw in decision_rows:
        if not isinstance(raw, Mapping):
            raise TeacherConfirmedLongitudinalError("decision ledger row must be a mapping")
        review_id = str(raw.get("review_id") or "")
        source = expected.get(review_id)
        if source is None or review_id in actual:
            raise TeacherConfirmedLongitudinalError("decision ledger review_id mismatch or duplicate")
        for field in ("event_id", "deal_id", "category", "queue_item_sha256"):
            if raw.get(field) != source.get(field):
                raise TeacherConfirmedLongitudinalError(f"immutable decision identity changed: {field}")
        if raw.get("automatic_methodology_mapping_allowed") is not False:
            raise TeacherConfirmedLongitudinalError("automatic methodology mapping was enabled")
        if raw.get("automatic_student_error_attribution_allowed") is not False:
            raise TeacherConfirmedLongitudinalError("automatic student-error attribution was enabled")

        status = str(raw.get("status") or "")
        if status not in valid_statuses:
            raise TeacherConfirmedLongitudinalError(f"unsupported teacher decision status: {status!r}")
        provenance = raw.get("decision_provenance")
        if status == TeacherDecisionStatus.PENDING.value:
            if raw.get("teacher_decision_required") is not True:
                raise TeacherConfirmedLongitudinalError("pending decision must still require teacher review")
            if raw.get("decision_note") is not None or provenance is not None:
                raise TeacherConfirmedLongitudinalError("pending decision contains explicit decision material")
        else:
            if raw.get("teacher_decision_required") is not False:
                raise TeacherConfirmedLongitudinalError("decided review must close teacher_decision_required")
            if not isinstance(provenance, Mapping) or provenance.get("decision_source") != "EXPLICIT_TEACHER_DECISION":
                raise TeacherConfirmedLongitudinalError("non-pending status requires explicit teacher-decision provenance")
        actual[review_id] = raw

    if set(actual) != set(expected):
        raise TeacherConfirmedLongitudinalError("decision ledger does not cover the exact portable review set")
    return actual


def _dossier_index(dossier_payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    items = dossier_payload.get("items")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        raise TeacherConfirmedLongitudinalError("portable dossier items are malformed")
    out: dict[str, Mapping[str, Any]] = {}
    for raw in items:
        if not isinstance(raw, Mapping):
            raise TeacherConfirmedLongitudinalError("portable dossier item must be a mapping")
        review_id = str(raw.get("review_id") or "")
        if not review_id or review_id in out:
            raise TeacherConfirmedLongitudinalError("portable dossier review_id missing or duplicated")
        if raw.get("methodology_mapping") is not None or raw.get("student_error_attribution") is not None:
            raise TeacherConfirmedLongitudinalError("portable dossier contains pedagogical attribution")
        if raw.get("causal_link") != "NOT_ESTABLISHED":
            raise TeacherConfirmedLongitudinalError("portable dossier causal boundary was weakened")
        out[review_id] = raw
    return out


def build_teacher_confirmed_longitudinal_report(
    portable_bundle: Mapping[str, Any],
    decision_ledger: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a technical longitudinal view only after explicit teacher relevance decisions.

    CONFIRMED_TECHNICAL_RELEVANCE means only that the teacher chose to retain the
    technical finding for longitudinal review. It is not a student-error decision,
    a causal attribution, a teaching category or a methodology mapping.
    """
    verify_teacher_review_bundle(portable_bundle)
    components = portable_bundle["components"]
    pending_ledger = components["ledger"]
    dossier = components["dossier"]
    active_ledger = pending_ledger if decision_ledger is None else decision_ledger

    decisions = _validate_decision_ledger(pending_ledger=pending_ledger, decision_ledger=active_ledger)
    dossier_by_id = _dossier_index(dossier)
    if set(decisions) != set(dossier_by_id):
        raise TeacherConfirmedLongitudinalError("decision ledger and dossier review sets differ")

    status_counts: Counter[str] = Counter()
    confirmed: list[dict[str, Any]] = []
    clusters_raw: dict[str, list[dict[str, Any]]] = {}
    confirmed_without_repeat_key = 0

    for review_id, decision in decisions.items():
        status = str(decision["status"])
        status_counts[status] += 1
        if status != TeacherDecisionStatus.CONFIRMED_TECHNICAL_RELEVANCE.value:
            continue
        dossier_item = dossier_by_id[review_id]
        finding = dossier_item.get("technical_finding")
        if not isinstance(finding, Mapping):
            raise TeacherConfirmedLongitudinalError("confirmed dossier item lacks technical finding")
        repeat_key = finding.get("repeat_key")
        row = {
            "review_id": review_id,
            "event_id": str(dossier_item["event_id"]),
            "deal_id": str(dossier_item["deal_id"]),
            "category": str(dossier_item["category"]),
            "repeat_key": None if repeat_key in (None, "") else str(repeat_key),
            "technical_trick_loss": finding.get("trick_loss"),
            "observability": finding.get("observability"),
            "decision_note": decision.get("decision_note"),
            "decision_provenance": decision.get("decision_provenance"),
            "causal_link": "NOT_ESTABLISHED",
            "student_error_attribution": None,
            "methodology_mapping": None,
        }
        confirmed.append(row)
        if row["repeat_key"] is None:
            confirmed_without_repeat_key += 1
        else:
            clusters_raw.setdefault(str(row["repeat_key"]), []).append(row)

    clusters: list[dict[str, Any]] = []
    for repeat_key, rows in clusters_raw.items():
        event_ids = sorted({str(row["event_id"]) for row in rows})
        clusters.append(
            {
                "repeat_key": repeat_key,
                "finding_count": len(rows),
                "event_count": len(event_ids),
                "event_ids": event_ids,
                "persistent_across_events": len(event_ids) >= 2,
                "technical_trick_loss_mass": sum(abs(float(row["technical_trick_loss"] or 0.0)) for row in rows),
                "causal_link": "NOT_ESTABLISHED",
                "student_error_attribution": None,
                "methodology_mapping": None,
            }
        )
    clusters.sort(
        key=lambda row: (
            bool(row["persistent_across_events"]),
            int(row["event_count"]),
            float(row["technical_trick_loss_mass"]),
            str(row["repeat_key"]),
        ),
        reverse=True,
    )

    return {
        "schema": "tournament-teacher-confirmed-longitudinal-v1",
        "bundle_id": portable_bundle["bundle_id"],
        "queue_sha256": portable_bundle["queue_sha256"],
        "teacher_decision_gate_enforced": True,
        "technical_relevance_only": True,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "causal_error_attribution_allowed": False,
        "status_counts": {status.value: int(status_counts.get(status.value, 0)) for status in TeacherDecisionStatus},
        "confirmed_items": confirmed,
        "confirmed_without_repeat_key": confirmed_without_repeat_key,
        "clusters": clusters,
        "persistent_clusters": [row for row in clusters if row["persistent_across_events"]],
        "interpretation": (
            "Only items explicitly marked CONFIRMED_TECHNICAL_RELEVANCE by the teacher may enter this technical "
            "longitudinal view. Confirmation does not establish a student error, causality, a teaching category, "
            "or methodology mapping."
        ),
    }
