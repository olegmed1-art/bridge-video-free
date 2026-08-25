from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .tournament_real_sources_v3 import findings_29912, validate_29912_report_contract


class Tournament29912ScoringError(ValueError):
    pass


@dataclass(frozen=True)
class MPScaleEvidence:
    round_no: int
    field_size: int
    max_matchpoints_per_board: float
    boards_counted: int
    reported_session_score: float
    derived_session_percentage: float
    absolute_difference: float
    verified: bool


@dataclass(frozen=True)
class MPBoardOutcome29912:
    deal_id: str
    round_no: int
    board_number: int
    raw_pair_matchpoints: float
    max_matchpoints_per_board: float
    observed_pair_percentage: float
    gap_to_neutral_percentage_points: float
    source_consistency_ok: bool


@dataclass(frozen=True)
class MPContext29912:
    event_id: str
    session_scales: tuple[MPScaleEvidence, ...]
    outcomes: tuple[MPBoardOutcome29912, ...]
    cross_session_observed_mean_percentage: float
    total_below_neutral_mass_percentage_points: float
    counterfactual_mean_if_all_below_neutral_were_neutral: float
    interpretation: str


@dataclass(frozen=True)
class FindingMPContext29912:
    deal_id: str
    category: str
    technical_trick_loss: float | None
    observed_pair_percentage: float
    observed_gap_to_neutral_percentage_points: float
    source_consistency_ok: bool
    causal_link: str
    dd_to_mp_conversion_available: bool
    student_error_attribution_allowed: bool


def _finite(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or value is None:
        raise Tournament29912ScoringError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise Tournament29912ScoringError(f"{field} must be numeric") from exc
    if result != result or result in (float("inf"), float("-inf")):
        raise Tournament29912ScoringError(f"{field} must be finite")
    return result


def _positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or value is None:
        raise Tournament29912ScoringError(f"{field} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise Tournament29912ScoringError(f"{field} must be a positive integer") from exc
    if parsed <= 0 or float(value) != float(parsed):
        raise Tournament29912ScoringError(f"{field} must be a positive integer")
    return parsed


def derive_session_mp_context(
    *,
    round_no: int,
    tournament: Mapping[str, Any],
    boards: Sequence[Mapping[str, Any]],
    score_tolerance: float = 0.11,
) -> tuple[MPScaleEvidence, tuple[MPBoardOutcome29912, ...]]:
    """Verify the raw MP scale against the independently reported session score.

    The conversion is accepted only when the standard 2*(field_size-1) board maximum
    reproduces the source-reported session percentage within display-rounding tolerance.
    This is an evidence gate, not a bridge-methodology inference.
    """
    if not boards:
        raise Tournament29912ScoringError("session must contain boards")
    field_size = _positive_int(tournament.get("field_size"), field="field_size")
    if field_size < 2:
        raise Tournament29912ScoringError("field_size must be at least 2")
    reported = _finite(tournament.get("session_score"), field="session_score")
    if not 0.0 <= reported <= 100.0:
        raise Tournament29912ScoringError("session_score must be within [0, 100]")

    max_mp = float(2 * (field_size - 1))
    rows: list[MPBoardOutcome29912] = []
    total_raw = 0.0
    seen: set[int] = set()
    for board in boards:
        board_no = _positive_int(board.get("board"), field="board")
        if board_no in seen:
            raise Tournament29912ScoringError(f"duplicate board in round {round_no}: {board_no}")
        seen.add(board_no)
        mp = _finite(board.get("pair_matchpoints"), field=f"round {round_no} board {board_no} pair_matchpoints")
        if not 0.0 <= mp <= max_mp:
            raise Tournament29912ScoringError(
                f"round {round_no} board {board_no} matchpoints {mp} outside [0, {max_mp}]"
            )
        pct = 100.0 * mp / max_mp
        consistency = board.get("source_consistency")
        source_ok = isinstance(consistency, Mapping) and consistency.get("ok") is True
        rows.append(
            MPBoardOutcome29912(
                deal_id=f"29912:round-{round_no}:{board_no}",
                round_no=round_no,
                board_number=board_no,
                raw_pair_matchpoints=mp,
                max_matchpoints_per_board=max_mp,
                observed_pair_percentage=pct,
                gap_to_neutral_percentage_points=max(0.0, 50.0 - pct),
                source_consistency_ok=source_ok,
            )
        )
        total_raw += mp

    derived = 100.0 * total_raw / (len(rows) * max_mp)
    difference = abs(derived - reported)
    verified = difference <= score_tolerance
    if not verified:
        raise Tournament29912ScoringError(
            f"round {round_no} raw MP scale does not reproduce source session score: "
            f"derived={derived:.6f}, reported={reported:.6f}, diff={difference:.6f}"
        )
    return (
        MPScaleEvidence(
            round_no=round_no,
            field_size=field_size,
            max_matchpoints_per_board=max_mp,
            boards_counted=len(rows),
            reported_session_score=reported,
            derived_session_percentage=derived,
            absolute_difference=difference,
            verified=True,
        ),
        tuple(rows),
    )


def build_29912_mp_context(report: Mapping[str, Any]) -> MPContext29912:
    validate_29912_report_contract(report)
    scales: list[MPScaleEvidence] = []
    outcomes: list[MPBoardOutcome29912] = []
    for session in report["sessions"]:
        if not isinstance(session, Mapping):
            raise Tournament29912ScoringError("session must be a mapping")
        round_no = _positive_int(session.get("round"), field="round")
        tournament = session.get("tournament")
        boards = session.get("boards")
        if not isinstance(tournament, Mapping):
            raise Tournament29912ScoringError("session tournament metadata missing")
        if not isinstance(boards, Sequence) or isinstance(boards, (str, bytes)):
            raise Tournament29912ScoringError("session boards must be a sequence")
        scale, board_rows = derive_session_mp_context(
            round_no=round_no,
            tournament=tournament,
            boards=boards,
        )
        scales.append(scale)
        outcomes.extend(board_rows)

    if len(outcomes) != 100:
        raise Tournament29912ScoringError(f"expected 100 scored boards, got {len(outcomes)}")
    mean_pct = sum(item.observed_pair_percentage for item in outcomes) / len(outcomes)
    gap_mass = sum(item.gap_to_neutral_percentage_points for item in outcomes)
    all_neutral = mean_pct + gap_mass / len(outcomes)
    return MPContext29912(
        event_id="29912",
        session_scales=tuple(sorted(scales, key=lambda x: x.round_no)),
        outcomes=tuple(sorted(outcomes, key=lambda x: (x.round_no, x.board_number))),
        cross_session_observed_mean_percentage=mean_pct,
        total_below_neutral_mass_percentage_points=gap_mass,
        counterfactual_mean_if_all_below_neutral_were_neutral=all_neutral,
        interpretation=(
            "Board MP percentages are exposed only because the preserved raw matchpoints, "
            "field size and independently reported session score pass an exact scale-consistency "
            "gate. Neutral-50% counterfactuals are arithmetic outcome context, not DDS3-to-MP "
            "conversion, causal attribution, or estimates of pedagogically recoverable result."
        ),
    )


def join_29912_findings_with_mp_context(
    report: Mapping[str, Any],
    context: MPContext29912,
) -> tuple[FindingMPContext29912, ...]:
    outcomes = {item.deal_id: item for item in context.outcomes}
    joined: list[FindingMPContext29912] = []
    for finding in findings_29912(report):
        outcome = outcomes.get(finding.deal_id)
        if outcome is None:
            raise Tournament29912ScoringError(f"finding missing scoring outcome: {finding.deal_id}")
        joined.append(
            FindingMPContext29912(
                deal_id=finding.deal_id,
                category=finding.category,
                technical_trick_loss=finding.trick_loss,
                observed_pair_percentage=outcome.observed_pair_percentage,
                observed_gap_to_neutral_percentage_points=outcome.gap_to_neutral_percentage_points,
                source_consistency_ok=outcome.source_consistency_ok,
                causal_link="NOT_ESTABLISHED",
                dd_to_mp_conversion_available=False,
                student_error_attribution_allowed=False,
            )
        )
    return tuple(
        sorted(
            joined,
            key=lambda item: (
                item.observed_gap_to_neutral_percentage_points,
                abs(float(item.technical_trick_loss or 0.0)),
                item.deal_id,
            ),
            reverse=True,
        )
    )


def serialize_29912_mp_context(
    context: MPContext29912,
    joined: Sequence[FindingMPContext29912],
) -> dict[str, Any]:
    return {
        "schema": "tournament-29912-mp-outcome-context-v1",
        "event_id": context.event_id,
        "session_scales": [asdict(item) for item in context.session_scales],
        "outcomes": [asdict(item) for item in context.outcomes],
        "cross_session_observed_mean_percentage": context.cross_session_observed_mean_percentage,
        "total_below_neutral_mass_percentage_points": context.total_below_neutral_mass_percentage_points,
        "counterfactual_mean_if_all_below_neutral_were_neutral": (
            context.counterfactual_mean_if_all_below_neutral_were_neutral
        ),
        "technical_finding_context": [asdict(item) for item in joined],
        "interpretation": context.interpretation,
        "mp_scale_verified_against_reported_session_scores": True,
        "dd_to_mp_conversion_available": False,
        "causal_error_attribution_allowed": False,
        "pedagogically_recoverable_result_estimated": False,
        "student_error_attribution_allowed": False,
    }
