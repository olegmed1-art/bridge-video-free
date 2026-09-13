from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from .tournament_teacher_decisions_v3 import (
    build_pending_teacher_decision_ledger,
    serialize_teacher_decision_ledger,
)


class TeacherReviewBundleError(ValueError):
    pass


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _require_false(payload: Mapping[str, Any], fields: Sequence[str], *, label: str) -> None:
    for field in fields:
        if payload.get(field) is not False:
            raise TeacherReviewBundleError(f"{label} boundary was weakened: {field}")


def _validate_queue(queue_payload: Mapping[str, Any]) -> str:
    if queue_payload.get("schema") != "tournament-teacher-review-queue-v1":
        raise TeacherReviewBundleError("unsupported teacher review queue schema")
    _require_false(
        queue_payload,
        (
            "cross_event_numeric_ranking_allowed",
            "causal_error_attribution_allowed",
            "student_error_attribution_allowed",
        ),
        label="queue",
    )
    lanes = queue_payload.get("lanes")
    if not isinstance(lanes, Sequence) or isinstance(lanes, (str, bytes)):
        raise TeacherReviewBundleError("teacher review queue lanes must be a sequence")
    return _sha256(queue_payload)


def _expected_pending_ledger(queue_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    return serialize_teacher_decision_ledger(build_pending_teacher_decision_ledger(queue_payload))


def _validate_ledger(queue_payload: Mapping[str, Any], ledger_payload: Mapping[str, Any]) -> None:
    if ledger_payload.get("schema") != "tournament-teacher-decision-ledger-v1":
        raise TeacherReviewBundleError("unsupported teacher decision ledger schema")
    _require_false(
        ledger_payload,
        (
            "automatic_decisions_allowed",
            "automatic_methodology_mapping_allowed",
            "automatic_student_error_attribution_allowed",
        ),
        label="ledger",
    )
    expected = _expected_pending_ledger(queue_payload)
    if ledger_payload != expected:
        raise TeacherReviewBundleError("portable bundle requires the exact pending ledger bound to this queue")


def _validate_dossier(
    queue_sha256: str,
    ledger_payload: Mapping[str, Any],
    dossier_payload: Mapping[str, Any],
) -> tuple[list[Mapping[str, Any]], dict[str, int]]:
    if dossier_payload.get("schema") != "tournament-teacher-review-dossier-v1":
        raise TeacherReviewBundleError("unsupported teacher review dossier schema")
    _require_false(
        dossier_payload,
        (
            "automatic_decisions_allowed",
            "automatic_methodology_mapping_allowed",
            "automatic_student_error_attribution_allowed",
            "cross_event_numeric_ranking_allowed",
        ),
        label="dossier",
    )
    if str(dossier_payload.get("queue_sha256") or "") != queue_sha256:
        raise TeacherReviewBundleError("dossier is not bound to this queue")
    if str(ledger_payload.get("queue_sha256") or "") != queue_sha256:
        raise TeacherReviewBundleError("ledger is not bound to this queue")

    decisions = ledger_payload.get("decisions")
    items = dossier_payload.get("items")
    if not isinstance(decisions, Sequence) or isinstance(decisions, (str, bytes)):
        raise TeacherReviewBundleError("ledger decisions must be a sequence")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        raise TeacherReviewBundleError("dossier items must be a sequence")
    if len(items) != len(decisions):
        raise TeacherReviewBundleError("dossier/ledger cardinality mismatch")

    decisions_by_id: dict[str, Mapping[str, Any]] = {}
    for raw in decisions:
        if not isinstance(raw, Mapping):
            raise TeacherReviewBundleError("ledger decision must be a mapping")
        review_id = str(raw.get("review_id") or "")
        if not review_id or review_id in decisions_by_id:
            raise TeacherReviewBundleError("ledger review_id missing or duplicated")
        if raw.get("status") != "PENDING" or raw.get("teacher_decision_required") is not True:
            raise TeacherReviewBundleError("portable bundle accepts pending teacher decisions only")
        if raw.get("decision_note") is not None or raw.get("decision_provenance") is not None:
            raise TeacherReviewBundleError("pending ledger contains decision material")
        decisions_by_id[review_id] = raw

    event_counts: dict[str, int] = {}
    normalized_items: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for raw in items:
        if not isinstance(raw, Mapping):
            raise TeacherReviewBundleError("dossier item must be a mapping")
        review_id = str(raw.get("review_id") or "")
        if not review_id or review_id in seen:
            raise TeacherReviewBundleError("dossier review_id missing or duplicated")
        seen.add(review_id)
        decision = decisions_by_id.get(review_id)
        if decision is None:
            raise TeacherReviewBundleError("dossier contains review_id absent from ledger")
        identity = (
            str(raw.get("event_id") or ""),
            str(raw.get("deal_id") or ""),
            str(raw.get("category") or ""),
        )
        decision_identity = (
            str(decision.get("event_id") or ""),
            str(decision.get("deal_id") or ""),
            str(decision.get("category") or ""),
        )
        if not all(identity) or identity != decision_identity:
            raise TeacherReviewBundleError("dossier/ledger identity mismatch")
        if raw.get("status") != "PENDING" or raw.get("teacher_decision_required") is not True:
            raise TeacherReviewBundleError("dossier contains a non-pending review")
        if raw.get("causal_link") != "NOT_ESTABLISHED":
            raise TeacherReviewBundleError("dossier causal boundary was weakened")
        if raw.get("automatic_methodology_mapping_allowed") is not False:
            raise TeacherReviewBundleError("automatic methodology mapping was enabled")
        if raw.get("automatic_student_error_attribution_allowed") is not False:
            raise TeacherReviewBundleError("automatic student-error attribution was enabled")
        if raw.get("methodology_mapping") is not None or raw.get("student_error_attribution") is not None:
            raise TeacherReviewBundleError("pending dossier contains pedagogical attribution")

        deal_facts = raw.get("deal_facts")
        finding = raw.get("technical_finding")
        if not isinstance(deal_facts, Mapping) or not isinstance(finding, Mapping):
            raise TeacherReviewBundleError("dossier item lacks deal facts or technical finding")
        hands = deal_facts.get("hands")
        if not isinstance(hands, Mapping) or set(hands) != {"N", "E", "S", "W"}:
            raise TeacherReviewBundleError("dossier hands must contain exactly N/E/S/W")
        cards: list[str] = []
        for seat in ("N", "E", "S", "W"):
            hand = hands.get(seat)
            if not isinstance(hand, Sequence) or isinstance(hand, (str, bytes)) or len(hand) != 13:
                raise TeacherReviewBundleError("every dossier hand must contain 13 cards")
            cards.extend(str(card) for card in hand)
        if len(cards) != 52 or len(set(cards)) != 52:
            raise TeacherReviewBundleError("dossier deal must contain 52 unique cards")
        evidence = finding.get("evidence")
        if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)) or not evidence:
            raise TeacherReviewBundleError("technical finding requires evidence")

        event_id = identity[0]
        event_counts[event_id] = event_counts.get(event_id, 0) + 1
        normalized_items.append(raw)

    if set(seen) != set(decisions_by_id):
        raise TeacherReviewBundleError("not every ledger decision has a dossier item")
    return normalized_items, dict(sorted(event_counts.items()))


def build_teacher_review_bundle(
    queue_payload: Mapping[str, Any],
    ledger_payload: Mapping[str, Any],
    dossier_payload: Mapping[str, Any],
) -> dict[str, Any]:
    queue_sha = _validate_queue(queue_payload)
    _validate_ledger(queue_payload, ledger_payload)
    items, event_counts = _validate_dossier(queue_sha, ledger_payload, dossier_payload)

    component_sha256 = {
        "queue": _sha256(queue_payload),
        "ledger": _sha256(ledger_payload),
        "dossier": _sha256(dossier_payload),
    }
    identity_payload = {
        "schema": "tournament-teacher-review-portable-bundle-v1",
        "component_sha256": component_sha256,
        "queue_sha256": queue_sha,
    }
    bundle_id = _sha256(identity_payload)
    return {
        "schema": "tournament-teacher-review-portable-bundle-v1",
        "bundle_id": bundle_id,
        "queue_sha256": queue_sha,
        "component_sha256": component_sha256,
        "item_count": len(items),
        "event_counts": event_counts,
        "review_state": "PENDING_TEACHER_DECISION",
        "automatic_decisions_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "cross_event_numeric_ranking_allowed": False,
        "components": {
            "queue": queue_payload,
            "ledger": ledger_payload,
            "dossier": dossier_payload,
        },
        "interpretation": (
            "Portable, hash-bound review package. All items remain pending explicit teacher decisions. "
            "The package preserves tournament facts, scoring context and technical DDS evidence but does not establish "
            "causality, a student error, a teaching category or methodology mapping."
        ),
    }


def verify_teacher_review_bundle(bundle_payload: Mapping[str, Any]) -> None:
    if bundle_payload.get("schema") != "tournament-teacher-review-portable-bundle-v1":
        raise TeacherReviewBundleError("unsupported portable bundle schema")
    components = bundle_payload.get("components")
    if not isinstance(components, Mapping):
        raise TeacherReviewBundleError("portable bundle components are missing")
    queue_payload = components.get("queue")
    ledger_payload = components.get("ledger")
    dossier_payload = components.get("dossier")
    if not all(isinstance(value, Mapping) for value in (queue_payload, ledger_payload, dossier_payload)):
        raise TeacherReviewBundleError("portable bundle components are malformed")

    rebuilt = build_teacher_review_bundle(queue_payload, ledger_payload, dossier_payload)
    for key in (
        "bundle_id",
        "queue_sha256",
        "component_sha256",
        "item_count",
        "event_counts",
        "review_state",
        "automatic_decisions_allowed",
        "automatic_methodology_mapping_allowed",
        "automatic_student_error_attribution_allowed",
        "cross_event_numeric_ranking_allowed",
    ):
        if bundle_payload.get(key) != rebuilt.get(key):
            raise TeacherReviewBundleError(f"portable bundle verification failed: {key}")
