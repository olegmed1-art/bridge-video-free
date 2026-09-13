from __future__ import annotations

from typing import Any, Mapping, Sequence

from .tournament_teacher_decision_intake_v3 import (
    apply_teacher_decision_intake,
    build_teacher_decision_template,
)
from .tournament_teacher_review_portfolio_v3 import verify_teacher_review_portfolio


class TeacherReviewPortfolioIntakeError(ValueError):
    pass


def _bundle_map(bundles: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for bundle in bundles:
        if not isinstance(bundle, Mapping):
            raise TeacherReviewPortfolioIntakeError("portfolio source bundle must be a mapping")
        bundle_id = str(bundle.get("bundle_id") or "")
        if not bundle_id or bundle_id in out:
            raise TeacherReviewPortfolioIntakeError("portfolio source bundle_id missing or duplicated")
        out[bundle_id] = bundle
    return out


def build_portfolio_teacher_decision_template(
    portfolio: Mapping[str, Any], bundles: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    verify_teacher_review_portfolio(portfolio, bundles)
    by_bundle = _bundle_map(bundles)
    expected_bundle_ids = {str(row.get("bundle_id") or "") for row in portfolio.get("source_bundles", [])}
    if expected_bundle_ids != set(by_bundle):
        raise TeacherReviewPortfolioIntakeError("portfolio/source bundle set mismatch")

    source_templates = {bundle_id: build_teacher_decision_template(bundle) for bundle_id, bundle in by_bundle.items()}
    source_rows: dict[tuple[str, str], Mapping[str, Any]] = {}
    for bundle_id, template in source_templates.items():
        rows = template.get("decisions")
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise TeacherReviewPortfolioIntakeError("source teacher decision template is malformed")
        for row in rows:
            if not isinstance(row, Mapping):
                raise TeacherReviewPortfolioIntakeError("source teacher decision row is malformed")
            review_id = str(row.get("review_id") or "")
            key = (bundle_id, review_id)
            if not review_id or key in source_rows:
                raise TeacherReviewPortfolioIntakeError("source teacher decision identity missing or duplicated")
            source_rows[key] = row

    portfolio_items = portfolio.get("items")
    if not isinstance(portfolio_items, Sequence) or isinstance(portfolio_items, (str, bytes)):
        raise TeacherReviewPortfolioIntakeError("portfolio items are malformed")
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in portfolio_items:
        if not isinstance(item, Mapping):
            raise TeacherReviewPortfolioIntakeError("portfolio item must be a mapping")
        bundle_id = str(item.get("source_bundle_id") or "")
        review_id = str(item.get("review_id") or "")
        key = (bundle_id, review_id)
        source = source_rows.get(key)
        if source is None or key in seen:
            raise TeacherReviewPortfolioIntakeError("portfolio item does not map exactly to source decision row")
        seen.add(key)
        for field in ("event_id", "deal_id", "category"):
            if str(item.get(field) or "") != str(source.get(field) or ""):
                raise TeacherReviewPortfolioIntakeError(f"portfolio/source decision identity mismatch: {field}")
        rows.append({"source_bundle_id": bundle_id, **dict(source)})
    if seen != set(source_rows):
        raise TeacherReviewPortfolioIntakeError("portfolio does not cover every source decision row")

    return {
        "schema": "tournament-teacher-review-portfolio-decision-intake-v1",
        "portfolio_id": portfolio["portfolio_id"],
        "source_bundle_ids": sorted(by_bundle),
        "automatic_decisions_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "required_decision_source": "EXPLICIT_TEACHER_DECISION",
        "decision_count": len(rows),
        "decisions": rows,
        "instructions": (
            "One inert decision form for all current review batches. Blank rows remain PENDING. "
            "Every decided row must retain its source_bundle_id and requires explicit teacher attestation; "
            "results are routed back to the original bundle ledger instead of rewriting historical review IDs."
        ),
    }


def apply_portfolio_teacher_decision_intake(
    portfolio: Mapping[str, Any],
    bundles: Sequence[Mapping[str, Any]],
    intake: Mapping[str, Any],
) -> dict[str, Any]:
    template = build_portfolio_teacher_decision_template(portfolio, bundles)
    by_bundle = _bundle_map(bundles)
    if intake.get("schema") != template["schema"]:
        raise TeacherReviewPortfolioIntakeError("unsupported portfolio decision intake schema")
    if intake.get("portfolio_id") != template["portfolio_id"]:
        raise TeacherReviewPortfolioIntakeError("portfolio decision intake portfolio_id mismatch")
    if intake.get("source_bundle_ids") != template["source_bundle_ids"]:
        raise TeacherReviewPortfolioIntakeError("portfolio decision intake source bundle set changed")
    for field in (
        "automatic_decisions_allowed",
        "automatic_methodology_mapping_allowed",
        "automatic_student_error_attribution_allowed",
    ):
        if intake.get(field) is not False:
            raise TeacherReviewPortfolioIntakeError(f"portfolio decision boundary was weakened: {field}")
    if intake.get("required_decision_source") != "EXPLICIT_TEACHER_DECISION":
        raise TeacherReviewPortfolioIntakeError("explicit teacher decision source contract was weakened")

    rows = intake.get("decisions")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise TeacherReviewPortfolioIntakeError("portfolio decision rows must be a sequence")
    if intake.get("decision_count") != len(template["decisions"]) or len(rows) != len(template["decisions"]):
        raise TeacherReviewPortfolioIntakeError("portfolio decision intake cardinality changed")

    expected = {
        (str(row["source_bundle_id"]), str(row["review_id"])): row for row in template["decisions"]
    }
    grouped: dict[str, list[dict[str, Any]]] = {bundle_id: [] for bundle_id in by_bundle}
    seen: set[tuple[str, str]] = set()
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise TeacherReviewPortfolioIntakeError("portfolio decision row must be a mapping")
        bundle_id = str(raw.get("source_bundle_id") or "")
        review_id = str(raw.get("review_id") or "")
        key = (bundle_id, review_id)
        expected_row = expected.get(key)
        if expected_row is None or key in seen:
            raise TeacherReviewPortfolioIntakeError("portfolio decision row identity unknown or duplicated")
        seen.add(key)
        for field in ("event_id", "deal_id", "category", "allowed_statuses"):
            if raw.get(field) != expected_row.get(field):
                raise TeacherReviewPortfolioIntakeError(f"portfolio decision immutable field changed: {field}")
        grouped[bundle_id].append({k: v for k, v in raw.items() if k != "source_bundle_id"})
    if seen != set(expected):
        raise TeacherReviewPortfolioIntakeError("portfolio decision intake must preserve the exact review set")

    results: list[dict[str, Any]] = []
    total_decided = 0
    total_pending = 0
    for bundle_id in sorted(by_bundle):
        bundle = by_bundle[bundle_id]
        source_template = build_teacher_decision_template(bundle)
        source_template = {**source_template, "decisions": grouped[bundle_id]}
        result = apply_teacher_decision_intake(bundle, source_template)
        total_decided += int(result["decided_count"])
        total_pending += int(result["pending_count"])
        results.append(
            {
                "source_bundle_id": bundle_id,
                "decided_count": result["decided_count"],
                "pending_count": result["pending_count"],
                "ledger": result["ledger"],
            }
        )

    if total_decided + total_pending != len(template["decisions"]):
        raise TeacherReviewPortfolioIntakeError("portfolio decision result cardinality mismatch")
    return {
        "schema": "tournament-teacher-review-portfolio-decision-result-v1",
        "portfolio_id": portfolio["portfolio_id"],
        "source_bundle_count": len(results),
        "decided_count": total_decided,
        "pending_count": total_pending,
        "review_state": "PENDING_TEACHER_DECISIONS" if total_pending else "ALL_REVIEW_ITEMS_DECIDED",
        "automatic_decisions_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "bundle_results": results,
    }
