from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Mapping, Sequence

from .tournament_duplicate_scoring_v3 import normalize_side, side_of_seat
from .tournament_structural_validation_v3 import opening_leader, validate_tournament_structure


class TournamentOpeningLeadDDS3Error(ValueError):
    pass


_CONTRACT_RE = re.compile(r"^[1-7](NT|[CDHS])(?:XX|X)?$")
_SEATS = ("N", "E", "S", "W")


def _rows(source: Mapping[str, Any]) -> list[dict[str, str]]:
    if source.get("schema") != "bridge-tournament-facts-v1":
        raise TournamentOpeningLeadDDS3Error("unsupported tournament facts schema")
    columns = source.get("columns")
    raw_rows = source.get("rows")
    if not isinstance(columns, Sequence) or isinstance(columns, (str, bytes)):
        raise TournamentOpeningLeadDDS3Error("facts columns must be a sequence")
    if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, (str, bytes)):
        raise TournamentOpeningLeadDDS3Error("facts rows must be a sequence")
    names = [str(value) for value in columns]
    out: list[dict[str, str]] = []
    for raw in raw_rows:
        if not isinstance(raw, str):
            raise TournamentOpeningLeadDDS3Error("facts row must be pipe-delimited text")
        values = raw.split("|")
        if len(values) != len(names):
            raise TournamentOpeningLeadDDS3Error("facts row width does not match columns")
        out.append(dict(zip(names, values, strict=True)))
    return out


def _contract_trump(contract: str) -> str:
    text = str(contract).strip().upper().replace(" ", "")
    match = _CONTRACT_RE.fullmatch(text)
    if not match:
        raise TournamentOpeningLeadDDS3Error(f"unsupported contract: {contract!r}")
    return match.group(1)


def _position_pbn(row: Mapping[str, str]) -> str:
    hands = [str(row.get(seat) or "").strip().upper() for seat in _SEATS]
    if not all(hands):
        raise TournamentOpeningLeadDDS3Error("all four source hands are required")
    return "N:" + " ".join(hands)


def _position_sha256(position: Mapping[str, Any]) -> str:
    raw = json.dumps(position, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _validate_solver_result(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if result.get("engine") != "DDS3" or result.get("fallback_used") is not False:
        raise TournamentOpeningLeadDDS3Error("non-canonical or fallback DDS result rejected")
    if result.get("operation") != "position_all_moves":
        raise TournamentOpeningLeadDDS3Error("unexpected DDS3 operation")
    moves = result.get("moves")
    if not isinstance(moves, Sequence) or isinstance(moves, (str, bytes)) or not moves:
        raise TournamentOpeningLeadDDS3Error("DDS3 returned no opening-lead moves")
    normalized: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for move in moves:
        if not isinstance(move, Mapping):
            raise TournamentOpeningLeadDDS3Error("DDS3 move must be a mapping")
        card = str(move.get("card") or "").strip().upper().replace("10", "T")
        if not card or card in seen:
            raise TournamentOpeningLeadDDS3Error("DDS3 move card must be present and unique")
        seen.add(card)
        regret = move.get("regret")
        tricks = move.get("tricks")
        if isinstance(regret, bool) or isinstance(tricks, bool):
            raise TournamentOpeningLeadDDS3Error("DDS3 move values must be integers")
        if int(regret) < 0:
            raise TournamentOpeningLeadDDS3Error("DDS3 regret cannot be negative")
        normalized.append(move)
    return normalized


def analyze_opening_leads(
    source: Mapping[str, Any],
    *,
    solve_position: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> dict[str, Any]:
    """Analyze actual opening leads with DDS3 position/all-moves.

    The result is a double-dummy technical comparison only. A positive lead regret is
    a teacher-review candidate when the observed opening leader belongs to the target
    pair; it is not automatically a student error, a causal explanation of the board
    result, or a methodology statement.
    """
    structure = validate_tournament_structure(source)
    if not structure.get("all_structural_checks_pass"):
        raise TournamentOpeningLeadDDS3Error("source structure must pass before opening-lead DDS3 analysis")
    rows = _rows(source)

    results: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    engine_versions: set[str] = set()
    for row in rows:
        if str(row.get("status") or "").strip().lower() != "played":
            continue
        board = int(row["board"])
        declarer = str(row.get("declarer") or "").strip().upper()
        actual_lead = str(row.get("opening_lead") or "").strip().upper().replace("10", "T")
        target_side = normalize_side(str(row.get("pair_direction") or ""))
        leader = opening_leader(declarer)
        leader_side = side_of_seat(leader)
        position = {
            "pbn": _position_pbn(row),
            "trump": _contract_trump(str(row.get("contract") or "")),
            "first": leader,
            "current_trick": [],
        }
        solved = solve_position(position)
        moves = _validate_solver_result(solved)
        if solved.get("engine_version"):
            engine_versions.add(str(solved["engine_version"]))
        if len(engine_versions) > 1:
            raise TournamentOpeningLeadDDS3Error("DDS3 engine version changed inside one tournament run")

        actual = next(
            (move for move in moves if str(move.get("card") or "").strip().upper().replace("10", "T") == actual_lead),
            None,
        )
        if actual is None:
            raise TournamentOpeningLeadDDS3Error(
                f"board {board}: actual opening lead {actual_lead!r} not present among DDS3 legal moves"
            )
        regret = int(actual["regret"])
        best_tricks = int(solved.get("best_tricks"))
        actual_tricks = int(actual["tricks"])
        target_pair_made_lead = leader_side == target_side
        item = {
            "board_number": board,
            "deal_id": f"{source['tournament']['provider_native_key']}:{board}",
            "contract": str(row.get("contract") or ""),
            "declarer": declarer,
            "opening_leader": leader,
            "opening_leader_side": leader_side,
            "target_pair_side": target_side,
            "target_pair_made_opening_lead": target_pair_made_lead,
            "actual_opening_lead": actual_lead,
            "actual_lead_tricks_for_opening_side": actual_tricks,
            "best_tricks_for_opening_side": best_tricks,
            "lead_regret_tricks": regret,
            "regret_class": "0" if regret == 0 else "1" if regret == 1 else "2+",
            "actual_lead_dd_optimal": regret == 0,
            "optimal_opening_leads": list(solved.get("optimal_cards") or []),
            "position_sha256": _position_sha256(position),
            "engine": "DDS3",
            "engine_version": solved.get("engine_version"),
            "fallback_used": False,
            "evidence_kind": "DDS_FACT",
            "causal_error_attribution": "NOT_ESTABLISHED",
            "student_error_attribution": None,
            "methodology_mapping": None,
        }
        results.append(item)
        if target_pair_made_lead and regret > 0:
            candidates.append(
                {
                    **item,
                    "candidate_kind": "DDS3_OPENING_LEAD_REGRET",
                    "teacher_review_required": True,
                    "coverage_eligible": False,
                }
            )

    return {
        "schema": "tournament-opening-lead-dds3-v1",
        "normative_algorithm_version": "1.4",
        "provider_native_key": source["tournament"]["provider_native_key"],
        "played_leads_analyzed": len(results),
        "target_pair_opening_leads_analyzed": sum(bool(row["target_pair_made_opening_lead"]) for row in results),
        "dd_optimal_actual_leads": sum(bool(row["actual_lead_dd_optimal"]) for row in results),
        "positive_regret_actual_leads": sum(int(row["lead_regret_tricks"]) > 0 for row in results),
        "target_pair_positive_regret_candidates": len(candidates),
        "engine": "DDS3",
        "engine_versions": sorted(engine_versions),
        "fallback_used": False,
        "results": results,
        "teacher_review_candidates": candidates,
        "automatic_student_error_attribution_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "interpretation": (
            "Lead regret is DDS3 double-dummy trick loss for the opening side relative to the best legal opening lead. "
            "It is a technical review signal only and does not establish that the target student made an error."
        ),
    }
