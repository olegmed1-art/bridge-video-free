from __future__ import annotations

from typing import Any, Mapping, Sequence

from .tournament_teacher_decisions_v3 import TeacherDecisionStatus
from .tournament_teacher_review_portfolio_v3 import verify_teacher_review_portfolio


class TournamentPortfolioEpisodeScoringError(ValueError):
    pass


def _score(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise TournamentPortfolioEpisodeScoringError(f"{field} must be integer 0..2")
    try:
        score = int(value)
    except (TypeError, ValueError) as exc:
        raise TournamentPortfolioEpisodeScoringError(f"{field} must be integer 0..2") from exc
    if score not in {0, 1, 2}:
        raise TournamentPortfolioEpisodeScoringError(f"{field} must be integer 0..2")
    return score


def _decision_index(result: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    if result.get("schema") != "tournament-teacher-review-portfolio-decision-result-v1":
        raise TournamentPortfolioEpisodeScoringError("unsupported portfolio decision result schema")
    if result.get("automatic_decisions_allowed") is not False:
        raise TournamentPortfolioEpisodeScoringError("automatic teacher decisions were enabled")
    if result.get("automatic_methodology_mapping_allowed") is not False:
        raise TournamentPortfolioEpisodeScoringError("automatic methodology mapping was enabled")
    if result.get("automatic_student_error_attribution_allowed") is not False:
        raise TournamentPortfolioEpisodeScoringError("automatic student-error attribution was enabled")
    bundle_results = result.get("bundle_results")
    if not isinstance(bundle_results, Sequence) or isinstance(bundle_results, (str, bytes)):
        raise TournamentPortfolioEpisodeScoringError("bundle_results must be a sequence")
    out: dict[tuple[str, str], Mapping[str, Any]] = {}
    valid = {status.value for status in TeacherDecisionStatus}
    for bundle_result in bundle_results:
        if not isinstance(bundle_result, Mapping):
            raise TournamentPortfolioEpisodeScoringError("bundle result must be a mapping")
        bundle_id = str(bundle_result.get("source_bundle_id") or "").strip()
        ledger = bundle_result.get("ledger")
        if not bundle_id or not isinstance(ledger, Mapping):
            raise TournamentPortfolioEpisodeScoringError("bundle result identity or ledger missing")
        rows = ledger.get("decisions")
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise TournamentPortfolioEpisodeScoringError("decision rows must be a sequence")
        for row in rows:
            if not isinstance(row, Mapping):
                raise TournamentPortfolioEpisodeScoringError("decision row must be a mapping")
            review_id = str(row.get("review_id") or "").strip()
            key = (bundle_id, review_id)
            if not review_id or key in out:
                raise TournamentPortfolioEpisodeScoringError("decision identity missing or duplicated")
            status = str(row.get("status") or "")
            if status not in valid:
                raise TournamentPortfolioEpisodeScoringError(f"unsupported teacher decision status: {status!r}")
            if row.get("automatic_methodology_mapping_allowed") is not False:
                raise TournamentPortfolioEpisodeScoringError("decision methodology boundary weakened")
            if row.get("automatic_student_error_attribution_allowed") is not False:
                raise TournamentPortfolioEpisodeScoringError("decision student-error boundary weakened")
            out[key] = row
    return out


def build_portfolio_episode_scoring_template(
    portfolio: Mapping[str, Any],
    bundles: Sequence[Mapping[str, Any]],
    portfolio_decision_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Create one scoring intake that cannot omit later additive review batches.

    Episode scores remain explicit teacher/adjudicator input after technical relevance
    has been confirmed. Dismissed or unresolved review items cannot receive scores.
    No score creates causality, methodology mapping, or student-error attribution.
    """
    verify_teacher_review_portfolio(portfolio, bundles)
    if portfolio_decision_result.get("portfolio_id") != portfolio.get("portfolio_id"):
        raise TournamentPortfolioEpisodeScoringError("portfolio decision result identity mismatch")
    decisions = _decision_index(portfolio_decision_result)
    items = portfolio.get("items")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        raise TournamentPortfolioEpisodeScoringError("portfolio items must be a sequence")

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    confirmed = 0
    dismissed = 0
    unresolved = 0
    for item in items:
        if not isinstance(item, Mapping):
            raise TournamentPortfolioEpisodeScoringError("portfolio item must be a mapping")
        bundle_id = str(item.get("source_bundle_id") or "").strip()
        review_id = str(item.get("review_id") or "").strip()
        key = (bundle_id, review_id)
        if not bundle_id or not review_id or key in seen:
            raise TournamentPortfolioEpisodeScoringError("portfolio review identity missing or duplicated")
        seen.add(key)
        decision = decisions.get(key)
        if decision is None:
            raise TournamentPortfolioEpisodeScoringError("portfolio item has no matching decision")
        for field in ("event_id", "deal_id", "category"):
            if str(item.get(field) or "") != str(decision.get(field) or ""):
                raise TournamentPortfolioEpisodeScoringError(f"portfolio/decision identity mismatch: {field}")
        status = str(decision["status"])
        if status == TeacherDecisionStatus.CONFIRMED_TECHNICAL_RELEVANCE.value:
            row_status = "PENDING_EPISODE_SCORING"
            scoring_required = True
            confirmed += 1
        elif status == TeacherDecisionStatus.DISMISSED.value:
            row_status = "NOT_APPLICABLE_DISMISSED"
            scoring_required = False
            dismissed += 1
        else:
            row_status = "BLOCKED_TEACHER_DECISION"
            scoring_required = False
            unresolved += 1
        rows.append(
            {
                "source_bundle_id": bundle_id,
                "review_id": review_id,
                "event_id": str(item.get("event_id") or ""),
                "deal_id": str(item.get("deal_id") or ""),
                "category": str(item.get("category") or ""),
                "teacher_decision_status": status,
                "episode_scoring_required": scoring_required,
                "explicit_episode_adjudication": False,
                "impact_score": None,
                "transferability_score": None,
                "reliability_score": None,
                "score_actor": None,
                "score_provenance": None,
                "status": row_status,
            }
        )
    if set(decisions) != seen:
        raise TournamentPortfolioEpisodeScoringError("decision result contains reviews outside portfolio")
    rows.sort(key=lambda row: (row["event_id"], row["deal_id"], row["category"], row["source_bundle_id"], row["review_id"]))
    return {
        "schema": "tournament-portfolio-episode-scoring-intake-v1",
        "normative_algorithm_version": "1.4",
        "portfolio_id": portfolio["portfolio_id"],
        "review_item_count": len(rows),
        "confirmed_technical_count": confirmed,
        "dismissed_count": dismissed,
        "unresolved_teacher_review_count": unresolved,
        "automatic_episode_scoring_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "rows": rows,
    }


def apply_portfolio_episode_scoring_intake(
    portfolio: Mapping[str, Any],
    bundles: Sequence[Mapping[str, Any]],
    portfolio_decision_result: Mapping[str, Any],
    intake: Mapping[str, Any],
) -> dict[str, Any]:
    expected = build_portfolio_episode_scoring_template(portfolio, bundles, portfolio_decision_result)
    if intake.get("schema") != expected["schema"] or intake.get("portfolio_id") != expected["portfolio_id"]:
        raise TournamentPortfolioEpisodeScoringError("scoring intake identity mismatch")
    for field in (
        "automatic_episode_scoring_allowed",
        "automatic_methodology_mapping_allowed",
        "automatic_student_error_attribution_allowed",
    ):
        if intake.get(field) is not False:
            raise TournamentPortfolioEpisodeScoringError(f"scoring boundary weakened: {field}")
    rows = intake.get("rows")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or len(rows) != len(expected["rows"]):
        raise TournamentPortfolioEpisodeScoringError("scoring intake cardinality mismatch")
    expected_by_key = {(r["source_bundle_id"], r["review_id"]): r for r in expected["rows"]}
    seen: set[tuple[str, str]] = set()
    scored: list[dict[str, Any]] = []
    pending_confirmed = 0
    unresolved = 0
    dismissed = 0
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise TournamentPortfolioEpisodeScoringError("scoring row must be a mapping")
        key = (str(raw.get("source_bundle_id") or ""), str(raw.get("review_id") or ""))
        baseline = expected_by_key.get(key)
        if baseline is None or key in seen:
            raise TournamentPortfolioEpisodeScoringError("unknown or duplicate scoring row identity")
        seen.add(key)
        for field in ("event_id", "deal_id", "category", "teacher_decision_status", "episode_scoring_required"):
            if raw.get(field) != baseline.get(field):
                raise TournamentPortfolioEpisodeScoringError(f"immutable scoring field changed: {field}")
        decision_status = str(baseline["teacher_decision_status"])
        explicit = raw.get("explicit_episode_adjudication") is True
        score_material = (
            raw.get("impact_score"), raw.get("transferability_score"), raw.get("reliability_score"),
            raw.get("score_actor"), raw.get("score_provenance"),
        )
        if decision_status == TeacherDecisionStatus.CONFIRMED_TECHNICAL_RELEVANCE.value:
            if not explicit:
                if any(value is not None and value != "" for value in score_material):
                    raise TournamentPortfolioEpisodeScoringError("unscored confirmed row contains score material")
                if raw.get("status") != "PENDING_EPISODE_SCORING":
                    raise TournamentPortfolioEpisodeScoringError("confirmed unscored row must remain pending")
                pending_confirmed += 1
                continue
            actor = str(raw.get("score_actor") or "").strip()
            provenance = raw.get("score_provenance")
            if not actor or not isinstance(provenance, Mapping) or not provenance:
                raise TournamentPortfolioEpisodeScoringError("explicit episode score requires actor and provenance")
            if raw.get("status") != "SCORED_EXPLICITLY":
                raise TournamentPortfolioEpisodeScoringError("explicit score must use SCORED_EXPLICITLY status")
            impact = _score(raw.get("impact_score"), "impact_score")
            transferability = _score(raw.get("transferability_score"), "transferability_score")
            reliability = _score(raw.get("reliability_score"), "reliability_score")
            total = impact + transferability + reliability
            tier = "SIGNIFICANT_DEEP_SLIDE" if total >= 4 else "STANDARD_BOARD_ANALYSIS" if total >= 2 else "BRIEF_REVIEW"
            scored.append(
                {
                    "source_bundle_id": key[0],
                    "review_id": key[1],
                    "event_id": raw["event_id"],
                    "deal_id": raw["deal_id"],
                    "category": raw["category"],
                    "impact_score": impact,
                    "transferability_score": transferability,
                    "reliability_score": reliability,
                    "total_score": total,
                    "tier": tier,
                    "score_actor": actor,
                    "score_provenance": dict(provenance),
                    "causal_link": "NOT_ESTABLISHED",
                    "methodology_mapping": None,
                    "student_error_attribution": None,
                }
            )
            continue
        if explicit or any(value is not None and value != "" for value in score_material):
            raise TournamentPortfolioEpisodeScoringError("episode scoring forbidden without confirmed technical relevance")
        if decision_status == TeacherDecisionStatus.DISMISSED.value:
            if raw.get("status") != "NOT_APPLICABLE_DISMISSED":
                raise TournamentPortfolioEpisodeScoringError("dismissed row status changed")
            dismissed += 1
        else:
            if raw.get("status") != "BLOCKED_TEACHER_DECISION":
                raise TournamentPortfolioEpisodeScoringError("unresolved review row must remain blocked")
            unresolved += 1
    if seen != set(expected_by_key):
        raise TournamentPortfolioEpisodeScoringError("scoring intake does not cover exact portfolio review set")
    complete = unresolved == 0 and pending_confirmed == 0
    return {
        "schema": "tournament-portfolio-episode-scoring-result-v1",
        "normative_algorithm_version": "1.4",
        "portfolio_id": portfolio["portfolio_id"],
        "review_item_count": len(expected_by_key),
        "explicitly_scored_count": len(scored),
        "confirmed_unscored_count": pending_confirmed,
        "dismissed_count": dismissed,
        "unresolved_teacher_review_count": unresolved,
        "portfolio_episode_scoring_complete": complete,
        "scored_items": scored,
        "automatic_episode_scoring_used": False,
        "automatic_methodology_mapping_used": False,
        "automatic_student_error_attribution_used": False,
        "causal_error_attribution_allowed": False,
    }
