from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence


class TournamentEpisodeScoringIntakeError(ValueError):
    pass


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def build_episode_scoring_template(inventory: Mapping[str, Any]) -> dict[str, Any]:
    if inventory.get("schema") != "tournament-evidence-episode-candidate-inventory-v1":
        raise TournamentEpisodeScoringIntakeError("unsupported episode candidate inventory schema")
    if inventory.get("evidence_candidate_inventory_complete") is not True:
        raise TournamentEpisodeScoringIntakeError("candidate inventory must be complete")
    for field in (
        "automatic_episode_scoring_allowed",
        "automatic_transferability_judgment_allowed",
        "automatic_methodology_mapping_allowed",
        "automatic_student_error_attribution_allowed",
    ):
        if inventory.get(field) is not False:
            raise TournamentEpisodeScoringIntakeError(f"candidate inventory boundary was weakened: {field}")
    candidates = inventory.get("technical_candidates")
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
        raise TournamentEpisodeScoringIntakeError("technical_candidates must be a sequence")

    inventory_sha256 = _sha256(inventory)
    rows = []
    seen = set()
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise TournamentEpisodeScoringIntakeError("candidate must be a mapping")
        candidate_id = str(candidate.get("candidate_id") or "")
        if not candidate_id or candidate_id in seen:
            raise TournamentEpisodeScoringIntakeError("candidate_id must be unique")
        seen.add(candidate_id)
        if candidate.get("review_status") != "PENDING_TEACHER_REVIEW":
            raise TournamentEpisodeScoringIntakeError("scoring template accepts pending candidates only")
        if candidate.get("coverage_eligible") is not False:
            raise TournamentEpisodeScoringIntakeError("unreviewed candidate cannot already be coverage eligible")
        rows.append(
            {
                "candidate_id": candidate_id,
                "review_id": candidate.get("review_id"),
                "event_id": candidate.get("event_id"),
                "deal_id": candidate.get("deal_id"),
                "board_number": candidate.get("board_number"),
                "category": candidate.get("category"),
                "candidate_sha256": _sha256(candidate),
                "explicit_episode_adjudication": False,
                "impact_score": None,
                "transferability_score": None,
                "reliability_score": None,
                "score_actor": None,
                "score_provenance": None,
                "status": "PENDING_SCORING",
            }
        )
    return {
        "schema": "tournament-episode-scoring-intake-v1",
        "normative_algorithm_version": "1.4",
        "inventory_sha256": inventory_sha256,
        "event_id": inventory.get("event_id"),
        "automatic_scoring_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "rows": rows,
    }


def _score(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise TournamentEpisodeScoringIntakeError(f"{field} must be integer 0..2")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise TournamentEpisodeScoringIntakeError(f"{field} must be integer 0..2") from exc
    if result not in {0, 1, 2}:
        raise TournamentEpisodeScoringIntakeError(f"{field} must be integer 0..2")
    return result


def validate_episode_scoring_intake(
    inventory: Mapping[str, Any], intake: Mapping[str, Any]
) -> dict[str, Any]:
    expected = build_episode_scoring_template(inventory)
    if intake.get("schema") != "tournament-episode-scoring-intake-v1":
        raise TournamentEpisodeScoringIntakeError("unsupported scoring intake schema")
    if str(intake.get("inventory_sha256") or "") != expected["inventory_sha256"]:
        raise TournamentEpisodeScoringIntakeError("scoring intake is not bound to this inventory")
    for field in (
        "automatic_scoring_allowed",
        "automatic_methodology_mapping_allowed",
        "automatic_student_error_attribution_allowed",
    ):
        if intake.get(field) is not False:
            raise TournamentEpisodeScoringIntakeError(f"scoring intake boundary was weakened: {field}")
    rows = intake.get("rows")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise TournamentEpisodeScoringIntakeError("rows must be a sequence")
    expected_by_id = {row["candidate_id"]: row for row in expected["rows"]}
    if len(rows) != len(expected_by_id):
        raise TournamentEpisodeScoringIntakeError("scoring intake cardinality mismatch")

    coverage_inputs = []
    pending = 0
    seen = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise TournamentEpisodeScoringIntakeError("scoring row must be a mapping")
        candidate_id = str(row.get("candidate_id") or "")
        baseline = expected_by_id.get(candidate_id)
        if baseline is None or candidate_id in seen:
            raise TournamentEpisodeScoringIntakeError("unknown or duplicate candidate_id")
        seen.add(candidate_id)
        for key in ("review_id", "event_id", "deal_id", "board_number", "category", "candidate_sha256"):
            if row.get(key) != baseline.get(key):
                raise TournamentEpisodeScoringIntakeError(f"immutable candidate binding mismatch: {key}")

        explicit = row.get("explicit_episode_adjudication") is True
        score_fields = (row.get("impact_score"), row.get("transferability_score"), row.get("reliability_score"))
        actor = str(row.get("score_actor") or "").strip()
        provenance = row.get("score_provenance")
        if not explicit:
            if any(value is not None for value in score_fields) or actor or provenance is not None:
                raise TournamentEpisodeScoringIntakeError("pending row cannot contain scores, actor or provenance")
            if row.get("status") != "PENDING_SCORING":
                raise TournamentEpisodeScoringIntakeError("unadjudicated row must remain PENDING_SCORING")
            pending += 1
            continue
        if not actor:
            raise TournamentEpisodeScoringIntakeError("explicit episode adjudication requires score_actor")
        if not isinstance(provenance, Mapping) or not provenance:
            raise TournamentEpisodeScoringIntakeError("explicit episode adjudication requires score_provenance")
        if row.get("status") != "SCORED_EXPLICITLY":
            raise TournamentEpisodeScoringIntakeError("explicitly adjudicated row must be SCORED_EXPLICITLY")
        impact = _score(row.get("impact_score"), "impact_score")
        transferability = _score(row.get("transferability_score"), "transferability_score")
        reliability = _score(row.get("reliability_score"), "reliability_score")
        coverage_inputs.append(
            {
                "episode_id": candidate_id,
                "board_number": int(row["board_number"]),
                "impact_score": impact,
                "transferability_score": transferability,
                "reliability_score": reliability,
                "score_provenance": {
                    **dict(provenance),
                    "explicit_episode_adjudication": True,
                    "score_actor": actor,
                    "candidate_sha256": row["candidate_sha256"],
                    "inventory_sha256": expected["inventory_sha256"],
                },
            }
        )

    return {
        "schema": "tournament-episode-scoring-validation-v1",
        "inventory_sha256": expected["inventory_sha256"],
        "candidate_count": len(expected_by_id),
        "explicitly_scored_count": len(coverage_inputs),
        "pending_scoring_count": pending,
        "episode_scoring_complete": pending == 0,
        "coverage_episode_inputs": coverage_inputs,
        "automatic_scoring_used": False,
        "methodology_mapping_created": False,
        "student_error_attribution_created": False,
    }
