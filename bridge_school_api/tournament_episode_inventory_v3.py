from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence


class TournamentEpisodeInventoryError(ValueError):
    pass


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _source_boards(source: Mapping[str, Any], event_id: str) -> tuple[str, list[int], dict[int, str]]:
    if source.get("schema") != "bridge-tournament-facts-v1":
        raise TournamentEpisodeInventoryError("unsupported tournament facts schema")
    tournament = source.get("tournament")
    if not isinstance(tournament, Mapping):
        raise TournamentEpisodeInventoryError("tournament metadata is required")
    provider_key = str(tournament.get("provider_native_key") or "").strip()
    if not provider_key:
        raise TournamentEpisodeInventoryError("provider_native_key is required")
    if f"event:{event_id}:" not in provider_key:
        raise TournamentEpisodeInventoryError("event_id does not match tournament provider identity")

    columns = source.get("columns")
    raw_rows = source.get("rows")
    if not isinstance(columns, Sequence) or isinstance(columns, (str, bytes)):
        raise TournamentEpisodeInventoryError("columns must be a sequence")
    if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, (str, bytes)):
        raise TournamentEpisodeInventoryError("rows must be a sequence")
    names = [str(value) for value in columns]
    if "board" not in names or "status" not in names or len(names) != len(set(names)):
        raise TournamentEpisodeInventoryError("facts columns are malformed")

    statuses: dict[int, str] = {}
    for raw in raw_rows:
        if not isinstance(raw, str):
            raise TournamentEpisodeInventoryError("facts rows must be pipe-delimited strings")
        values = raw.split("|")
        if len(values) != len(names):
            raise TournamentEpisodeInventoryError("facts row width does not match columns")
        row = dict(zip(names, values, strict=True))
        try:
            board = int(row["board"])
        except (TypeError, ValueError) as exc:
            raise TournamentEpisodeInventoryError("invalid board number") from exc
        if board <= 0 or board in statuses:
            raise TournamentEpisodeInventoryError("board numbers must be positive and unique")
        status = str(row["status"] or "").strip().lower()
        if status not in {"played", "average", "unplayed"}:
            raise TournamentEpisodeInventoryError(f"unsupported board status: {status!r}")
        statuses[board] = status

    if not statuses:
        raise TournamentEpisodeInventoryError("tournament facts contain no boards")
    played = sorted(board for board, status in statuses.items() if status == "played")
    return provider_key, played, statuses


def _dossier_items(dossier: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    if dossier.get("schema") != "tournament-teacher-review-dossier-v1":
        raise TournamentEpisodeInventoryError("unsupported teacher review dossier schema")
    for field in (
        "automatic_decisions_allowed",
        "automatic_methodology_mapping_allowed",
        "automatic_student_error_attribution_allowed",
        "cross_event_numeric_ranking_allowed",
    ):
        if dossier.get(field) is not False:
            raise TournamentEpisodeInventoryError(f"teacher-review boundary was weakened: {field}")
    items = dossier.get("items")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        raise TournamentEpisodeInventoryError("dossier items must be a sequence")
    return items


def _board_from_deal_id(event_id: str, deal_id: str) -> int:
    if not deal_id.startswith(event_id + ":"):
        raise TournamentEpisodeInventoryError("deal_id event identity mismatch")
    try:
        board = int(deal_id.rsplit(":", 1)[1])
    except (IndexError, ValueError) as exc:
        raise TournamentEpisodeInventoryError("deal_id must end with board number") from exc
    if board <= 0:
        raise TournamentEpisodeInventoryError("deal_id board number must be positive")
    return board


def build_evidence_episode_candidate_inventory(
    source: Mapping[str, Any],
    dossier: Mapping[str, Any],
    *,
    event_id: str,
) -> dict[str, Any]:
    """Enumerate evidence-bound technical review candidates without scoring them.

    A pending DDS/technical review item is not automatically a v1.4 teaching episode.
    This layer only proves which evidence candidates exist for the event. It does not
    assign impact, transferability, reliability, methodology, causality, or student error.
    """
    event_id = str(event_id).strip()
    if not event_id:
        raise TournamentEpisodeInventoryError("event_id is required")
    provider_key, played_boards, statuses = _source_boards(source, event_id)
    played_set = set(played_boards)
    queue_sha256 = str(dossier.get("queue_sha256") or "").strip()
    if len(queue_sha256) != 64:
        raise TournamentEpisodeInventoryError("dossier queue_sha256 is required")

    candidates: list[dict[str, Any]] = []
    seen_review_ids: set[str] = set()
    seen_identity: set[tuple[str, str]] = set()
    for raw in _dossier_items(dossier):
        if not isinstance(raw, Mapping):
            raise TournamentEpisodeInventoryError("dossier item must be a mapping")
        if str(raw.get("event_id") or "") != event_id:
            continue
        if raw.get("status") != "PENDING" or raw.get("teacher_decision_required") is not True:
            raise TournamentEpisodeInventoryError("candidate inventory accepts pending teacher-review items only")
        if raw.get("causal_link") != "NOT_ESTABLISHED":
            raise TournamentEpisodeInventoryError("causal boundary was weakened")
        if raw.get("automatic_methodology_mapping_allowed") is not False:
            raise TournamentEpisodeInventoryError("automatic methodology mapping was enabled")
        if raw.get("automatic_student_error_attribution_allowed") is not False:
            raise TournamentEpisodeInventoryError("automatic student-error attribution was enabled")
        if raw.get("methodology_mapping") is not None or raw.get("student_error_attribution") is not None:
            raise TournamentEpisodeInventoryError("pending dossier item contains pedagogical attribution")

        review_id = str(raw.get("review_id") or "").strip()
        deal_id = str(raw.get("deal_id") or "").strip()
        category = str(raw.get("category") or "").strip()
        if not review_id or not deal_id or not category:
            raise TournamentEpisodeInventoryError("review_id, deal_id and category are required")
        if review_id in seen_review_ids or (deal_id, category) in seen_identity:
            raise TournamentEpisodeInventoryError("duplicate teacher-review candidate identity")
        seen_review_ids.add(review_id)
        seen_identity.add((deal_id, category))

        board = _board_from_deal_id(event_id, deal_id)
        if board not in played_set:
            raise TournamentEpisodeInventoryError("teacher-review candidate must reference a played board")
        technical = raw.get("technical_finding")
        if not isinstance(technical, Mapping):
            raise TournamentEpisodeInventoryError("technical_finding is required")
        evidence = technical.get("evidence")
        if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)) or not evidence:
            raise TournamentEpisodeInventoryError("technical_finding evidence is required")

        queue_context = raw.get("queue_context")
        if not isinstance(queue_context, Mapping):
            raise TournamentEpisodeInventoryError("queue_context is required")
        technical_digest = _sha256(technical)
        candidate_id = "technical-review:" + _sha256(
            {"queue_sha256": queue_sha256, "review_id": review_id, "technical_finding_sha256": technical_digest}
        )[:24]
        candidates.append(
            {
                "candidate_id": candidate_id,
                "review_id": review_id,
                "event_id": event_id,
                "deal_id": deal_id,
                "board_number": board,
                "category": category,
                "candidate_kind": "TECHNICAL_REVIEW_CANDIDATE",
                "review_status": "PENDING_TEACHER_REVIEW",
                "technical_finding_sha256": technical_digest,
                "technical_repeat_key": technical.get("repeat_key"),
                "observed_outcome_context": dict(queue_context),
                "impact_score": None,
                "transferability_score": None,
                "reliability_score": None,
                "total_score": None,
                "coverage_tier": None,
                "deep_slide_required": None,
                "coverage_eligible": False,
                "methodology_mapping": None,
                "student_error_attribution": None,
                "causal_link": "NOT_ESTABLISHED",
            }
        )

    candidates.sort(key=lambda item: (int(item["board_number"]), str(item["review_id"])))
    by_board: dict[int, list[str]] = {board: [] for board in played_boards}
    for candidate in candidates:
        by_board[int(candidate["board_number"])].append(str(candidate["candidate_id"]))
    board_inventory = [
        {
            "board_number": board,
            "status": statuses[board],
            "technical_review_candidate_ids": by_board[board],
            "technical_review_candidate_count": len(by_board[board]),
        }
        for board in played_boards
    ]

    blockers = [
        "EXPLICIT_EPISODE_SCORING_NOT_AVAILABLE",
        "NON_DDS_EPISODE_COVERAGE_NOT_ESTABLISHED",
    ]
    return {
        "schema": "tournament-evidence-episode-candidate-inventory-v1",
        "normative_algorithm_version": "1.4",
        "event_id": event_id,
        "provider_native_key": provider_key,
        "queue_sha256": queue_sha256,
        "played_board_count": len(played_boards),
        "played_boards": played_boards,
        "technical_candidate_count": len(candidates),
        "technical_candidate_board_count": len({int(item["board_number"]) for item in candidates}),
        "technical_candidates": candidates,
        "boards": board_inventory,
        "evidence_candidate_inventory_complete": True,
        "v1_4_episode_inventory_complete": False,
        "coverage_episode_inputs": [],
        "automatic_episode_scoring_allowed": False,
        "automatic_transferability_judgment_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "release_ready": False,
        "release_blockers": blockers,
        "interpretation": (
            "This artifact exhaustively enumerates the current evidence-bound technical review candidates for the event. "
            "A candidate is not a confirmed teaching episode. No v1.4 impact/transferability/reliability score or slide tier "
            "is assigned until explicit evidence/teacher adjudication exists, and non-DDS episode coverage is not inferred."
        ),
    }


def coverage_episode_inputs(inventory: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return only explicitly coverage-eligible scored episodes; current candidate inventory must return none."""
    if inventory.get("schema") != "tournament-evidence-episode-candidate-inventory-v1":
        raise TournamentEpisodeInventoryError("unsupported candidate inventory schema")
    for field in (
        "automatic_episode_scoring_allowed",
        "automatic_transferability_judgment_allowed",
        "automatic_methodology_mapping_allowed",
        "automatic_student_error_attribution_allowed",
    ):
        if inventory.get(field) is not False:
            raise TournamentEpisodeInventoryError(f"candidate inventory boundary was weakened: {field}")
    if inventory.get("v1_4_episode_inventory_complete") is not False:
        raise TournamentEpisodeInventoryError("candidate inventory must not claim completed v1.4 episode adjudication")
    values = inventory.get("coverage_episode_inputs")
    if values != []:
        raise TournamentEpisodeInventoryError("pending technical candidates cannot enter coverage scoring")
    return []
