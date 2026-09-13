from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from .tournament_analyzer_v3 import AnalysisFinding, TournamentAnalysis


@dataclass(frozen=True)
class RepeatCluster:
    repeat_key: str
    tournament_count: int
    finding_count: int
    total_trick_loss: float
    total_score_loss: float
    total_tournament_impact: float
    event_ids: tuple[str, ...]

    @property
    def recoverable_loss(self) -> float:
        """Evidence-only aggregate, not a claim that training guarantees recovery."""
        if self.total_tournament_impact:
            return self.total_tournament_impact
        if self.total_score_loss:
            return self.total_score_loss
        return self.total_trick_loss


@dataclass(frozen=True)
class LongitudinalReport:
    clusters: tuple[RepeatCluster, ...]
    persistent: tuple[RepeatCluster, ...]
    single_event: tuple[RepeatCluster, ...]


def _magnitude(value: float | None) -> float:
    return abs(float(value or 0.0))


def build_longitudinal_report(analyses: Sequence[TournamentAnalysis]) -> LongitudinalReport:
    """Cluster only explicit repeat_key values supplied by upstream analysis.

    No bridge pedagogy or category mapping is invented here. Findings without a
    repeat_key are deliberately excluded from longitudinal skill attribution.
    """
    by_key: dict[str, list[tuple[str, AnalysisFinding]]] = {}
    for analysis in analyses:
        for finding in analysis.findings:
            if not finding.repeat_key:
                continue
            by_key.setdefault(finding.repeat_key, []).append((analysis.event_id, finding))

    clusters: list[RepeatCluster] = []
    for key, items in by_key.items():
        event_ids = tuple(sorted({event_id for event_id, _ in items}))
        clusters.append(
            RepeatCluster(
                repeat_key=key,
                tournament_count=len(event_ids),
                finding_count=len(items),
                total_trick_loss=sum(_magnitude(f.trick_loss) for _, f in items),
                total_score_loss=sum(_magnitude(f.score_loss) for _, f in items),
                total_tournament_impact=sum(_magnitude(f.tournament_impact) for _, f in items),
                event_ids=event_ids,
            )
        )

    ranked = tuple(
        sorted(
            clusters,
            key=lambda c: (c.tournament_count, c.recoverable_loss, c.finding_count, c.repeat_key),
            reverse=True,
        )
    )
    persistent = tuple(c for c in ranked if c.tournament_count >= 2)
    single_event = tuple(c for c in ranked if c.tournament_count == 1)
    return LongitudinalReport(clusters=ranked, persistent=persistent, single_event=single_event)


def category_recoverable_loss(analysis: TournamentAnalysis) -> Mapping[str, float]:
    """Return observed loss mass by category without asserting causality."""
    out: dict[str, float] = {}
    for category, totals in analysis.category_totals.items():
        impact = abs(float(totals.get("tournament_impact", 0.0)))
        score = abs(float(totals.get("score_loss", 0.0)))
        tricks = abs(float(totals.get("trick_loss", 0.0)))
        out[category] = impact or score or tricks
    return out


def _jsonable(value: Any) -> Any:
    """Convert analysis dataclasses/enums into deterministic JSON-safe values."""
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported longitudinal provenance value: {type(value).__name__}")


def _sha256(value: Any) -> str:
    raw = json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_longitudinal_provenance_receipt(
    analyses: Sequence[TournamentAnalysis],
    report: LongitudinalReport,
) -> dict[str, Any]:
    """Bind a longitudinal report to the exact upstream TournamentAnalysis values.

    This receipt is technical provenance only. It does not promote a repeat_key to a
    school methodology rule, attribute a student error, or claim causal/recoverable
    training effect. The supplied report must equal a fresh deterministic rebuild
    from the supplied analyses, so a stale or caller-modified report fails closed.
    """
    rebuilt = build_longitudinal_report(analyses)
    if report != rebuilt:
        raise ValueError("longitudinal report does not match supplied analyses")

    analysis_digests = tuple(
        {
            "event_id": analysis.event_id,
            "sha256": _sha256(analysis),
        }
        for analysis in analyses
    )
    identity = {
        "schema": "tournament-longitudinal-provenance-receipt-v1",
        "analysis_digests": analysis_digests,
        "report_sha256": _sha256(report),
        "event_ids": tuple(sorted({analysis.event_id for analysis in analyses})),
        "analysis_count": len(analyses),
        "cluster_count": len(report.clusters),
        "persistent_cluster_count": len(report.persistent),
        "automatic_methodology_mapping_used": False,
        "automatic_student_error_attribution_used": False,
        "causal_training_effect_claimed": False,
        "recoverable_loss_guarantee_claimed": False,
    }
    return {
        **identity,
        "receipt_id": _sha256(identity),
        "content_addressed": True,
    }


def verify_longitudinal_provenance_receipt(
    analyses: Sequence[TournamentAnalysis],
    report: LongitudinalReport,
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify a persisted receipt against exact upstream analyses and report.

    JSON round-tripping converts tuples to lists, so verification compares the
    deterministic JSON-safe representation rather than Python container types. Any
    changed evidence, report field, digest, safety boundary, or receipt marker fails
    closed by differing from a freshly rebuilt receipt.
    """
    if not isinstance(receipt, Mapping):
        raise ValueError("longitudinal provenance receipt must be a mapping")

    expected = build_longitudinal_provenance_receipt(analyses, report)
    if _jsonable(receipt) != _jsonable(expected):
        raise ValueError("longitudinal provenance receipt does not match supplied evidence")

    return {
        "schema": "tournament-longitudinal-provenance-verification-v1",
        "receipt_id": expected["receipt_id"],
        "report_sha256": expected["report_sha256"],
        "status": "PASS",
        "exact_upstream_evidence_verified": True,
        "longitudinal_safety_boundaries_verified": True,
    }
