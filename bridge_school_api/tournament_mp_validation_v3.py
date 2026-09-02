from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


class MatchpointValidationError(ValueError):
    pass


@dataclass(frozen=True)
class MatchpointResult:
    target_score: float
    comparisons: int
    lower_results: int
    equal_results: int
    higher_results: int
    matchpoints: float
    top: float
    percentage: float


def calculate_matchpoints(target_score: float, comparison_scores: Sequence[float]) -> MatchpointResult:
    """Calculate pair MP using v1.4: 2 per worse score, 1 per equal score.

    comparison_scores excludes the target result itself. Scores must already be
    expressed from the target pair's perspective.
    """
    if isinstance(comparison_scores, (str, bytes)) or not isinstance(comparison_scores, Sequence):
        raise MatchpointValidationError("comparison_scores must be a sequence")
    if not comparison_scores:
        raise MatchpointValidationError("at least one comparison score is required")
    try:
        target = float(target_score)
        scores = [float(value) for value in comparison_scores]
    except (TypeError, ValueError) as exc:
        raise MatchpointValidationError("scores must be numeric") from exc

    lower = sum(score < target for score in scores)
    equal = sum(score == target for score in scores)
    higher = sum(score > target for score in scores)
    comparisons = len(scores)
    mp = 2.0 * lower + 1.0 * equal
    top = 2.0 * comparisons
    percentage = 100.0 * mp / top
    return MatchpointResult(
        target_score=target,
        comparisons=comparisons,
        lower_results=lower,
        equal_results=equal,
        higher_results=higher,
        matchpoints=mp,
        top=top,
        percentage=percentage,
    )


def validate_published_mp_percentage(
    *,
    target_score: float,
    comparison_scores: Sequence[float],
    published_percentage: float,
    tolerance: float = 0.051,
) -> dict[str, Any]:
    if tolerance < 0:
        raise MatchpointValidationError("tolerance must be non-negative")
    result = calculate_matchpoints(target_score, comparison_scores)
    published = float(published_percentage)
    delta = result.percentage - published
    return {
        "schema": "tournament-mp-board-validation-v1",
        "target_score": result.target_score,
        "comparisons": result.comparisons,
        "lower_results": result.lower_results,
        "equal_results": result.equal_results,
        "higher_results": result.higher_results,
        "matchpoints": result.matchpoints,
        "top": result.top,
        "recalculated_percentage": result.percentage,
        "published_percentage": published,
        "percentage_delta": delta,
        "matches_within_tolerance": abs(delta) <= tolerance,
        "formula": "MP = 2*worse + 1*equal; top = 2*comparisons; pct = 100*MP/top",
    }


def assess_mp_recalculation_availability(source: Mapping[str, Any]) -> dict[str, Any]:
    """Determine whether the current facts contain a full traveller per board.

    The audited 30041 derivative contains only the target pair's score/percentage,
    not field result distributions. It must therefore remain official-but-not-
    independently-recalculated instead of synthesising comparisons.
    """
    if source.get("schema") != "bridge-tournament-facts-v1":
        raise MatchpointValidationError("unsupported tournament facts schema")
    tournament = source.get("tournament")
    if not isinstance(tournament, Mapping):
        raise MatchpointValidationError("tournament metadata is required")
    scoring = str(tournament.get("scoring") or "").strip().upper()
    if scoring != "MP":
        return {
            "schema": "tournament-mp-recalculation-availability-v1",
            "scoring_method": scoring or None,
            "applicable": False,
            "full_traveller_available": False,
            "independent_mp_recalculation_allowed": False,
            "status": "NOT_APPLICABLE_NON_MP",
        }

    # A future source adapter may provide an explicit, normalized traveller block.
    traveller = source.get("traveller")
    full_available = isinstance(traveller, Mapping) and bool(traveller)
    if full_available:
        board_entries = traveller.get("boards")
        full_available = isinstance(board_entries, Sequence) and not isinstance(board_entries, (str, bytes)) and bool(board_entries)

    if not full_available:
        return {
            "schema": "tournament-mp-recalculation-availability-v1",
            "scoring_method": "MP",
            "applicable": True,
            "full_traveller_available": False,
            "independent_mp_recalculation_allowed": False,
            "status": "OFFICIAL_PERCENTAGE_NOT_INDEPENDENTLY_RECALCULATED",
            "reason": "current evidence lacks full per-board field result distributions",
            "forbidden_shortcut": "do not derive field comparisons from target pair percentage or DDS3",
        }

    return {
        "schema": "tournament-mp-recalculation-availability-v1",
        "scoring_method": "MP",
        "applicable": True,
        "full_traveller_available": True,
        "independent_mp_recalculation_allowed": True,
        "status": "TRAVELLER_AVAILABLE_RECALCULATION_REQUIRED",
    }
