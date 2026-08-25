from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from .tournament_teacher_review_bundle_v3 import verify_teacher_review_bundle


class TeacherReviewPortfolioError(ValueError):
    pass


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _bundle_index(bundle: Mapping[str, Any]) -> list[dict[str, Any]]:
    verify_teacher_review_bundle(bundle)
    if bundle.get("review_state") != "PENDING_TEACHER_DECISION":
        raise TeacherReviewPortfolioError("portfolio accepts pending portable bundles only")
    if bundle.get("automatic_decisions_allowed") is not False:
        raise TeacherReviewPortfolioError("bundle automatic decision boundary was weakened")
    if bundle.get("automatic_methodology_mapping_allowed") is not False:
        raise TeacherReviewPortfolioError("bundle methodology boundary was weakened")
    if bundle.get("automatic_student_error_attribution_allowed") is not False:
        raise TeacherReviewPortfolioError("bundle student-attribution boundary was weakened")
    if bundle.get("cross_event_numeric_ranking_allowed") is not False:
        raise TeacherReviewPortfolioError("bundle cross-event ranking boundary was weakened")

    components = bundle["components"]
    ledger = components["ledger"]
    dossier = components["dossier"]
    decisions = ledger.get("decisions")
    items = dossier.get("items")
    if not isinstance(decisions, Sequence) or isinstance(decisions, (str, bytes)):
        raise TeacherReviewPortfolioError("bundle ledger decisions are malformed")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        raise TeacherReviewPortfolioError("bundle dossier items are malformed")

    decision_by_id: dict[str, Mapping[str, Any]] = {}
    for raw in decisions:
        if not isinstance(raw, Mapping):
            raise TeacherReviewPortfolioError("bundle decision is malformed")
        review_id = str(raw.get("review_id") or "")
        if not review_id or review_id in decision_by_id:
            raise TeacherReviewPortfolioError("bundle review_id missing or duplicated")
        if raw.get("status") != "PENDING" or raw.get("teacher_decision_required") is not True:
            raise TeacherReviewPortfolioError("portfolio accepts unresolved teacher decisions only")
        decision_by_id[review_id] = raw

    out: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, Mapping):
            raise TeacherReviewPortfolioError("bundle dossier item is malformed")
        review_id = str(raw.get("review_id") or "")
        decision = decision_by_id.get(review_id)
        if decision is None:
            raise TeacherReviewPortfolioError("dossier review_id absent from decision ledger")
        event_id = str(raw.get("event_id") or "")
        deal_id = str(raw.get("deal_id") or "")
        category = str(raw.get("category") or "")
        if not event_id or not deal_id or not category:
            raise TeacherReviewPortfolioError("dossier identity is incomplete")
        if (
            event_id != str(decision.get("event_id") or "")
            or deal_id != str(decision.get("deal_id") or "")
            or category != str(decision.get("category") or "")
        ):
            raise TeacherReviewPortfolioError("dossier/decision identity mismatch")
        if raw.get("causal_link") != "NOT_ESTABLISHED":
            raise TeacherReviewPortfolioError("portfolio cannot ingest causal attribution")
        if raw.get("methodology_mapping") is not None or raw.get("student_error_attribution") is not None:
            raise TeacherReviewPortfolioError("portfolio cannot ingest pedagogical attribution")
        out.append(
            {
                "review_id": review_id,
                "event_id": event_id,
                "deal_id": deal_id,
                "category": category,
                "status": "PENDING",
                "teacher_decision_required": True,
                "causal_link": "NOT_ESTABLISHED",
                "methodology_mapping": None,
                "student_error_attribution": None,
                "queue_item_sha256": str(decision.get("queue_item_sha256") or ""),
            }
        )
    if len(out) != int(bundle.get("item_count") or -1):
        raise TeacherReviewPortfolioError("bundle item_count mismatch")
    return out


def build_teacher_review_portfolio(bundles: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not isinstance(bundles, Sequence) or isinstance(bundles, (str, bytes)) or not bundles:
        raise TeacherReviewPortfolioError("at least one portable teacher-review bundle is required")

    normalized: list[tuple[str, Mapping[str, Any], list[dict[str, Any]]]] = []
    seen_bundle_ids: set[str] = set()
    for bundle in bundles:
        if not isinstance(bundle, Mapping):
            raise TeacherReviewPortfolioError("portfolio bundle must be a mapping")
        if bundle.get("schema") != "tournament-teacher-review-portable-bundle-v1":
            raise TeacherReviewPortfolioError("unsupported portfolio bundle schema")
        bundle_id = str(bundle.get("bundle_id") or "")
        if len(bundle_id) != 64 or bundle_id in seen_bundle_ids:
            raise TeacherReviewPortfolioError("bundle_id missing or duplicated")
        seen_bundle_ids.add(bundle_id)
        normalized.append((bundle_id, bundle, _bundle_index(bundle)))

    normalized.sort(key=lambda row: row[0])
    all_items: list[dict[str, Any]] = []
    logical_seen: set[tuple[str, str, str]] = set()
    review_seen: set[str] = set()
    event_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    deal_signals: dict[tuple[str, str], list[dict[str, Any]]] = {}
    batches: list[dict[str, Any]] = []

    for bundle_id, bundle, items in normalized:
        batches.append(
            {
                "bundle_id": bundle_id,
                "bundle_sha256": _sha256(bundle),
                "item_count": len(items),
                "event_counts": dict(bundle.get("event_counts") or {}),
            }
        )
        for item in items:
            review_id = item["review_id"]
            logical_key = (item["event_id"], item["deal_id"], item["category"])
            if review_id in review_seen:
                raise TeacherReviewPortfolioError("review_id reused across portable bundles")
            if logical_key in logical_seen:
                raise TeacherReviewPortfolioError(
                    f"same event/deal/category appears in multiple review bundles: {logical_key}"
                )
            review_seen.add(review_id)
            logical_seen.add(logical_key)
            indexed = {**item, "source_bundle_id": bundle_id}
            all_items.append(indexed)
            event_counts[item["event_id"]] = event_counts.get(item["event_id"], 0) + 1
            category_counts[item["category"]] = category_counts.get(item["category"], 0) + 1
            deal_signals.setdefault((item["event_id"], item["deal_id"]), []).append(indexed)

    all_items.sort(key=lambda x: (x["event_id"], x["deal_id"], x["category"], x["review_id"]))
    multi_signal_deals: list[dict[str, Any]] = []
    for (event_id, deal_id), rows in sorted(deal_signals.items()):
        if len(rows) < 2:
            continue
        multi_signal_deals.append(
            {
                "event_id": event_id,
                "deal_id": deal_id,
                "signal_count": len(rows),
                "categories": sorted({str(row["category"]) for row in rows}),
                "review_ids": sorted(str(row["review_id"]) for row in rows),
                "causal_collapse_allowed": False,
                "interpretation": (
                    "Several independent technical evidence families refer to the same deal. "
                    "They remain separate review questions and are not one inferred student error."
                ),
            }
        )

    identity = {
        "schema": "tournament-teacher-review-portfolio-v1",
        "bundle_ids": [row["bundle_id"] for row in batches],
        "bundle_sha256": [row["bundle_sha256"] for row in batches],
    }
    portfolio_id = _sha256(identity)
    return {
        "schema": "tournament-teacher-review-portfolio-v1",
        "portfolio_id": portfolio_id,
        "source_bundle_count": len(batches),
        "source_bundles": batches,
        "item_count": len(all_items),
        "pending_decision_count": len(all_items),
        "event_counts": dict(sorted(event_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "multi_signal_deal_count": len(multi_signal_deals),
        "multi_signal_deals": multi_signal_deals,
        "review_state": "BLOCKED_PENDING_TEACHER_DECISIONS",
        "automatic_decisions_allowed": False,
        "automatic_methodology_mapping_allowed": False,
        "automatic_student_error_attribution_allowed": False,
        "cross_batch_numeric_ranking_allowed": False,
        "cross_category_causal_collapse_allowed": False,
        "historical_bundle_mutation_allowed": False,
        "items": all_items,
        "interpretation": (
            "Hash-bound portfolio over independent pending teacher-review batches. "
            "It makes the growing review workload complete and auditable without changing old review IDs, "
            "combining evidence families into a causal claim, or creating methodology/student attribution."
        ),
    }


def verify_teacher_review_portfolio(
    portfolio_payload: Mapping[str, Any], bundles: Sequence[Mapping[str, Any]]
) -> None:
    if portfolio_payload.get("schema") != "tournament-teacher-review-portfolio-v1":
        raise TeacherReviewPortfolioError("unsupported teacher-review portfolio schema")
    rebuilt = build_teacher_review_portfolio(bundles)
    if portfolio_payload != rebuilt:
        raise TeacherReviewPortfolioError("teacher-review portfolio verification failed")
