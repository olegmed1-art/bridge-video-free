from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence


class TournamentTeacherReviewQueueError(ValueError):
    pass


@dataclass(frozen=True)
class TeacherReviewItem:
    event_id: str
    deal_id: str
    category: str
    technical_trick_loss: float | None
    outcome_scale: str
    observed_outcome: float | None
    adverse_outcome_magnitude: float
    causal_link: str
    student_error_attribution_allowed: bool
    teacher_review_required: bool


@dataclass(frozen=True)
class TeacherReviewLane:
    event_id: str
    outcome_scale: str
    ranking_scope: str
    items: tuple[TeacherReviewItem, ...]


@dataclass(frozen=True)
class CrossEventTeacherReviewQueue:
    lanes: tuple[TeacherReviewLane, ...]
    cross_event_numeric_ranking_allowed: bool
    causal_error_attribution_allowed: bool
    student_error_attribution_allowed: bool
    interpretation: str


def _finite_optional(value: Any, *, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TournamentTeacherReviewQueueError(f"{field} must be numeric or null")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise TournamentTeacherReviewQueueError(f"{field} must be numeric or null") from exc
    if result != result or result in (float("inf"), float("-inf")):
        raise TournamentTeacherReviewQueueError(f"{field} must be finite")
    return result


def _technical_items(context: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    items = context.get("technical_finding_context")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        raise TournamentTeacherReviewQueueError("technical_finding_context must be a sequence")
    return items


def _build_30041_lane(context: Mapping[str, Any], *, limit: int) -> TeacherReviewLane:
    if context.get("schema") != "tournament-mp-outcome-context-v1" or str(context.get("event_id")) != "30041":
        raise TournamentTeacherReviewQueueError("unexpected 30041 scoring context")
    if context.get("dd_to_mp_conversion_available") is not False or context.get("causal_error_attribution_allowed") is not False:
        raise TournamentTeacherReviewQueueError("30041 evidence boundary was weakened")
    rows: list[TeacherReviewItem] = []
    for raw in _technical_items(context):
        if not isinstance(raw, Mapping) or raw.get("causal_link") != "NOT_ESTABLISHED":
            raise TournamentTeacherReviewQueueError("30041 technical row lacks causal boundary")
        deal_id = str(raw.get("deal_id") or "")
        category = str(raw.get("category") or "")
        if not deal_id or not category:
            raise TournamentTeacherReviewQueueError("30041 technical row missing identity/category")
        trick = _finite_optional(raw.get("technical_trick_loss"), field="technical_trick_loss")
        pct = _finite_optional(raw.get("observed_pair_percentage"), field="observed_pair_percentage")
        gap = _finite_optional(raw.get("observed_gap_to_neutral"), field="observed_gap_to_neutral")
        if gap is not None and gap < 0:
            raise TournamentTeacherReviewQueueError("30041 neutral gap cannot be negative")
        rows.append(
            TeacherReviewItem(
                event_id="30041",
                deal_id=deal_id,
                category=category,
                technical_trick_loss=trick,
                outcome_scale="MP_PERCENTAGE",
                observed_outcome=pct,
                adverse_outcome_magnitude=float(gap or 0.0),
                causal_link="NOT_ESTABLISHED",
                student_error_attribution_allowed=False,
                teacher_review_required=True,
            )
        )
    ranked = sorted(
        rows,
        key=lambda x: (x.adverse_outcome_magnitude, abs(float(x.technical_trick_loss or 0.0)), x.deal_id),
        reverse=True,
    )
    return TeacherReviewLane("30041", "MP_PERCENTAGE", "WITHIN_EVENT_ONLY", tuple(ranked[:limit]))


def _build_29912_lane(context: Mapping[str, Any], *, limit: int) -> TeacherReviewLane:
    if context.get("schema") != "tournament-29912-source-score-context-v1" or str(context.get("event_id")) != "29912":
        raise TournamentTeacherReviewQueueError("unexpected 29912 scoring context")
    if context.get("percentage_conversion_available") is not False or context.get("dd_to_score_conversion_available") is not False:
        raise TournamentTeacherReviewQueueError("29912 score-conversion boundary was weakened")
    if context.get("causal_error_attribution_allowed") is not False or context.get("student_error_attribution_allowed") is not False:
        raise TournamentTeacherReviewQueueError("29912 attribution boundary was weakened")
    rows: list[TeacherReviewItem] = []
    for raw in _technical_items(context):
        if not isinstance(raw, Mapping) or raw.get("causal_link") != "NOT_ESTABLISHED":
            raise TournamentTeacherReviewQueueError("29912 technical row lacks causal boundary")
        if raw.get("source_consistency_ok") is not True:
            raise TournamentTeacherReviewQueueError("29912 source-inconsistent row entered review queue")
        deal_id = str(raw.get("deal_id") or "")
        category = str(raw.get("category") or "")
        if not deal_id or not category:
            raise TournamentTeacherReviewQueueError("29912 technical row missing identity/category")
        trick = _finite_optional(raw.get("technical_trick_loss"), field="technical_trick_loss")
        score = _finite_optional(raw.get("source_pair_score_contribution"), field="source_pair_score_contribution")
        adverse = _finite_optional(raw.get("negative_score_contribution"), field="negative_score_contribution")
        if adverse is None or adverse < 0:
            raise TournamentTeacherReviewQueueError("29912 negative score contribution must be non-negative")
        rows.append(
            TeacherReviewItem(
                event_id="29912",
                deal_id=deal_id,
                category=category,
                technical_trick_loss=trick,
                outcome_scale="SIGNED_SOURCE_SCORE_CONTRIBUTION",
                observed_outcome=score,
                adverse_outcome_magnitude=adverse,
                causal_link="NOT_ESTABLISHED",
                student_error_attribution_allowed=False,
                teacher_review_required=True,
            )
        )
    ranked = sorted(
        rows,
        key=lambda x: (x.adverse_outcome_magnitude, abs(float(x.technical_trick_loss or 0.0)), x.deal_id),
        reverse=True,
    )
    return TeacherReviewLane(
        "29912",
        "SIGNED_SOURCE_SCORE_CONTRIBUTION",
        "WITHIN_EVENT_ONLY",
        tuple(ranked[:limit]),
    )


def build_cross_event_teacher_review_queue(
    context_30041: Mapping[str, Any],
    context_29912: Mapping[str, Any],
    *,
    per_event_limit: int = 10,
) -> CrossEventTeacherReviewQueue:
    if isinstance(per_event_limit, bool) or not isinstance(per_event_limit, int) or per_event_limit <= 0:
        raise TournamentTeacherReviewQueueError("per_event_limit must be a positive integer")
    lane_30041 = _build_30041_lane(context_30041, limit=per_event_limit)
    lane_29912 = _build_29912_lane(context_29912, limit=per_event_limit)
    return CrossEventTeacherReviewQueue(
        lanes=(lane_30041, lane_29912),
        cross_event_numeric_ranking_allowed=False,
        causal_error_attribution_allowed=False,
        student_error_attribution_allowed=False,
        interpretation=(
            "Teacher-review prioritization only. Each event is ranked inside its own observed outcome scale. "
            "MP percentages from event 30041 and signed source score contributions from event 29912 are not "
            "numerically comparable, so no cross-event score is formed. Technical DDS3 evidence does not by "
            "itself establish a player error, cause, teaching category, or pedagogically recoverable result."
        ),
    )


def serialize_teacher_review_queue(queue: CrossEventTeacherReviewQueue) -> dict[str, Any]:
    return {
        "schema": "tournament-teacher-review-queue-v1",
        "lanes": [
            {
                "event_id": lane.event_id,
                "outcome_scale": lane.outcome_scale,
                "ranking_scope": lane.ranking_scope,
                "items": [asdict(item) for item in lane.items],
            }
            for lane in queue.lanes
        ],
        "cross_event_numeric_ranking_allowed": queue.cross_event_numeric_ranking_allowed,
        "causal_error_attribution_allowed": queue.causal_error_attribution_allowed,
        "student_error_attribution_allowed": queue.student_error_attribution_allowed,
        "interpretation": queue.interpretation,
    }
