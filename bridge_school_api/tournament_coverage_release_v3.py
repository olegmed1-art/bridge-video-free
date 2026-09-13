from __future__ import annotations

from typing import Any, Mapping, Sequence


class TournamentCoverageError(ValueError):
    pass


def _rows(source: Mapping[str, Any]) -> list[dict[str, str]]:
    if source.get("schema") != "bridge-tournament-facts-v1":
        raise TournamentCoverageError("unsupported tournament facts schema")
    columns = source.get("columns")
    raw_rows = source.get("rows")
    if not isinstance(columns, Sequence) or isinstance(columns, (str, bytes)):
        raise TournamentCoverageError("columns must be a sequence")
    if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, (str, bytes)):
        raise TournamentCoverageError("rows must be a sequence")
    names = [str(value) for value in columns]
    if len(names) != len(set(names)):
        raise TournamentCoverageError("duplicate columns")
    parsed: list[dict[str, str]] = []
    for raw in raw_rows:
        if not isinstance(raw, str):
            raise TournamentCoverageError("facts row must be pipe-delimited text")
        values = raw.split("|")
        if len(values) != len(names):
            raise TournamentCoverageError("facts row width does not match columns")
        parsed.append(dict(zip(names, values, strict=True)))
    if not parsed:
        raise TournamentCoverageError("tournament facts contain no rows")
    return parsed


def _score(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise TournamentCoverageError(f"{name} must be an integer 0..2")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise TournamentCoverageError(f"{name} must be an integer 0..2") from exc
    if result not in {0, 1, 2}:
        raise TournamentCoverageError(f"{name} must be within 0..2")
    return result


def _normalize_episode(episode: Mapping[str, Any], played_boards: set[int]) -> dict[str, Any]:
    if not isinstance(episode, Mapping):
        raise TournamentCoverageError("episode must be a mapping")
    episode_id = str(episode.get("episode_id") or "").strip()
    if not episode_id:
        raise TournamentCoverageError("episode_id is required")
    try:
        board_number = int(episode.get("board_number"))
    except (TypeError, ValueError) as exc:
        raise TournamentCoverageError("episode board_number is invalid") from exc
    if board_number not in played_boards:
        raise TournamentCoverageError("scored episodes are allowed only on played boards")
    provenance = episode.get("score_provenance")
    if not isinstance(provenance, Mapping) or not provenance:
        raise TournamentCoverageError("score_provenance is required; coverage layer does not invent episode scores")

    impact = _score(episode.get("impact_score"), "impact_score")
    transferability = _score(episode.get("transferability_score"), "transferability_score")
    reliability = _score(episode.get("reliability_score"), "reliability_score")
    total = impact + transferability + reliability
    if total >= 4:
        tier = "SIGNIFICANT_DEEP_SLIDE"
    elif total >= 2:
        tier = "STANDARD_BOARD_ANALYSIS"
    else:
        tier = "BRIEF_REVIEW"

    return {
        "episode_id": episode_id,
        "board_number": board_number,
        "impact_score": impact,
        "transferability_score": transferability,
        "reliability_score": reliability,
        "total_score": total,
        "tier": tier,
        "score_provenance": dict(provenance),
        "automatic_scoring": False,
    }


def build_coverage_manifest(
    source: Mapping[str, Any],
    *,
    episodes: Sequence[Mapping[str, Any]] = (),
    episode_inventory_complete: bool,
) -> dict[str, Any]:
    """Build the v1.4 slide-coverage plan without inventing episode scores.

    Every played board receives a base slide. A scored episode with total 4..6
    receives a separate adjacent deep slide. Scores are supplied by an upstream
    evidence/methodology layer and must include provenance; this module never
    creates bridge-methodology judgements itself.
    """
    rows = _rows(source)
    tournament = source.get("tournament")
    if not isinstance(tournament, Mapping):
        raise TournamentCoverageError("tournament metadata is required")
    provider_key = str(tournament.get("provider_native_key") or "").strip()
    if not provider_key:
        raise TournamentCoverageError("provider_native_key is required")

    boards: list[dict[str, Any]] = []
    played_boards: set[int] = set()
    seen: set[int] = set()
    status_counts = {"played": 0, "average": 0, "unplayed": 0}
    for row in rows:
        try:
            board = int(row.get("board", ""))
        except (TypeError, ValueError) as exc:
            raise TournamentCoverageError("invalid board number") from exc
        if board <= 0 or board in seen:
            raise TournamentCoverageError("board numbers must be positive and unique")
        seen.add(board)
        status = str(row.get("status") or "").strip().lower()
        if status not in status_counts:
            raise TournamentCoverageError(f"unsupported board status: {status!r}")
        status_counts[status] += 1
        if status == "played":
            played_boards.add(board)
        boards.append(
            {
                "board_number": board,
                "status": status,
                "base_slide_required": status == "played",
                "student_decision_statistics_allowed": status == "played",
                "administrative_or_unplayed": status != "played",
                "episodes": [],
                "planned_slide_keys": [],
            }
        )

    normalized: list[dict[str, Any]] = []
    episode_ids: set[str] = set()
    for episode in episodes:
        item = _normalize_episode(episode, played_boards)
        if item["episode_id"] in episode_ids:
            raise TournamentCoverageError("episode_id values must be unique")
        episode_ids.add(item["episode_id"])
        normalized.append(item)

    by_board = {entry["board_number"]: entry for entry in boards}
    for episode in normalized:
        by_board[episode["board_number"]]["episodes"].append(episode)

    expected_slide_keys = ["deck-title", "deck-overview"]
    significant_count = 0
    for entry in boards:
        if not entry["base_slide_required"]:
            continue
        board = entry["board_number"]
        base_key = f"board-{board}-base"
        entry["planned_slide_keys"].append(base_key)
        expected_slide_keys.append(base_key)
        significant = sorted(
            (episode for episode in entry["episodes"] if episode["tier"] == "SIGNIFICANT_DEEP_SLIDE"),
            key=lambda episode: episode["episode_id"],
        )
        for index, episode in enumerate(significant, start=1):
            slide_key = f"board-{board}-deep-{index}"
            episode["required_separate_slide_key"] = slide_key
            entry["planned_slide_keys"].append(slide_key)
            expected_slide_keys.append(slide_key)
            significant_count += 1
    expected_slide_keys.append("deck-final")

    all_played_have_base_plan = all(by_board[board]["planned_slide_keys"] for board in played_boards)
    significant_have_separate = all(
        episode.get("required_separate_slide_key")
        for episode in normalized
        if episode["tier"] == "SIGNIFICANT_DEEP_SLIDE"
    )
    blockers: list[str] = []
    if not all_played_have_base_plan:
        blockers.append("PLAYED_BOARD_MISSING_BASE_SLIDE_PLAN")
    if not significant_have_separate:
        blockers.append("SIGNIFICANT_EPISODE_MISSING_DEEP_SLIDE_PLAN")
    if not episode_inventory_complete:
        blockers.append("EPISODE_INVENTORY_NOT_COMPLETE")

    return {
        "schema": "tournament-coverage-manifest-v1",
        "normative_algorithm_version": "1.4",
        "provider_native_key": provider_key,
        "status_counts": status_counts,
        "played_boards": sorted(played_boards),
        "boards": boards,
        "episodes": normalized,
        "episode_inventory_complete": bool(episode_inventory_complete),
        "automatic_episode_scoring_allowed": False,
        "significant_episode_count": significant_count,
        "expected_slide_keys": expected_slide_keys,
        "planned_deck_slide_count": len(expected_slide_keys),
        "gates": {
            "all_played_have_base_slide_plan": all_played_have_base_plan,
            "significant_episodes_have_separate_slide": significant_have_separate,
            "episode_inventory_complete": bool(episode_inventory_complete),
        },
        "coverage_plan_release_ready": not blockers,
        "release_blockers": blockers,
    }


def validate_rendered_slide_coverage(
    manifest: Mapping[str, Any],
    actual_slide_keys: Sequence[str],
) -> dict[str, Any]:
    if manifest.get("schema") != "tournament-coverage-manifest-v1":
        raise TournamentCoverageError("unsupported coverage manifest schema")
    if isinstance(actual_slide_keys, (str, bytes)) or not isinstance(actual_slide_keys, Sequence):
        raise TournamentCoverageError("actual_slide_keys must be a sequence")
    actual = [str(value) for value in actual_slide_keys]
    if len(actual) != len(set(actual)):
        raise TournamentCoverageError("actual slide keys must be unique")
    expected = [str(value) for value in manifest.get("expected_slide_keys", [])]
    missing = [value for value in expected if value not in actual]
    extra = [value for value in actual if value not in expected]
    order_match = actual == expected
    episode_inventory_complete = bool(manifest.get("episode_inventory_complete"))
    gate_pass = not missing and not extra and order_match and episode_inventory_complete
    return {
        "schema": "tournament-slide-coverage-validation-v1",
        "expected_slide_count": len(expected),
        "actual_slide_count": len(actual),
        "missing_slide_keys": missing,
        "extra_slide_keys": extra,
        "order_matches_plan": order_match,
        "episode_inventory_complete": episode_inventory_complete,
        "export_coverage_gate_pass": gate_pass,
    }


def build_release_gate(
    *,
    preanalysis_gate: Mapping[str, Any],
    coverage_manifest: Mapping[str, Any],
    mp_availability: Mapping[str, Any],
    rendered_slide_keys: Sequence[str] | None = None,
    visual_qa_pass: bool | None = None,
) -> dict[str, Any]:
    """Combine v1.4 hard stops into a final-report release checkpoint.

    Missing full traveller or actual auction/play are explicit limitations, not
    automatic publication blockers when the report stays within observable facts.
    Coverage and final visual QA are mandatory before export.
    """
    if preanalysis_gate.get("schema") != "tournament-preanalysis-gate-v1":
        raise TournamentCoverageError("unsupported preanalysis gate schema")
    if coverage_manifest.get("schema") != "tournament-coverage-manifest-v1":
        raise TournamentCoverageError("unsupported coverage manifest schema")
    if mp_availability.get("schema") != "tournament-mp-recalculation-availability-v1":
        raise TournamentCoverageError("unsupported MP availability schema")

    blockers = list(preanalysis_gate.get("hard_stop_conditions") or [])
    if not preanalysis_gate.get("facts_only_analysis_ready"):
        blockers.append("PREANALYSIS_NOT_READY")
    if not coverage_manifest.get("coverage_plan_release_ready"):
        blockers.extend(str(value) for value in coverage_manifest.get("release_blockers") or [])

    coverage_validation = None
    if rendered_slide_keys is None:
        blockers.append("RENDERED_SLIDE_COVERAGE_NOT_PROVIDED")
    else:
        coverage_validation = validate_rendered_slide_coverage(coverage_manifest, rendered_slide_keys)
        if not coverage_validation["export_coverage_gate_pass"]:
            blockers.append("RENDERED_SLIDE_COVERAGE_FAILED")

    if visual_qa_pass is not True:
        blockers.append("VISUAL_QA_NOT_PASSED")

    mp_status = str(mp_availability.get("status") or "")
    limitations = list(preanalysis_gate.get("limitations") or [])
    if mp_status == "OFFICIAL_PERCENTAGE_NOT_INDEPENDENTLY_RECALCULATED":
        limitations.append("FULL_TRAVELLER_ABSENT_OFFICIAL_PERCENTAGE_RETAINED")
    elif mp_status == "TRAVELLER_AVAILABLE_RECALCULATION_REQUIRED":
        blockers.append("TRAVELLER_PRESENT_MP_RECALCULATION_STILL_REQUIRED")

    blockers = sorted(set(blockers))
    limitations = sorted(set(str(value) for value in limitations))
    return {
        "schema": "tournament-v1.4-release-gate-v1",
        "run_id": preanalysis_gate.get("run_id"),
        "provider_native_key": preanalysis_gate.get("tournament", {}).get("provider_native_key"),
        "technical_analysis_ready": bool(preanalysis_gate.get("facts_only_analysis_ready")),
        "full_causal_replay_ready": bool(preanalysis_gate.get("full_causal_replay_ready")),
        "full_traveller_available": bool(mp_availability.get("full_traveller_available")),
        "coverage_plan_ready": bool(coverage_manifest.get("coverage_plan_release_ready")),
        "rendered_coverage_checked": coverage_validation is not None,
        "visual_qa_pass": visual_qa_pass is True,
        "final_report_release_ready": not blockers,
        "hard_stop_conditions": blockers,
        "limitations": limitations,
        "coverage_validation": coverage_validation,
        "mp_recalculation_status": mp_status,
        "automatic_student_error_attribution_allowed": False,
        "automatic_methodology_invention_allowed": False,
    }
