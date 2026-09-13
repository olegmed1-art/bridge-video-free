from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .tournament_real_sources_v3 import findings_29912, validate_29912_report_contract


class Tournament29912ScoringError(ValueError):
    pass


@dataclass(frozen=True)
class SessionAdditivityEvidence:
    round_no: int
    boards_counted: int
    skipped_rows_count: int
    analyzed_board_sum: float
    skipped_numeric_sum: float
    known_source_sum: float
    reported_session_score: float
    unexplained_remainder: float
    absolute_difference: float
    verified: bool


@dataclass(frozen=True)
class BoardScoreOutcome29912:
    deal_id: str
    round_no: int
    board_number: int
    source_pair_score_contribution: float
    negative_score_contribution: float
    source_consistency_ok: bool


@dataclass(frozen=True)
class SourceScoreContext29912:
    event_id: str
    session_additivity: tuple[SessionAdditivityEvidence, ...]
    outcomes: tuple[BoardScoreOutcome29912, ...]
    analyzed_board_score_contribution_sum: float
    negative_score_contribution_mass: float
    interpretation: str


@dataclass(frozen=True)
class FindingScoreContext29912:
    deal_id: str
    category: str
    technical_trick_loss: float | None
    source_pair_score_contribution: float
    negative_score_contribution: float
    source_consistency_ok: bool
    causal_link: str
    dd_to_score_conversion_available: bool
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


def _skipped_score_values(skipped_rows: Sequence[Mapping[str, Any]]) -> tuple[float, int]:
    total = 0.0
    numeric_count = 0
    for index, item in enumerate(skipped_rows):
        if not isinstance(item, Mapping):
            raise Tournament29912ScoringError(f"skipped row {index} must be a mapping")
        raw = item.get("row")
        if raw is None:
            continue
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise Tournament29912ScoringError(f"skipped row {index} raw row must be a sequence")
        if len(raw) <= 4 or raw[4] in (None, ""):
            continue
        try:
            value = _finite(raw[4], field=f"skipped row {index} score contribution")
        except Tournament29912ScoringError:
            # The historical extractor deliberately preserved adjusted/unplayed raw rows.
            # A non-numeric value contributes no verified score mass and is not guessed.
            continue
        total += value
        numeric_count += 1
    return total, numeric_count


def derive_session_score_context(
    *,
    round_no: int,
    tournament: Mapping[str, Any],
    boards: Sequence[Mapping[str, Any]],
    skipped_rows: Sequence[Mapping[str, Any]] = (),
    score_tolerance: float = 1e-9,
) -> tuple[SessionAdditivityEvidence, tuple[BoardScoreOutcome29912, ...]]:
    """Expose the preserved signed source score without inventing a percentage scale.

    Historical evidence proves that ``pair_matchpoints`` contains signed additive
    score contributions (negative values are valid).  It does *not* prove a
    board-percentage conversion.  We therefore preserve the signed values as-is and
    use session additivity only as a provenance/consistency check.
    """
    round_no = _positive_int(round_no, field="round")
    if not boards:
        raise Tournament29912ScoringError("session must contain boards")
    reported = _finite(tournament.get("session_score"), field="session_score")
    if score_tolerance < 0:
        raise Tournament29912ScoringError("score_tolerance must be non-negative")

    rows: list[BoardScoreOutcome29912] = []
    analyzed_sum = 0.0
    seen: set[int] = set()
    for board in boards:
        if not isinstance(board, Mapping):
            raise Tournament29912ScoringError("board must be a mapping")
        board_no = _positive_int(board.get("board"), field="board")
        if board_no in seen:
            raise Tournament29912ScoringError(f"duplicate board in round {round_no}: {board_no}")
        seen.add(board_no)
        score = _finite(
            board.get("pair_matchpoints"),
            field=f"round {round_no} board {board_no} pair_matchpoints",
        )
        consistency = board.get("source_consistency")
        source_ok = isinstance(consistency, Mapping) and consistency.get("ok") is True
        rows.append(
            BoardScoreOutcome29912(
                deal_id=f"29912:round-{round_no}:{board_no}",
                round_no=round_no,
                board_number=board_no,
                source_pair_score_contribution=score,
                negative_score_contribution=max(0.0, -score),
                source_consistency_ok=source_ok,
            )
        )
        analyzed_sum += score

    skipped_sum, _ = _skipped_score_values(skipped_rows)
    known_sum = analyzed_sum + skipped_sum
    remainder = reported - known_sum
    difference = abs(remainder)
    evidence = SessionAdditivityEvidence(
        round_no=round_no,
        boards_counted=len(rows),
        skipped_rows_count=len(skipped_rows),
        analyzed_board_sum=analyzed_sum,
        skipped_numeric_sum=skipped_sum,
        known_source_sum=known_sum,
        reported_session_score=reported,
        unexplained_remainder=remainder,
        absolute_difference=difference,
        verified=difference <= score_tolerance,
    )
    return evidence, tuple(rows)


def _validate_source_facts_for_session(
    *,
    round_no: int,
    session: Mapping[str, Any],
    facts: Mapping[str, Any],
) -> Sequence[Mapping[str, Any]]:
    source = facts.get("source")
    if not isinstance(source, Mapping) or int(source.get("round", -1)) != round_no:
        raise Tournament29912ScoringError(f"source facts round mismatch for round {round_no}")
    source_boards = facts.get("boards")
    if not isinstance(source_boards, Sequence) or isinstance(source_boards, (str, bytes)):
        raise Tournament29912ScoringError(f"source facts boards missing for round {round_no}")
    by_board: dict[int, float] = {}
    for item in source_boards:
        if not isinstance(item, Mapping):
            raise Tournament29912ScoringError("source fact board must be a mapping")
        board_no = _positive_int(item.get("board"), field="source board")
        if board_no in by_board:
            raise Tournament29912ScoringError(f"duplicate source board in round {round_no}: {board_no}")
        by_board[board_no] = _finite(
            item.get("pair_matchpoints"),
            field=f"source round {round_no} board {board_no} pair_matchpoints",
        )
    for board in session["boards"]:
        board_no = _positive_int(board.get("board"), field="board")
        if board_no not in by_board:
            raise Tournament29912ScoringError(f"DDS3 board missing from source facts: round {round_no} board {board_no}")
        observed = _finite(board.get("pair_matchpoints"), field="DDS3 pair_matchpoints")
        if abs(observed - by_board[board_no]) > 1e-9:
            raise Tournament29912ScoringError(
                f"source score mismatch: round {round_no} board {board_no}: {observed} != {by_board[board_no]}"
            )
    skipped = facts.get("skipped_rows", ())
    if not isinstance(skipped, Sequence) or isinstance(skipped, (str, bytes)):
        raise Tournament29912ScoringError(f"skipped_rows must be a sequence for round {round_no}")
    return skipped


def build_29912_source_score_context(
    report: Mapping[str, Any],
    source_facts_by_round: Mapping[int, Mapping[str, Any]],
) -> SourceScoreContext29912:
    validate_29912_report_contract(report)
    additivity: list[SessionAdditivityEvidence] = []
    outcomes: list[BoardScoreOutcome29912] = []
    for session in report["sessions"]:
        if not isinstance(session, Mapping):
            raise Tournament29912ScoringError("session must be a mapping")
        round_no = _positive_int(session.get("round"), field="round")
        tournament = session.get("tournament")
        boards = session.get("boards")
        facts = source_facts_by_round.get(round_no)
        if not isinstance(tournament, Mapping):
            raise Tournament29912ScoringError("session tournament metadata missing")
        if not isinstance(boards, Sequence) or isinstance(boards, (str, bytes)):
            raise Tournament29912ScoringError("session boards must be a sequence")
        if not isinstance(facts, Mapping):
            raise Tournament29912ScoringError(f"missing exact source facts for round {round_no}")
        skipped = _validate_source_facts_for_session(round_no=round_no, session=session, facts=facts)
        evidence, board_rows = derive_session_score_context(
            round_no=round_no,
            tournament=tournament,
            boards=boards,
            skipped_rows=skipped,
        )
        additivity.append(evidence)
        outcomes.extend(board_rows)

    if len(outcomes) != 100:
        raise Tournament29912ScoringError(f"expected 100 scored boards, got {len(outcomes)}")
    return SourceScoreContext29912(
        event_id="29912",
        session_additivity=tuple(sorted(additivity, key=lambda x: x.round_no)),
        outcomes=tuple(sorted(outcomes, key=lambda x: (x.round_no, x.board_number))),
        analyzed_board_score_contribution_sum=sum(x.source_pair_score_contribution for x in outcomes),
        negative_score_contribution_mass=sum(x.negative_score_contribution for x in outcomes),
        interpretation=(
            "Preserved pair_matchpoints are exposed as signed source score contributions. "
            "Negative and positive values are valid. Four sessions reproduce the published "
            "session score exactly from known preserved rows; any non-zero unexplained remainder "
            "is retained as an evidence gap. No percentage, 50% baseline, DDS3-to-score conversion, "
            "causal attribution, or pedagogically recoverable result is inferred."
        ),
    )


def join_29912_findings_with_source_score_context(
    report: Mapping[str, Any],
    context: SourceScoreContext29912,
) -> tuple[FindingScoreContext29912, ...]:
    outcomes = {item.deal_id: item for item in context.outcomes}
    joined: list[FindingScoreContext29912] = []
    for finding in findings_29912(report):
        outcome = outcomes.get(finding.deal_id)
        if outcome is None:
            raise Tournament29912ScoringError(f"finding missing score outcome: {finding.deal_id}")
        if not outcome.source_consistency_ok:
            raise Tournament29912ScoringError(f"technical finding references source-inconsistent deal: {finding.deal_id}")
        joined.append(
            FindingScoreContext29912(
                deal_id=finding.deal_id,
                category=finding.category,
                technical_trick_loss=finding.trick_loss,
                source_pair_score_contribution=outcome.source_pair_score_contribution,
                negative_score_contribution=outcome.negative_score_contribution,
                source_consistency_ok=True,
                causal_link="NOT_ESTABLISHED",
                dd_to_score_conversion_available=False,
                student_error_attribution_allowed=False,
            )
        )
    return tuple(
        sorted(
            joined,
            key=lambda item: (
                item.negative_score_contribution,
                abs(float(item.technical_trick_loss or 0.0)),
                item.deal_id,
            ),
            reverse=True,
        )
    )


def serialize_29912_source_score_context(
    context: SourceScoreContext29912,
    joined: Sequence[FindingScoreContext29912],
) -> dict[str, Any]:
    verified_rounds = [x.round_no for x in context.session_additivity if x.verified]
    unverified_rounds = [x.round_no for x in context.session_additivity if not x.verified]
    return {
        "schema": "tournament-29912-source-score-context-v1",
        "event_id": context.event_id,
        "session_additivity": [asdict(item) for item in context.session_additivity],
        "outcomes": [asdict(item) for item in context.outcomes],
        "analyzed_board_score_contribution_sum": context.analyzed_board_score_contribution_sum,
        "negative_score_contribution_mass": context.negative_score_contribution_mass,
        "technical_finding_context": [asdict(item) for item in joined],
        "interpretation": context.interpretation,
        "source_score_additivity_verified": not unverified_rounds,
        "source_score_additivity_verified_rounds": verified_rounds,
        "source_score_additivity_unverified_rounds": unverified_rounds,
        "percentage_conversion_available": False,
        "dd_to_score_conversion_available": False,
        "causal_error_attribution_allowed": False,
        "pedagogically_recoverable_result_estimated": False,
        "student_error_attribution_allowed": False,
    }
