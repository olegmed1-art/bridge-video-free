from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

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
