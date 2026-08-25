from __future__ import annotations

from typing import Any, Mapping, Sequence

from .tournament_coverage_release_v3 import build_coverage_manifest
from .tournament_episode_source_census_v3 import source_facts_sha256
from .tournament_teacher_decisions_v3 import TeacherDecisionStatus
from .tournament_teacher_review_portfolio_v3 import verify_teacher_review_portfolio


class TournamentPortfolioEpisodeCoverageError(ValueError):
    pass


def _source_event_id(source: Mapping[str, Any]) -> str:
    if source.get("schema") != "bridge-tournament-facts-v1":
        raise TournamentPortfolioEpisodeCoverageError("unsupported tournament facts schema")
    tournament = source.get("tournament")
    if not isinstance(tournament, Mapping):
        raise TournamentPortfolioEpisodeCoverageError("tournament metadata missing")
    provider_key = str(tournament.get("provider_native_key") or "").strip()
    prefix = "bridge.co.il:event:"
    marker = ":round:"
    if not provider_key.startswith(prefix) or marker not in provider_key:
        raise TournamentPortfolioEpisodeCoverageError("unsupported provider_native_key for event coverage binding")
    event_id = provider_key[len(prefix):].split(marker, 1)[0].strip()
    if not event_id:
        raise TournamentPortfolioEpisodeCoverageError("provider event id missing")
    return event_id


def _portfolio_items(portfolio: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    portfolio_id = str(portfolio.get("portfolio_id") or "").strip()
    if not portfolio_id:
        raise TournamentPortfolioEpisodeCoverageError("portfolio identity missing")
    rows = portfolio.get("items")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise TournamentPortfolioEpisodeCoverageError("portfolio items must be a sequence")
    out: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise TournamentPortfolioEpisodeCoverageError("portfolio item must be a mapping")
        bundle_id = str(row.get("source_bundle_id") or "").strip()
        review_id = str(row.get("review_id") or "").strip()
        key = (bundle_id, review_id)
        if not bundle_id or not review_id or key in out:
            raise TournamentPortfolioEpisodeCoverageError("portfolio review identity missing or duplicated")
        for field in ("event_id", "deal_id", "category"):
            if not str(row.get(field) or "").strip():
                raise TournamentPortfolioEpisodeCoverageError(f"portfolio item missing {field}")
        out[key] = row
    if not out:
        raise TournamentPortfolioEpisodeCoverageError("portfolio contains no review items")
    return out


def _decision_index(
    portfolio: Mapping[str, Any],
    portfolio_decision_result: Mapping[str, Any],
    expected_items: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    if portfolio_decision_result.get("schema") != "tournament-teacher-review-portfolio-decision-result-v1":
        raise TournamentPortfolioEpisodeCoverageError("unsupported portfolio decision result schema")
    if portfolio_decision_result.get("portfolio_id") != portfolio.get("portfolio_id"):
        raise TournamentPortfolioEpisodeCoverageError("portfolio decision result identity mismatch")
    for field in (
        "automatic_decisions_allowed",
        "automatic_methodology_mapping_allowed",
        "automatic_student_error_attribution_allowed",
    ):
        if portfolio_decision_result.get(field) is not False:
            raise TournamentPortfolioEpisodeCoverageError(f"portfolio decision boundary weakened: {field}")
    bundle_results = portfolio_decision_result.get("bundle_results")
    if not isinstance(bundle_results, Sequence) or isinstance(bundle_results, (str, bytes)):
        raise TournamentPortfolioEpisodeCoverageError("bundle_results must be a sequence")
    valid_statuses = {status.value for status in TeacherDecisionStatus}
    out: dict[tuple[str, str], Mapping[str, Any]] = {}
    for bundle_result in bundle_results:
        if not isinstance(bundle_result, Mapping):
            raise TournamentPortfolioEpisodeCoverageError("bundle result must be a mapping")
        bundle_id = str(bundle_result.get("source_bundle_id") or "").strip()
        ledger = bundle_result.get("ledger")
        if not bundle_id or not isinstance(ledger, Mapping):
            raise TournamentPortfolioEpisodeCoverageError("bundle result identity or ledger missing")
        rows = ledger.get("decisions")
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise TournamentPortfolioEpisodeCoverageError("decision rows must be a sequence")
        for row in rows:
            if not isinstance(row, Mapping):
                raise TournamentPortfolioEpisodeCoverageError("decision row must be a mapping")
            review_id = str(row.get("review_id") or "").strip()
            key = (bundle_id, review_id)
            expected = expected_items.get(key)
            if expected is None or not review_id or key in out:
                raise TournamentPortfolioEpisodeCoverageError("decision review identity unknown or duplicated")
            for field in ("event_id", "deal_id", "category"):
                if str(row.get(field) or "") != str(expected.get(field) or ""):
                    raise TournamentPortfolioEpisodeCoverageError(f"portfolio/decision identity mismatch: {field}")
            if str(row.get("status") or "") not in valid_statuses:
                raise TournamentPortfolioEpisodeCoverageError("unsupported teacher decision status")
            if row.get("automatic_methodology_mapping_allowed") is not False:
                raise TournamentPortfolioEpisodeCoverageError("decision methodology boundary weakened")
            if row.get("automatic_student_error_attribution_allowed") is not False:
                raise TournamentPortfolioEpisodeCoverageError("decision student-error boundary weakened")
            out[key] = row
    if set(out) != set(expected_items):
        raise TournamentPortfolioEpisodeCoverageError("decision result does not cover exact portfolio review set")
    return out


def _score(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise TournamentPortfolioEpisodeCoverageError(f"{field} must be integer 0..2")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise TournamentPortfolioEpisodeCoverageError(f"{field} must be integer 0..2") from exc
    if result not in {0, 1, 2}:
        raise TournamentPortfolioEpisodeCoverageError(f"{field} must be integer 0..2")
    return result


def _scored_index(
    portfolio: Mapping[str, Any],
    scoring_result: Mapping[str, Any],
    expected_items: Mapping[tuple[str, str], Mapping[str, Any]],
    decisions: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    if scoring_result.get("schema") != "tournament-portfolio-episode-scoring-result-v1":
        raise TournamentPortfolioEpisodeCoverageError("unsupported portfolio episode scoring result schema")
    if scoring_result.get("normative_algorithm_version") != "1.4":
        raise TournamentPortfolioEpisodeCoverageError("portfolio episode scoring normative version mismatch")
    if scoring_result.get("portfolio_id") != portfolio.get("portfolio_id"):
        raise TournamentPortfolioEpisodeCoverageError("portfolio scoring result identity mismatch")
    if int(scoring_result.get("review_item_count") or -1) != len(expected_items):
        raise TournamentPortfolioEpisodeCoverageError("portfolio scoring result cardinality mismatch")
    for field in (
        "automatic_episode_scoring_used",
        "automatic_methodology_mapping_used",
        "automatic_student_error_attribution_used",
        "causal_error_attribution_allowed",
    ):
        if scoring_result.get(field) is not False:
            raise TournamentPortfolioEpisodeCoverageError(f"portfolio scoring boundary weakened: {field}")
    rows = scoring_result.get("scored_items")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise TournamentPortfolioEpisodeCoverageError("scored_items must be a sequence")
    out: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise TournamentPortfolioEpisodeCoverageError("scored item must be a mapping")
        key = (str(row.get("source_bundle_id") or ""), str(row.get("review_id") or ""))
        expected = expected_items.get(key)
        decision = decisions.get(key)
        if expected is None or decision is None or key in out:
            raise TournamentPortfolioEpisodeCoverageError("scored item identity unknown or duplicated")
        if str(decision.get("status") or "") != TeacherDecisionStatus.CONFIRMED_TECHNICAL_RELEVANCE.value:
            raise TournamentPortfolioEpisodeCoverageError("scored item lacks confirmed technical relevance")
        for field in ("event_id", "deal_id", "category"):
            if str(row.get(field) or "") != str(expected.get(field) or ""):
                raise TournamentPortfolioEpisodeCoverageError(f"portfolio/scoring identity mismatch: {field}")
        impact = _score(row.get("impact_score"), "impact_score")
        transferability = _score(row.get("transferability_score"), "transferability_score")
        reliability = _score(row.get("reliability_score"), "reliability_score")
        total = impact + transferability + reliability
        if int(row.get("total_score") or -1) != total:
            raise TournamentPortfolioEpisodeCoverageError("scored item total is inconsistent")
        expected_tier = "SIGNIFICANT_DEEP_SLIDE" if total >= 4 else "STANDARD_BOARD_ANALYSIS" if total >= 2 else "BRIEF_REVIEW"
        if row.get("tier") != expected_tier:
            raise TournamentPortfolioEpisodeCoverageError("scored item tier is inconsistent")
        if not str(row.get("score_actor") or "").strip():
            raise TournamentPortfolioEpisodeCoverageError("scored item actor missing")
        provenance = row.get("score_provenance")
        if not isinstance(provenance, Mapping) or not provenance:
            raise TournamentPortfolioEpisodeCoverageError("scored item provenance missing")
        if row.get("causal_link") != "NOT_ESTABLISHED":
            raise TournamentPortfolioEpisodeCoverageError("scored item causal boundary weakened")
        if row.get("methodology_mapping") is not None or row.get("student_error_attribution") is not None:
            raise TournamentPortfolioEpisodeCoverageError("scored item pedagogical boundary weakened")
        out[key] = row
    if int(scoring_result.get("explicitly_scored_count") or 0) != len(out):
        raise TournamentPortfolioEpisodeCoverageError("scored item count is inconsistent")
    return out


def _validate_source_census(source: Mapping[str, Any], census: Mapping[str, Any]) -> tuple[bool, list[str]]:
    if census.get("schema") != "tournament-episode-source-census-v1":
        raise TournamentPortfolioEpisodeCoverageError("unsupported episode source census schema")
    if census.get("normative_algorithm_version") != "1.4":
        raise TournamentPortfolioEpisodeCoverageError("episode source census algorithm boundary mismatch")
    if census.get("source_facts_sha256") != source_facts_sha256(source):
        raise TournamentPortfolioEpisodeCoverageError("episode source census is not bound to exact facts")
    tournament = source.get("tournament")
    provider_key = str(tournament.get("provider_native_key") or "") if isinstance(tournament, Mapping) else ""
    if census.get("provider_native_key") != provider_key:
        raise TournamentPortfolioEpisodeCoverageError("episode source census provider identity mismatch")
    for field in (
        "automatic_episode_creation_allowed",
        "automatic_methodology_mapping_allowed",
        "automatic_student_error_attribution_allowed",
    ):
        if census.get(field) is not False:
            raise TournamentPortfolioEpisodeCoverageError(f"episode source census boundary weakened: {field}")
    if census.get("unavailable_evidence_not_reconstructed") is not True:
        raise TournamentPortfolioEpisodeCoverageError("episode source census may not reconstruct unavailable evidence")
    blockers = census.get("census_blockers")
    if not isinstance(blockers, list):
        raise TournamentPortfolioEpisodeCoverageError("episode source census blockers must be a list")
    complete = census.get("non_dd_episode_source_census_complete") is True
    if complete and blockers:
        raise TournamentPortfolioEpisodeCoverageError("complete episode source census cannot have blockers")
    if not complete and not blockers:
        raise TournamentPortfolioEpisodeCoverageError("incomplete episode source census must expose blockers")
    return complete, [str(value) for value in blockers]


def _board_number(event_id: str, deal_id: str) -> int:
    if not deal_id.startswith(f"{event_id}:"):
        raise TournamentPortfolioEpisodeCoverageError("deal identity does not belong to source event")
    try:
        board = int(deal_id.rsplit(":", 1)[1])
    except (IndexError, TypeError, ValueError) as exc:
        raise TournamentPortfolioEpisodeCoverageError("deal identity does not contain a board number") from exc
    if board <= 0:
        raise TournamentPortfolioEpisodeCoverageError("deal board number must be positive")
    return board


def build_portfolio_episode_coverage_handoff(
    source: Mapping[str, Any],
    portfolio: Mapping[str, Any],
    bundles: Sequence[Mapping[str, Any]],
    portfolio_decision_result: Mapping[str, Any],
    portfolio_scoring_result: Mapping[str, Any],
    *,
    source_census: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind portfolio-wide teacher decisions and explicit episode scores to one event's v1.4 coverage.

    The handoff is event-local but portfolio-complete: later additive evidence batches for
    the same event cannot be omitted. Different evidence categories on the same board
    remain distinct review/episode identities. Only explicitly teacher-confirmed and
    explicitly scored technical items enter coverage; no causality, methodology mapping,
    or student-error attribution is created here.
    """
    verify_teacher_review_portfolio(portfolio, bundles)
    event_id = _source_event_id(source)
    items = _portfolio_items(portfolio)
    decisions = _decision_index(portfolio, portfolio_decision_result, items)
    scored = _scored_index(portfolio, portfolio_scoring_result, items, decisions)

    event_keys = {key for key, item in items.items() if str(item.get("event_id") or "") == event_id}
    if not event_keys:
        raise TournamentPortfolioEpisodeCoverageError("portfolio contains no reviews for source event")

    confirmed_keys = {
        key for key in event_keys
        if str(decisions[key].get("status") or "") == TeacherDecisionStatus.CONFIRMED_TECHNICAL_RELEVANCE.value
    }
    dismissed_keys = {
        key for key in event_keys
        if str(decisions[key].get("status") or "") == TeacherDecisionStatus.DISMISSED.value
    }
    needs_context_keys = {
        key for key in event_keys
        if str(decisions[key].get("status") or "") == TeacherDecisionStatus.NEEDS_CONTEXT.value
    }
    pending_keys = {
        key for key in event_keys
        if str(decisions[key].get("status") or "") == TeacherDecisionStatus.PENDING.value
    }
    event_scored_keys = set(scored) & event_keys
    if not event_scored_keys <= confirmed_keys:
        raise TournamentPortfolioEpisodeCoverageError("event scoring contains an unconfirmed review")
    confirmed_unscored = confirmed_keys - event_scored_keys

    episodes: list[dict[str, Any]] = []
    for key in sorted(event_scored_keys):
        item = items[key]
        score = scored[key]
        provenance = dict(score["score_provenance"])
        provenance["portfolio_binding"] = {
            "portfolio_id": portfolio["portfolio_id"],
            "source_bundle_id": key[0],
            "review_id": key[1],
            "event_id": event_id,
            "deal_id": item["deal_id"],
            "category": item["category"],
            "score_actor": score["score_actor"],
        }
        episodes.append(
            {
                "episode_id": f"{key[0]}:{key[1]}",
                "board_number": _board_number(event_id, str(item["deal_id"])),
                "impact_score": score["impact_score"],
                "transferability_score": score["transferability_score"],
                "reliability_score": score["reliability_score"],
                "score_provenance": provenance,
            }
        )

    census_complete = False
    census_blockers: list[str] = []
    if source_census is None:
        census_blockers.append("NON_DDS_SOURCE_CENSUS_NOT_SUPPLIED")
    else:
        census_complete, census_blockers = _validate_source_census(source, source_census)
        if not census_complete:
            census_blockers = sorted(set(census_blockers + ["NON_DDS_SOURCE_CENSUS_NOT_COMPLETE"]))

    event_adjudication_complete = not pending_keys and not needs_context_keys and not confirmed_unscored
    event_inventory_complete = event_adjudication_complete and census_complete
    coverage_manifest = build_coverage_manifest(
        source,
        episodes=episodes,
        episode_inventory_complete=event_inventory_complete,
    )

    blockers = set(str(value) for value in coverage_manifest.get("release_blockers") or [])
    blockers.update(census_blockers)
    if pending_keys:
        blockers.add("TEACHER_DECISION_PENDING")
    if needs_context_keys:
        blockers.add("TEACHER_CONTEXT_REQUIRED")
    if confirmed_unscored:
        blockers.add("CONFIRMED_EPISODE_SCORING_NOT_COMPLETE")
    if not event_adjudication_complete:
        blockers.add("EVENT_EPISODE_ADJUDICATION_NOT_COMPLETE")

    return {
        "schema": "tournament-portfolio-episode-coverage-handoff-v1",
        "normative_algorithm_version": "1.4",
        "portfolio_id": portfolio["portfolio_id"],
        "event_id": event_id,
        "event_review_item_count": len(event_keys),
        "event_confirmed_technical_count": len(confirmed_keys),
        "event_dismissed_count": len(dismissed_keys),
        "event_needs_context_count": len(needs_context_keys),
        "event_pending_decision_count": len(pending_keys),
        "event_explicitly_scored_count": len(event_scored_keys),
        "event_confirmed_unscored_count": len(confirmed_unscored),
        "event_episode_adjudication_complete": event_adjudication_complete,
        "non_dd_source_census_supplied": source_census is not None,
        "non_dd_source_census_complete": census_complete,
        "non_dd_source_census_blockers": census_blockers,
        "v1_4_episode_inventory_complete": event_inventory_complete,
        "coverage_episode_count": len(episodes),
        "coverage_manifest": coverage_manifest,
        "handoff_ready": event_inventory_complete and coverage_manifest.get("coverage_plan_release_ready") is True,
        "handoff_blockers": sorted(blockers),
        "portfolio_complete_for_event": True,
        "cross_category_causal_collapse_allowed": False,
        "teacher_decision_gate_enforced": True,
        "automatic_teacher_decisions_used": False,
        "automatic_episode_scoring_used": False,
        "automatic_methodology_mapping_used": False,
        "automatic_student_error_attribution_used": False,
        "causal_error_attribution_allowed": False,
        "interpretation": (
            "Coverage is derived from the complete current teacher-review portfolio for this event. Only explicitly "
            "confirmed and explicitly scored technical reviews enter slide coverage. Multiple evidence categories on "
            "one deal remain distinct and no causal or pedagogical attribution is inferred."
        ),
    }
