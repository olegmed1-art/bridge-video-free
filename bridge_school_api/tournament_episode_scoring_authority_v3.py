from __future__ import annotations

from typing import Any, Mapping, Sequence

from .tournament_episode_scoring_intake_v3 import validate_episode_scoring_intake
from .tournament_teacher_decisions_v3 import TeacherDecisionStatus


class TournamentEpisodeScoringAuthorityError(ValueError):
    pass


def _require_false(payload: Mapping[str, Any], fields: Sequence[str], *, label: str) -> None:
    for field in fields:
        if payload.get(field) is not False:
            raise TournamentEpisodeScoringAuthorityError(f"{label} boundary was weakened: {field}")


def _candidate_index(inventory: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    if inventory.get("schema") != "tournament-evidence-episode-candidate-inventory-v1":
        raise TournamentEpisodeScoringAuthorityError("unsupported episode candidate inventory schema")
    queue_sha = str(inventory.get("queue_sha256") or "").strip()
    if len(queue_sha) != 64:
        raise TournamentEpisodeScoringAuthorityError("inventory queue_sha256 is required")
    candidates = inventory.get("technical_candidates")
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
        raise TournamentEpisodeScoringAuthorityError("technical_candidates must be a sequence")
    out: dict[str, Mapping[str, Any]] = {}
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise TournamentEpisodeScoringAuthorityError("candidate must be a mapping")
        review_id = str(candidate.get("review_id") or "").strip()
        if not review_id or review_id in out:
            raise TournamentEpisodeScoringAuthorityError("candidate review_id must be unique")
        out[review_id] = candidate
    return out


def _decision_index(
    inventory: Mapping[str, Any], decision_ledger: Mapping[str, Any]
) -> dict[str, Mapping[str, Any]]:
    if decision_ledger.get("schema") != "tournament-teacher-decision-ledger-v1":
        raise TournamentEpisodeScoringAuthorityError("unsupported teacher decision ledger schema")
    _require_false(
        decision_ledger,
        (
            "automatic_decisions_allowed",
            "automatic_methodology_mapping_allowed",
            "automatic_student_error_attribution_allowed",
        ),
        label="teacher decision ledger",
    )
    if str(decision_ledger.get("queue_sha256") or "") != str(inventory.get("queue_sha256") or ""):
        raise TournamentEpisodeScoringAuthorityError("teacher decision ledger is not bound to candidate inventory queue")

    rows = decision_ledger.get("decisions")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise TournamentEpisodeScoringAuthorityError("teacher decision ledger rows must be a sequence")
    out: dict[str, Mapping[str, Any]] = {}
    valid_statuses = {status.value for status in TeacherDecisionStatus}
    for row in rows:
        if not isinstance(row, Mapping):
            raise TournamentEpisodeScoringAuthorityError("teacher decision row must be a mapping")
        review_id = str(row.get("review_id") or "").strip()
        if not review_id or review_id in out:
            raise TournamentEpisodeScoringAuthorityError("teacher decision review_id must be unique")
        status = str(row.get("status") or "")
        if status not in valid_statuses:
            raise TournamentEpisodeScoringAuthorityError(f"unsupported teacher decision status: {status!r}")
        if row.get("automatic_methodology_mapping_allowed") is not False:
            raise TournamentEpisodeScoringAuthorityError("automatic methodology mapping was enabled")
        if row.get("automatic_student_error_attribution_allowed") is not False:
            raise TournamentEpisodeScoringAuthorityError("automatic student-error attribution was enabled")
        provenance = row.get("decision_provenance")
        if status == TeacherDecisionStatus.PENDING.value:
            if row.get("teacher_decision_required") is not True:
                raise TournamentEpisodeScoringAuthorityError("pending decision must still require teacher review")
            if row.get("decision_note") is not None or provenance is not None:
                raise TournamentEpisodeScoringAuthorityError("pending decision contains explicit decision material")
        else:
            if row.get("teacher_decision_required") is not False:
                raise TournamentEpisodeScoringAuthorityError("resolved teacher decision must close review requirement")
            if not isinstance(provenance, Mapping) or provenance.get("decision_source") != "EXPLICIT_TEACHER_DECISION":
                raise TournamentEpisodeScoringAuthorityError("resolved decision requires explicit teacher provenance")
        out[review_id] = row
    return out


def authorize_episode_scoring(
    inventory: Mapping[str, Any],
    intake: Mapping[str, Any],
    decision_ledger: Mapping[str, Any],
) -> dict[str, Any]:
    """Authorize scored episodes only after an explicit teacher relevance decision.

    A raw score is not authority. Only a candidate whose matching teacher decision is
    CONFIRMED_TECHNICAL_RELEVANCE may carry explicit 0..2 episode scores into slide
    coverage. DISMISSED candidates resolve without scores; PENDING and NEEDS_CONTEXT
    remain hard blockers. No decision here creates causality, methodology, or a
    student-error attribution.
    """
    raw_validation = validate_episode_scoring_intake(inventory, intake)
    candidates = _candidate_index(inventory)
    decisions = _decision_index(inventory, decision_ledger)

    intake_rows = intake.get("rows")
    if not isinstance(intake_rows, Sequence) or isinstance(intake_rows, (str, bytes)):
        raise TournamentEpisodeScoringAuthorityError("scoring intake rows must be a sequence")
    intake_by_review: dict[str, Mapping[str, Any]] = {}
    for row in intake_rows:
        if not isinstance(row, Mapping):
            raise TournamentEpisodeScoringAuthorityError("scoring row must be a mapping")
        review_id = str(row.get("review_id") or "").strip()
        if not review_id or review_id in intake_by_review:
            raise TournamentEpisodeScoringAuthorityError("scoring intake review_id must be unique")
        intake_by_review[review_id] = row

    raw_coverage_by_candidate = {
        str(row.get("episode_id") or ""): row
        for row in raw_validation.get("coverage_episode_inputs", [])
        if isinstance(row, Mapping)
    }

    counts = {
        TeacherDecisionStatus.CONFIRMED_TECHNICAL_RELEVANCE.value: 0,
        TeacherDecisionStatus.DISMISSED.value: 0,
        TeacherDecisionStatus.NEEDS_CONTEXT.value: 0,
        TeacherDecisionStatus.PENDING.value: 0,
    }
    authorized: list[Mapping[str, Any]] = []
    confirmed_unscored = 0

    for review_id, candidate in candidates.items():
        decision = decisions.get(review_id)
        if decision is None:
            raise TournamentEpisodeScoringAuthorityError("candidate has no matching teacher decision")
        for field in ("event_id", "deal_id", "category"):
            if decision.get(field) != candidate.get(field):
                raise TournamentEpisodeScoringAuthorityError(f"teacher decision candidate identity mismatch: {field}")

        scoring_row = intake_by_review.get(review_id)
        if scoring_row is None:
            raise TournamentEpisodeScoringAuthorityError("candidate has no matching scoring row")
        candidate_id = str(candidate.get("candidate_id") or "")
        if scoring_row.get("candidate_id") != candidate_id:
            raise TournamentEpisodeScoringAuthorityError("scoring row candidate identity mismatch")

        status = str(decision.get("status") or "")
        counts[status] += 1
        explicitly_scored = scoring_row.get("explicit_episode_adjudication") is True

        if status == TeacherDecisionStatus.CONFIRMED_TECHNICAL_RELEVANCE.value:
            if explicitly_scored:
                coverage = raw_coverage_by_candidate.get(candidate_id)
                if coverage is None:
                    raise TournamentEpisodeScoringAuthorityError("confirmed explicit score missing validated coverage input")
                authorized.append(coverage)
            else:
                confirmed_unscored += 1
            continue

        if explicitly_scored:
            raise TournamentEpisodeScoringAuthorityError(
                f"episode scoring is forbidden for teacher decision status {status}"
            )

    unresolved = (
        counts[TeacherDecisionStatus.PENDING.value]
        + counts[TeacherDecisionStatus.NEEDS_CONTEXT.value]
        + confirmed_unscored
    )
    complete = unresolved == 0

    return {
        "schema": "tournament-episode-scoring-authority-v1",
        "normative_algorithm_version": "1.4",
        "event_id": inventory.get("event_id"),
        "queue_sha256": inventory.get("queue_sha256"),
        "inventory_sha256": raw_validation.get("inventory_sha256"),
        "candidate_count": len(candidates),
        "confirmed_decision_count": counts[TeacherDecisionStatus.CONFIRMED_TECHNICAL_RELEVANCE.value],
        "dismissed_count": counts[TeacherDecisionStatus.DISMISSED.value],
        "needs_context_count": counts[TeacherDecisionStatus.NEEDS_CONTEXT.value],
        "pending_decision_count": counts[TeacherDecisionStatus.PENDING.value],
        "authorized_scored_count": len(authorized),
        "confirmed_unscored_count": confirmed_unscored,
        "episode_adjudication_complete": complete,
        "authorized_coverage_episode_inputs": authorized,
        "automatic_teacher_decisions_used": False,
        "automatic_episode_scoring_used": False,
        "automatic_methodology_mapping_used": False,
        "automatic_student_error_attribution_used": False,
        "causal_error_attribution_allowed": False,
    }
