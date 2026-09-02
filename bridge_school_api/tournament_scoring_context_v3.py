from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .tournament_analyzer_v3 import AnalysisFinding, TournamentAnalysis
from .tournament_real_sources_v3 import EXPECTED_30041_PROVIDER_KEY, normalize_30041_facts


class TournamentScoringContextError(ValueError):
    pass


@dataclass(frozen=True)
class MPBoardOutcome:
    deal_id: str
    board_number: int
    status: str
    observed_pair_percentage: float
    centered_from_neutral: float
    gap_to_neutral: float
    final_percentage_uplift_if_neutral: float
    counterfactual_final_percentage_if_neutral: float


@dataclass(frozen=True)
class MPTournamentContext:
    event_id: str
    session_id: str
    provider_native_key: str
    final_percentage: float
    rank: int
    field_size: int
    counted_results: int
    neutral_reference_percentage: float
    outcomes: tuple[MPBoardOutcome, ...]
    total_below_neutral_mass: float
    counterfactual_final_percentage_if_all_below_neutral_were_neutral: float
    interpretation: str


@dataclass(frozen=True)
class FindingMPContext:
    deal_id: str
    category: str
    technical_trick_loss: float | None
    observed_pair_percentage: float | None
    observed_gap_to_neutral: float | None
    final_percentage_uplift_if_neutral: float | None
    causal_link: str
    dd_to_mp_conversion_available: bool


def _number(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or value is None:
        raise TournamentScoringContextError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise TournamentScoringContextError(f"{field} must be numeric") from exc
    if result != result or result in (float("inf"), float("-inf")):
        raise TournamentScoringContextError(f"{field} must be finite")
    return result


def _integer(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or value is None:
        raise TournamentScoringContextError(f"{field} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise TournamentScoringContextError(f"{field} must be an integer") from exc
    if str(value).strip() not in {str(parsed), f"{parsed}.0"} and not isinstance(value, int):
        raise TournamentScoringContextError(f"{field} must be an integer")
    return parsed


def _split_row(columns: Sequence[str], raw: Any) -> dict[str, str]:
    values = str(raw).split("|")
    if len(values) != len(columns):
        raise TournamentScoringContextError(
            f"source row has {len(values)} fields, expected {len(columns)}"
        )
    return dict(zip(columns, values, strict=True))


def build_30041_mp_context(source: Mapping[str, Any]) -> MPTournamentContext:
    """Build an outcome-only MP context for the exact audited 30041 source.

    This layer does not convert DDS3 trick differences into matchpoints and does not
    claim that a below-average board is a player mistake. It only quantifies observed
    board percentages and simple neutral-50% counterfactuals from the official source.
    """
    batch = normalize_30041_facts(source)
    tournament = source.get("tournament")
    if not isinstance(tournament, Mapping):
        raise TournamentScoringContextError("missing tournament metadata")
    if tournament.get("provider_native_key") != EXPECTED_30041_PROVIDER_KEY:
        raise TournamentScoringContextError("unexpected provider identity")
    if tournament.get("scoring") != "MP":
        raise TournamentScoringContextError("30041 scoring must remain MP")

    final_percentage = _number(tournament.get("final_percentage"), field="final_percentage")
    if not 0.0 <= final_percentage <= 100.0:
        raise TournamentScoringContextError("final_percentage must be within [0, 100]")
    rank = _integer(tournament.get("rank"), field="rank")
    field_size = _integer(tournament.get("field_size"), field="field_size")
    counted_results = _integer(tournament.get("counted_results"), field="counted_results")
    if counted_results <= 0 or rank <= 0 or field_size <= 0 or rank > field_size:
        raise TournamentScoringContextError("invalid tournament rank/count metadata")

    columns = source.get("columns")
    rows = source.get("rows")
    if not isinstance(columns, Sequence) or isinstance(columns, (str, bytes)):
        raise TournamentScoringContextError("columns must be a sequence")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise TournamentScoringContextError("rows must be a sequence")

    outcomes: list[MPBoardOutcome] = []
    neutral = 50.0
    for raw in rows:
        row = _split_row(list(columns), raw)
        pct_raw = row.get("pair_percentage", "").strip()
        if not pct_raw:
            continue
        status = row.get("status", "").strip()
        if status not in {"played", "average"}:
            raise TournamentScoringContextError(
                f"percentage present for non-counted status: {status!r}"
            )
        board_number = _integer(row.get("board"), field="board")
        pct = _number(pct_raw, field=f"board {board_number} pair_percentage")
        if not 0.0 <= pct <= 100.0:
            raise TournamentScoringContextError("pair_percentage must be within [0, 100]")
        centered = pct - neutral
        gap = max(0.0, neutral - pct)
        uplift = gap / counted_results
        outcomes.append(
            MPBoardOutcome(
                deal_id=f"{batch.event_id}:{batch.session_id}:{board_number}",
                board_number=board_number,
                status=status,
                observed_pair_percentage=pct,
                centered_from_neutral=centered,
                gap_to_neutral=gap,
                final_percentage_uplift_if_neutral=uplift,
                counterfactual_final_percentage_if_neutral=final_percentage + uplift,
            )
        )

    if len(outcomes) != counted_results:
        raise TournamentScoringContextError(
            f"counted percentage rows {len(outcomes)} != counted_results {counted_results}"
        )
    total_gap = sum(item.gap_to_neutral for item in outcomes)
    all_neutral = final_percentage + total_gap / counted_results
    return MPTournamentContext(
        event_id=batch.event_id,
        session_id=batch.session_id,
        provider_native_key=EXPECTED_30041_PROVIDER_KEY,
        final_percentage=final_percentage,
        rank=rank,
        field_size=field_size,
        counted_results=counted_results,
        neutral_reference_percentage=neutral,
        outcomes=tuple(outcomes),
        total_below_neutral_mass=total_gap,
        counterfactual_final_percentage_if_all_below_neutral_were_neutral=all_neutral,
        interpretation=(
            "Observed MP outcome context only. Neutral 50% counterfactuals are arithmetic "
            "what-if values, not DDS3-to-MP conversions, causal error attributions, or "
            "estimates of pedagogically recoverable result."
        ),
    )


def join_findings_with_mp_context(
    analysis: TournamentAnalysis,
    context: MPTournamentContext,
) -> tuple[FindingMPContext, ...]:
    if analysis.event_id != context.event_id:
        raise TournamentScoringContextError("analysis/context event mismatch")
    outcomes = {item.deal_id: item for item in context.outcomes}
    joined: list[FindingMPContext] = []
    for finding in analysis.findings:
        outcome = outcomes.get(finding.deal_id)
        joined.append(
            FindingMPContext(
                deal_id=finding.deal_id,
                category=finding.category,
                technical_trick_loss=finding.trick_loss,
                observed_pair_percentage=(
                    outcome.observed_pair_percentage if outcome is not None else None
                ),
                observed_gap_to_neutral=(outcome.gap_to_neutral if outcome is not None else None),
                final_percentage_uplift_if_neutral=(
                    outcome.final_percentage_uplift_if_neutral if outcome is not None else None
                ),
                causal_link="NOT_ESTABLISHED",
                dd_to_mp_conversion_available=False,
            )
        )
    return tuple(
        sorted(
            joined,
            key=lambda item: (
                item.final_percentage_uplift_if_neutral or 0.0,
                abs(float(item.technical_trick_loss or 0.0)),
                item.deal_id,
            ),
            reverse=True,
        )
    )


def serialize_mp_context(
    context: MPTournamentContext,
    joined: Sequence[FindingMPContext],
) -> dict[str, Any]:
    return {
        "schema": "tournament-mp-outcome-context-v1",
        "event_id": context.event_id,
        "session_id": context.session_id,
        "provider_native_key": context.provider_native_key,
        "final_percentage": context.final_percentage,
        "rank": context.rank,
        "field_size": context.field_size,
        "counted_results": context.counted_results,
        "neutral_reference_percentage": context.neutral_reference_percentage,
        "total_below_neutral_mass": context.total_below_neutral_mass,
        "counterfactual_final_percentage_if_all_below_neutral_were_neutral": (
            context.counterfactual_final_percentage_if_all_below_neutral_were_neutral
        ),
        "interpretation": context.interpretation,
        "outcomes": [asdict(item) for item in context.outcomes],
        "technical_finding_context": [asdict(item) for item in joined],
        "dd_to_mp_conversion_available": False,
        "causal_error_attribution_allowed": False,
        "pedagogically_recoverable_result_estimated": False,
    }
