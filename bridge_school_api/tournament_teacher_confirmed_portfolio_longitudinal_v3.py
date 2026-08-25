from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

from .tournament_teacher_confirmed_longitudinal_v3 import build_teacher_confirmed_longitudinal_report
from .tournament_teacher_decisions_v3 import TeacherDecisionStatus
from .tournament_teacher_review_portfolio_v3 import verify_teacher_review_portfolio


class TeacherConfirmedPortfolioLongitudinalError(ValueError):
    pass


def _bundle_map(bundles: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for bundle in bundles:
        if not isinstance(bundle, Mapping):
            raise TeacherConfirmedPortfolioLongitudinalError("source bundle must be a mapping")
        bundle_id = str(bundle.get("bundle_id") or "")
        if not bundle_id or bundle_id in out:
            raise TeacherConfirmedPortfolioLongitudinalError("source bundle_id missing or duplicated")
        out[bundle_id] = bundle
    return out


def _require_false(payload: Mapping[str, Any], fields: Sequence[str]) -> None:
    for field in fields:
        if payload.get(field) is not False:
            raise TeacherConfirmedPortfolioLongitudinalError(f"portfolio decision boundary weakened: {field}")


def build_portfolio_teacher_confirmed_longitudinal_report(
    portfolio: Mapping[str, Any],
    bundles: Sequence[Mapping[str, Any]],
    portfolio_decision_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Aggregate only teacher-confirmed technical relevance across review bundles.

    This is a portfolio projection over already verified source bundles and their
    explicit teacher-decision ledgers. It never merges review identities and never
    turns technical relevance into student-error, causal or methodology claims.
    """
    verify_teacher_review_portfolio(portfolio, bundles)
    by_bundle = _bundle_map(bundles)

    if portfolio_decision_result.get("schema") != "tournament-teacher-review-portfolio-decision-result-v1":
        raise TeacherConfirmedPortfolioLongitudinalError("unsupported portfolio decision result schema")
    if portfolio_decision_result.get("portfolio_id") != portfolio.get("portfolio_id"):
        raise TeacherConfirmedPortfolioLongitudinalError("portfolio decision result portfolio_id mismatch")
    _require_false(
        portfolio_decision_result,
        (
            "automatic_decisions_allowed",
            "automatic_methodology_mapping_allowed",
            "automatic_student_error_attribution_allowed",
        ),
    )

    bundle_results = portfolio_decision_result.get("bundle_results")
    if not isinstance(bundle_results, Sequence) or isinstance(bundle_results, (str, bytes)):
        raise TeacherConfirmedPortfolioLongitudinalError("bundle_results must be a sequence")
    if int(portfolio_decision_result.get("source_bundle_count", -1)) != len(by_bundle):
        raise TeacherConfirmedPortfolioLongitudinalError("source bundle count mismatch")
    if len(bundle_results) != len(by_bundle):
        raise TeacherConfirmedPortfolioLongitudinalError("bundle result cardinality mismatch")

    result_by_bundle: dict[str, Mapping[str, Any]] = {}
    for raw in bundle_results:
        if not isinstance(raw, Mapping):
            raise TeacherConfirmedPortfolioLongitudinalError("bundle result must be a mapping")
        bundle_id = str(raw.get("source_bundle_id") or "")
        if bundle_id not in by_bundle or bundle_id in result_by_bundle:
            raise TeacherConfirmedPortfolioLongitudinalError("bundle result identity unknown or duplicated")
        if not isinstance(raw.get("ledger"), Mapping):
            raise TeacherConfirmedPortfolioLongitudinalError("bundle result lacks decision ledger")
        result_by_bundle[bundle_id] = raw
    if set(result_by_bundle) != set(by_bundle):
        raise TeacherConfirmedPortfolioLongitudinalError("portfolio decision result does not cover exact source bundles")

    status_counts: Counter[str] = Counter()
    confirmed_items: list[dict[str, Any]] = []
    bundle_reports: list[dict[str, Any]] = []
    seen_review_keys: set[tuple[str, str]] = set()

    for bundle_id in sorted(by_bundle):
        result = result_by_bundle[bundle_id]
        report = build_teacher_confirmed_longitudinal_report(by_bundle[bundle_id], result["ledger"])
        if str(report.get("bundle_id") or "") != bundle_id:
            raise TeacherConfirmedPortfolioLongitudinalError("source longitudinal report bundle_id mismatch")
        counts = report.get("status_counts")
        if not isinstance(counts, Mapping):
            raise TeacherConfirmedPortfolioLongitudinalError("source longitudinal status_counts malformed")
        for status in TeacherDecisionStatus:
            status_counts[status.value] += int(counts.get(status.value, 0))

        source_confirmed = report.get("confirmed_items")
        if not isinstance(source_confirmed, Sequence) or isinstance(source_confirmed, (str, bytes)):
            raise TeacherConfirmedPortfolioLongitudinalError("source confirmed_items malformed")
        for raw in source_confirmed:
            if not isinstance(raw, Mapping):
                raise TeacherConfirmedPortfolioLongitudinalError("confirmed item must be a mapping")
            review_id = str(raw.get("review_id") or "")
            key = (bundle_id, review_id)
            if not review_id or key in seen_review_keys:
                raise TeacherConfirmedPortfolioLongitudinalError("confirmed review identity missing or duplicated")
            seen_review_keys.add(key)
            if raw.get("student_error_attribution") is not None or raw.get("methodology_mapping") is not None:
                raise TeacherConfirmedPortfolioLongitudinalError("source confirmed item contains pedagogical attribution")
            if raw.get("causal_link") != "NOT_ESTABLISHED":
                raise TeacherConfirmedPortfolioLongitudinalError("source confirmed item weakened causal boundary")
            confirmed_items.append({"source_bundle_id": bundle_id, **dict(raw)})
        bundle_reports.append(
            {
                "source_bundle_id": bundle_id,
                "queue_sha256": report.get("queue_sha256"),
                "status_counts": dict(counts),
                "confirmed_count": len(source_confirmed),
            }
        )

    total = sum(status_counts.values())
    if total != int(portfolio_decision_result.get("decided_count", -1)) + int(
        portfolio_decision_result.get("pending_count", -1)
    ):
        raise TeacherConfirmedPortfolioLongitudinalError("portfolio decision/status totals disagree")
    if status_counts[TeacherDecisionStatus.PENDING.value] != int(portfolio_decision_result.get("pending_count", -1)):
        raise TeacherConfirmedPortfolioLongitudinalError("portfolio pending count disagrees with source ledgers")
    decided = total - status_counts[TeacherDecisionStatus.PENDING.value]
    if decided != int(portfolio_decision_result.get("decided_count", -1)):
        raise TeacherConfirmedPortfolioLongitudinalError("portfolio decided count disagrees with source ledgers")

    clusters_raw: dict[str, list[dict[str, Any]]] = {}
    confirmed_without_repeat_key = 0
    for row in confirmed_items:
        repeat_key = row.get("repeat_key")
        if repeat_key in (None, ""):
            confirmed_without_repeat_key += 1
        else:
            clusters_raw.setdefault(str(repeat_key), []).append(row)

    clusters: list[dict[str, Any]] = []
    for repeat_key, rows in clusters_raw.items():
        event_ids = sorted({str(row.get("event_id") or "") for row in rows})
        bundle_ids = sorted({str(row["source_bundle_id"]) for row in rows})
        categories = Counter(str(row.get("category") or "") for row in rows)
        clusters.append(
            {
                "repeat_key": repeat_key,
                "finding_count": len(rows),
                "event_count": len(event_ids),
                "event_ids": event_ids,
                "source_bundle_ids": bundle_ids,
                "category_counts": dict(sorted(categories.items())),
                "persistent_across_events": len(event_ids) >= 2,
                "technical_trick_loss_mass": sum(abs(float(row.get("technical_trick_loss") or 0.0)) for row in rows),
                "causal_link": "NOT_ESTABLISHED",
                "student_error_attribution": None,
                "methodology_mapping": None,
            }
        )
    clusters.sort(
        key=lambda row: (
            bool(row["persistent_across_events"]),
            int(row["event_count"]),
            float(row["technical_trick_loss_mass"]),
            str(row["repeat_key"]),
        ),
        reverse=True,
    )

    return {
        "schema": "tournament-teacher-confirmed-portfolio-longitudinal-v1",
        "portfolio_id": portfolio["portfolio_id"],
        "teacher_decision_gate_enforced": True,
        "technical_relevance_only": True,
        "cross_bundle_review_identity_preserved": True,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "causal_error_attribution_allowed": False,
        "status_counts": {status.value: int(status_counts.get(status.value, 0)) for status in TeacherDecisionStatus},
        "confirmed_items": confirmed_items,
        "confirmed_without_repeat_key": confirmed_without_repeat_key,
        "clusters": clusters,
        "persistent_clusters": [row for row in clusters if row["persistent_across_events"]],
        "bundle_reports": bundle_reports,
        "interpretation": (
            "Only explicit CONFIRMED_TECHNICAL_RELEVANCE decisions from each original source bundle enter this "
            "portfolio longitudinal view. Cross-bundle aggregation preserves source_bundle_id and does not establish "
            "student error, causality, methodology mapping or a teaching category."
        ),
    }
