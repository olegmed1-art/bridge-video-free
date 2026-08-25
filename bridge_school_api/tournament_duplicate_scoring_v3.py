from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


class DuplicateScoringError(ValueError):
    pass


_CONTRACT_RE = re.compile(r"^([1-7])(NT|[CDHS])(XX|X)?$")
_SEATS = {"N", "E", "S", "W"}


@dataclass(frozen=True)
class ParsedContract:
    level: int
    strain: str
    multiplier: int
    doubled: str


@dataclass(frozen=True)
class ScoreValidation:
    board_number: int
    published_score: int
    recalculated_score: int
    matches: bool
    target_side: str
    declarer_side: str
    contract: str
    declarer: str
    result_delta: int
    vulnerability: str


def parse_contract(contract: str) -> ParsedContract:
    text = str(contract).strip().upper().replace(" ", "")
    match = _CONTRACT_RE.fullmatch(text)
    if not match:
        raise DuplicateScoringError(f"unsupported contract: {contract!r}")
    marker = match.group(3) or ""
    multiplier = 1 if not marker else (2 if marker == "X" else 4)
    return ParsedContract(
        level=int(match.group(1)),
        strain=match.group(2),
        multiplier=multiplier,
        doubled=marker,
    )


def normalize_side(value: str) -> str:
    text = str(value).strip().upper().replace("–", "-").replace("—", "-").replace(" ", "")
    if text in {"NS", "N-S"}:
        return "NS"
    if text in {"EW", "E-W"}:
        return "EW"
    raise DuplicateScoringError(f"unsupported pair side: {value!r}")


def side_of_seat(seat: str) -> str:
    normalized = str(seat).strip().upper()
    if normalized not in _SEATS:
        raise DuplicateScoringError(f"unsupported declarer seat: {seat!r}")
    return "NS" if normalized in {"N", "S"} else "EW"


def is_vulnerable(vulnerability: str, side: str) -> bool:
    text = str(vulnerability).strip().upper().replace("–", "-").replace("—", "-").replace(" ", "")
    if text in {"NONE", "LOVE", "-", ""}:
        return False
    if text in {"BOTH", "ALL"}:
        return True
    if text in {"NS", "N-S"}:
        return side == "NS"
    if text in {"EW", "E-W"}:
        return side == "EW"
    raise DuplicateScoringError(f"unsupported vulnerability: {vulnerability!r}")


def _undoubled_contract_points(level: int, strain: str) -> int:
    if strain in {"C", "D"}:
        return level * 20
    if strain in {"H", "S"}:
        return level * 30
    if strain == "NT":
        return 40 + (level - 1) * 30
    raise DuplicateScoringError(f"unsupported strain: {strain!r}")


def _undoubled_overtrick_points(strain: str) -> int:
    if strain in {"C", "D"}:
        return 20
    if strain in {"H", "S", "NT"}:
        return 30
    raise DuplicateScoringError(f"unsupported strain: {strain!r}")


def _doubled_undertrick_penalty(undertricks: int, vulnerable: bool) -> int:
    if undertricks <= 0:
        return 0
    if vulnerable:
        return 200 + max(0, undertricks - 1) * 300
    if undertricks == 1:
        return 100
    if undertricks == 2:
        return 300
    if undertricks == 3:
        return 500
    return 500 + (undertricks - 3) * 300


def duplicate_score_declarer(
    contract: str,
    *,
    result_delta: int,
    vulnerable: bool,
) -> int:
    """Return duplicate score from declarer's perspective.

    ``result_delta`` is over/under tricks relative to the contract: 0 made exactly,
    positive for overtricks, negative for undertricks.
    """
    if isinstance(result_delta, bool):
        raise DuplicateScoringError("boolean is not a valid result delta")
    try:
        delta = int(result_delta)
    except (TypeError, ValueError) as exc:
        raise DuplicateScoringError(f"invalid result delta: {result_delta!r}") from exc
    parsed = parse_contract(contract)

    if delta < 0:
        undertricks = -delta
        if parsed.multiplier == 1:
            return -(undertricks * (100 if vulnerable else 50))
        doubled_penalty = _doubled_undertrick_penalty(undertricks, vulnerable)
        if parsed.multiplier == 4:
            doubled_penalty *= 2
        return -doubled_penalty

    undoubled_points = _undoubled_contract_points(parsed.level, parsed.strain)
    contract_points = undoubled_points * parsed.multiplier
    score = contract_points

    if contract_points >= 100:
        score += 500 if vulnerable else 300
    else:
        score += 50

    if parsed.level == 6:
        score += 750 if vulnerable else 500
    elif parsed.level == 7:
        score += 1500 if vulnerable else 1000

    if parsed.multiplier == 2:
        score += 50
    elif parsed.multiplier == 4:
        score += 100

    if delta > 0:
        if parsed.multiplier == 1:
            score += delta * _undoubled_overtrick_points(parsed.strain)
        else:
            per_overtrick = 200 if vulnerable else 100
            if parsed.multiplier == 4:
                per_overtrick *= 2
            score += delta * per_overtrick
    return score


def score_for_target_pair(
    contract: str,
    *,
    declarer: str,
    result_delta: int,
    vulnerability: str,
    target_side: str,
) -> int:
    declarer_side = side_of_seat(declarer)
    target = normalize_side(target_side)
    score = duplicate_score_declarer(
        contract,
        result_delta=result_delta,
        vulnerable=is_vulnerable(vulnerability, declarer_side),
    )
    return score if target == declarer_side else -score


def validate_published_score(
    *,
    board_number: int,
    contract: str,
    declarer: str,
    result_delta: int,
    vulnerability: str,
    target_side: str,
    published_score: int,
) -> ScoreValidation:
    if isinstance(board_number, bool) or int(board_number) <= 0:
        raise DuplicateScoringError("board_number must be positive")
    if isinstance(published_score, bool):
        raise DuplicateScoringError("boolean is not a valid published score")
    recalculated = score_for_target_pair(
        contract,
        declarer=declarer,
        result_delta=result_delta,
        vulnerability=vulnerability,
        target_side=target_side,
    )
    published = int(published_score)
    return ScoreValidation(
        board_number=int(board_number),
        published_score=published,
        recalculated_score=recalculated,
        matches=published == recalculated,
        target_side=normalize_side(target_side),
        declarer_side=side_of_seat(declarer),
        contract=parse_contract(contract) and str(contract).strip().upper(),
        declarer=str(declarer).strip().upper(),
        result_delta=int(result_delta),
        vulnerability=str(vulnerability),
    )


def _rows_from_facts(source: Mapping[str, Any]) -> list[dict[str, str]]:
    if source.get("schema") != "bridge-tournament-facts-v1":
        raise DuplicateScoringError("unsupported tournament facts schema")
    columns = source.get("columns")
    rows = source.get("rows")
    if not isinstance(columns, Sequence) or isinstance(columns, (str, bytes)):
        raise DuplicateScoringError("facts columns are malformed")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise DuplicateScoringError("facts rows are malformed")
    out: list[dict[str, str]] = []
    for raw in rows:
        if not isinstance(raw, str):
            raise DuplicateScoringError("facts row must be pipe-delimited text")
        values = raw.split("|")
        if len(values) != len(columns):
            raise DuplicateScoringError("facts row/column length mismatch")
        out.append(dict(zip((str(x) for x in columns), values, strict=True)))
    return out


def validate_tournament_fact_scores(source: Mapping[str, Any]) -> dict[str, Any]:
    """Independently recalculate all ordinary played-contract scores in a facts extract.

    Administrative average/unplayed rows are preserved outside this recalculation, as
    required by the tournament-analysis v1.4 source/scoring boundary.
    """
    validations: list[ScoreValidation] = []
    skipped: list[dict[str, Any]] = []
    for row in _rows_from_facts(source):
        status = str(row.get("status") or "").strip().lower()
        board = int(row["board"])
        if status != "played":
            skipped.append({"board_number": board, "status": status, "reason": "ADMINISTRATIVE_OR_UNPLAYED"})
            continue
        required = ("contract", "declarer", "result_delta", "vulnerability", "pair_direction", "pair_score")
        missing = [field for field in required if row.get(field) in (None, "")]
        if missing:
            raise DuplicateScoringError(f"played board {board} lacks scoring fields: {missing}")
        validations.append(
            validate_published_score(
                board_number=board,
                contract=row["contract"],
                declarer=row["declarer"],
                result_delta=int(row["result_delta"]),
                vulnerability=row["vulnerability"],
                target_side=row["pair_direction"],
                published_score=int(row["pair_score"]),
            )
        )

    mismatches = [item for item in validations if not item.matches]
    return {
        "schema": "tournament-duplicate-score-validation-v1",
        "scoring_method": "DUPLICATE_CONTRACT_SCORE",
        "played_scores_checked": len(validations),
        "skipped_nonplayed": len(skipped),
        "all_published_scores_match": not mismatches,
        "mismatches": [item.__dict__ for item in mismatches],
        "checks": [item.__dict__ for item in validations],
        "skipped": skipped,
        "administrative_results_recalculated": False,
    }
