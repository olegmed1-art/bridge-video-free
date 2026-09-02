from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

from .tournament_teacher_confirmed_portfolio_longitudinal_v3 import (
    build_portfolio_teacher_confirmed_longitudinal_report,
)
from .tournament_teacher_decisions_v3 import TeacherDecisionStatus
from .tournament_teacher_review_portfolio_v3 import verify_teacher_review_portfolio


class TournamentTeacherReviewReleaseGateError(ValueError):
    pass


def _decision_index(portfolio_decision_result: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    if portfolio_decision_result.get("schema") != "tournament-teacher-review-portfolio-decision-result-v1":
        raise TournamentTeacherReviewReleaseGateError("unsupported portfolio decision result schema")
    bundle_results = portfolio_decision_result.get("bundle_results")
    if not isinstance(bundle_results, Sequence) or isinstance(bundle_results, (str, bytes)):
        raise TournamentTeacherReviewReleaseGateError("bundle_results must be a sequence")

    out: dict[tuple[str, str], Mapping[str, Any]] = {}
    for bundle_result in bundle_results:
        if not isinstance(bundle_result, Mapping):
            raise TournamentTeacherReviewReleaseGateError("bundle result must be a mapping")
        bundle_id = str(bundle_result.get("source_bundle_id") or "").strip()
        ledger = bundle_result.get("ledger")
        if not bundle_id or not isinstance(ledger, Mapping):
            raise TournamentTeacherReviewReleaseGateError("bundle result identity or ledger missing")
        if ledger.get("schema") != "tournament-teacher-decision-ledger-v1":
            raise TournamentTeacherReviewReleaseGateError("unsupported teacher decision ledger schema")
        decisions = ledger.get("decisions")
        if not isinstance(decisions, Sequence) or isinstance(decisions, (str, bytes)):
            raise TournamentTeacherReviewReleaseGateError("teacher decision rows must be a sequence")
        for decision in decisions:
            if not isinstance(decision, Mapping):
                raise TournamentTeacherReviewReleaseGateError("teacher decision row must be a mapping")
            review_id = str(decision.get("review_id") or "").strip()
            key = (bundle_id, review_id)
            if not review_id or key in out:
                raise TournamentTeacherReviewReleaseGateError("teacher decision identity missing or duplicated")
            out[key] = decision
    return out


def build_event_teacher_review_release_gate(
    portfolio: Mapping[str, Any],
    bundles: Sequence[Mapping[str, Any]],
    portfolio_decision_result: Mapping[str, Any],
    *,
    event_id: str,
) -> dict[str, Any]:
    """Require every technical review item for one event to cross the Teacher Decision Gate.

    The portfolio may contain multiple independent evidence families and multiple
    tournaments. This event-scoped gate prevents a later review batch (for example,
    opening-lead DDS3) from being omitted merely because an older episode inventory
    covered only an earlier batch. It does not score an episode, infer causality, or
    create methodology/student attribution.
    """
    verify_teacher_review_portfolio(portfolio, bundles)
    confirmed_report = build_portfolio_teacher_confirmed_longitudinal_report(
        portfolio, bundles, portfolio_decision_result
    )

    target_event = str(event_id or "").strip()
    if not target_event:
        raise TournamentTeacherReviewReleaseGateError("event_id is required")
    if portfolio_decision_result.get("portfolio_id") != portfolio.get("portfolio_id"):
        raise TournamentTeacherReviewReleaseGateError("portfolio decision result portfolio_id mismatch")

    items = portfolio.get("items")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        raise TournamentTeacherReviewReleaseGateError("portfolio items must be a sequence")
    event_items = [item for item in items if isinstance(item, Mapping) and str(item.get("event_id") or "") == target_event]
    if not event_items:
        raise TournamentTeacherReviewReleaseGateError("event has no review items in the verified portfolio")

    decisions = _decision_index(portfolio_decision_result)
    valid_statuses = {status.value for status in TeacherDecisionStatus}
    status_counts: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for item in event_items:
        bundle_id = str(item.get("source_bundle_id") or "").strip()
        review_id = str(item.get("review_id") or "").strip()
        key = (bundle_id, review_id)
        if not bundle_id or not review_id or key in seen:
            raise TournamentTeacherReviewReleaseGateError("event review identity missing or duplicated")
        seen.add(key)
        decision = decisions.get(key)
        if decision is None:
            raise TournamentTeacherReviewReleaseGateError("event review item has no matching teacher decision")
        for field in ("event_id", "deal_id", "category"):
            if str(decision.get(field) or "") != str(item.get(field) or ""):
                raise TournamentTeacherReviewReleaseGateError(f"event review decision identity mismatch: {field}")
        status = str(decision.get("status") or "")
        if status not in valid_statuses:
            raise TournamentTeacherReviewReleaseGateError(f"unsupported teacher decision status: {status!r}")
        if decision.get("automatic_methodology_mapping_allowed") is not False:
            raise TournamentTeacherReviewReleaseGateError("automatic methodology mapping boundary was weakened")
        if decision.get("automatic_student_error_attribution_allowed") is not False:
            raise TournamentTeacherReviewReleaseGateError("automatic student-error attribution boundary was weakened")
        status_counts[status] += 1
        rows.append(
            {
                "source_bundle_id": bundle_id,
                "review_id": review_id,
                "deal_id": str(item.get("deal_id") or ""),
                "category": str(item.get("category") or ""),
                "status": status,
                "causal_link": "NOT_ESTABLISHED",
                "methodology_mapping": None,
                "student_error_attribution": None,
            }
        )

    unresolved = (
        status_counts[TeacherDecisionStatus.PENDING.value]
        + status_counts[TeacherDecisionStatus.NEEDS_CONTEXT.value]
    )
    confirmed = status_counts[TeacherDecisionStatus.CONFIRMED_TECHNICAL_RELEVANCE.value]
    dismissed = status_counts[TeacherDecisionStatus.DISMISSED.value]
    blockers: list[str] = []
    if unresolved:
        blockers.append("TEACHER_REVIEW_PORTFOLIO_UNRESOLVED")

    confirmed_in_report = [
        row
        for row in confirmed_report.get("confirmed_items", [])
        if isinstance(row, Mapping) and str(row.get("event_id") or "") == target_event
    ]
    if len(confirmed_in_report) != confirmed:
        raise TournamentTeacherReviewReleaseGateError("confirmed event count disagrees with portfolio longitudinal projection")

    rows.sort(key=lambda row: (row["deal_id"], row["category"], row["source_bundle_id"], row["review_id"]))
    return {
        "schema": "tournament-event-teacher-review-release-gate-v1",
        "normative_algorithm_version": "1.4",
        "portfolio_id": portfolio["portfolio_id"],
        "event_id": target_event,
        "review_item_count": len(rows),
        "status_counts": {status.value: int(status_counts.get(status.value, 0)) for status in TeacherDecisionStatus},
        "confirmed_technical_count": int(confirmed),
        "dismissed_count": int(dismissed),
        "unresolved_review_count": int(unresolved),
        "all_event_review_items_decided": unresolved == 0,
        "teacher_review_release_ready": not blockers,
        "release_blockers": blockers,
        "review_items": rows,
        "cross_bundle_review_identity_preserved": True,
        "cross_category_causal_collapse_allowed": False,
        "automatic_episode_scoring_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "causal_error_attribution_allowed": False,
        "interpretation": (
            "Every technical review item for this event, across every verified source bundle in the portfolio, "
            "must receive an explicit teacher decision before the event-level review gate can open. "
            "A confirmed technical item still requires separate explicit episode scoring before slide coverage."
        ),
    }
