from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence


class EvidenceKind(str, Enum):
    FACT = "FACT"
    DDS_FACT = "DDS_FACT"
    SYSTEM_RULE = "SYSTEM_RULE"
    MODEL_OPINION = "MODEL_OPINION"
    TEACHER_REVIEW = "TEACHER_REVIEW"


class Observability(str, Enum):
    OBSERVABLE = "OBSERVABLE"
    NOT_OBSERVABLE = "NOT_OBSERVABLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Evidence:
    kind: EvidenceKind
    message: str
    provenance: Mapping[str, Any] = field(default_factory=dict)
    confidence: float | None = None

    def __post_init__(self) -> None:
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be within [0, 1]")


@dataclass(frozen=True)
class TournamentDeal:
    event_id: str
    session_id: str
    board_number: int
    hands: Mapping[str, Sequence[str]]
    dealer: str | None = None
    vulnerability: str | None = None
    auction: Sequence[str] | None = None
    contract: str | None = None
    declarer: str | None = None
    opening_lead: str | None = None
    score: int | None = None
    datum: float | None = None
    play_record: Sequence[str] | None = None
    source_provenance: Mapping[str, Any] = field(default_factory=dict)

    @property
    def deal_id(self) -> str:
        return f"{self.event_id}:{self.session_id}:{self.board_number}"


@dataclass(frozen=True)
class ContractBaseline:
    par_score: int | None
    dd_tricks_for_played_contract: int | None
    played_tricks: int | None
    trick_delta: int | None
    score_loss: float | None
    tournament_impact: float | None
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class AnalysisFinding:
    deal_id: str
    category: str
    summary: str
    evidence: tuple[Evidence, ...]
    trick_loss: float | None = None
    score_loss: float | None = None
    tournament_impact: float | None = None
    observability: Observability = Observability.UNKNOWN
    repeat_key: str | None = None


@dataclass(frozen=True)
class TournamentAnalysis:
    event_id: str
    findings: tuple[AnalysisFinding, ...]
    ranked_findings: tuple[AnalysisFinding, ...]
    category_totals: Mapping[str, Mapping[str, float]]
    student_summary: tuple[str, ...]
    teacher_summary: tuple[str, ...]


class DealIntegrityError(ValueError):
    pass


def _normalise_card(card: str) -> str:
    return card.strip().upper().replace("10", "T")


def validate_deal_integrity(deal: TournamentDeal) -> None:
    seats = {str(k).upper(): tuple(v) for k, v in deal.hands.items()}
    if set(seats) != {"N", "E", "S", "W"}:
        raise DealIntegrityError("hands must contain exactly N/E/S/W")

    cards = [_normalise_card(card) for seat in ("N", "E", "S", "W") for card in seats[seat]]
    if len(cards) != 52:
        raise DealIntegrityError(f"expected 52 cards, got {len(cards)}")
    if len(set(cards)) != 52:
        raise DealIntegrityError("duplicate cards detected")

    ranks = set("23456789TJQKA")
    suits = set("CDHS")
    for card in cards:
        if len(card) != 2 or card[0] not in ranks or card[1] not in suits:
            raise DealIntegrityError(f"invalid card token: {card}")

    if deal.board_number <= 0:
        raise DealIntegrityError("board_number must be positive")
    if not deal.event_id or not deal.session_id:
        raise DealIntegrityError("event_id and session_id are required to scope board identity")


def analysis_observability(deal: TournamentDeal) -> Observability:
    if deal.play_record is None:
        return Observability.NOT_OBSERVABLE
    if len(deal.play_record) == 0:
        return Observability.UNKNOWN
    return Observability.OBSERVABLE


def build_contract_baseline(
    *,
    par_score: int | None,
    dd_tricks_for_played_contract: int | None,
    played_tricks: int | None,
    actual_score: int | None,
    comparison_score: float | None = None,
    tournament_impact: float | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> ContractBaseline:
    trick_delta = None
    if dd_tricks_for_played_contract is not None and played_tricks is not None:
        trick_delta = dd_tricks_for_played_contract - played_tricks

    score_loss = None
    if actual_score is not None and comparison_score is not None:
        score_loss = max(0.0, float(comparison_score) - float(actual_score))

    evidence = (
        Evidence(
            EvidenceKind.DDS_FACT,
            "Contract-level DDS3 baseline; this is a double-dummy opportunity, not by itself a player-error attribution.",
            provenance=provenance or {},
            confidence=1.0,
        ),
    )
    return ContractBaseline(
        par_score=par_score,
        dd_tricks_for_played_contract=dd_tricks_for_played_contract,
        played_tricks=played_tricks,
        trick_delta=trick_delta,
        score_loss=score_loss,
        tournament_impact=tournament_impact,
        evidence=evidence,
    )


def finding_from_contract_baseline(
    deal: TournamentDeal,
    baseline: ContractBaseline,
    *,
    category: str = "contract_result",
) -> AnalysisFinding | None:
    if baseline.trick_delta in (None, 0) and not baseline.score_loss and not baseline.tournament_impact:
        return None
    return AnalysisFinding(
        deal_id=deal.deal_id,
        category=category,
        summary="Фактический результат отличается от доступной double-dummy возможности.",
        evidence=baseline.evidence,
        trick_loss=float(baseline.trick_delta) if baseline.trick_delta is not None else None,
        score_loss=baseline.score_loss,
        tournament_impact=baseline.tournament_impact,
        observability=analysis_observability(deal),
        repeat_key=category,
    )


def attach_system_rule(
    finding: AnalysisFinding,
    *,
    rule_id: str,
    message: str,
    provenance: Mapping[str, Any],
) -> AnalysisFinding:
    ev = Evidence(
        EvidenceKind.SYSTEM_RULE,
        message,
        provenance={**provenance, "rule_id": rule_id},
        confidence=1.0,
    )
    return AnalysisFinding(**{**finding.__dict__, "evidence": finding.evidence + (ev,)})


def attach_model_opinion(
    finding: AnalysisFinding,
    *,
    message: str,
    confidence: float,
    provenance: Mapping[str, Any],
) -> AnalysisFinding:
    ev = Evidence(EvidenceKind.MODEL_OPINION, message, provenance=provenance, confidence=confidence)
    return AnalysisFinding(**{**finding.__dict__, "evidence": finding.evidence + (ev,)})


def teacher_review_finding(
    deal: TournamentDeal,
    *,
    category: str,
    summary: str,
    reason: str,
) -> AnalysisFinding:
    return AnalysisFinding(
        deal_id=deal.deal_id,
        category=category,
        summary=summary,
        evidence=(Evidence(EvidenceKind.TEACHER_REVIEW, reason),),
        observability=analysis_observability(deal),
        repeat_key=category,
    )


def _impact_value(finding: AnalysisFinding) -> float:
    if finding.tournament_impact is not None:
        return abs(float(finding.tournament_impact))
    if finding.score_loss is not None:
        return abs(float(finding.score_loss))
    if finding.trick_loss is not None:
        return abs(float(finding.trick_loss))
    return 0.0


def rank_findings(findings: Iterable[AnalysisFinding]) -> tuple[AnalysisFinding, ...]:
    return tuple(sorted(findings, key=lambda f: (_impact_value(f), f.deal_id), reverse=True))


def aggregate_categories(findings: Iterable[AnalysisFinding]) -> dict[str, dict[str, float]]:
    totals: dict[str, dict[str, float]] = {}
    for finding in findings:
        bucket = totals.setdefault(
            finding.category,
            {"count": 0.0, "trick_loss": 0.0, "score_loss": 0.0, "tournament_impact": 0.0},
        )
        bucket["count"] += 1.0
        bucket["trick_loss"] += abs(float(finding.trick_loss or 0.0))
        bucket["score_loss"] += abs(float(finding.score_loss or 0.0))
        bucket["tournament_impact"] += abs(float(finding.tournament_impact or 0.0))
    return totals


def build_student_summary(ranked: Sequence[AnalysisFinding], *, limit: int = 5) -> tuple[str, ...]:
    lines: list[str] = []
    for finding in ranked[:limit]:
        impact = finding.tournament_impact
        suffix = f"; влияние на турнир: {impact:.2f}" if impact is not None else ""
        lines.append(f"{finding.deal_id}: {finding.summary}{suffix}")
    return tuple(lines)


def build_teacher_summary(ranked: Sequence[AnalysisFinding]) -> tuple[str, ...]:
    lines: list[str] = []
    for finding in ranked:
        kinds = ",".join(sorted({e.kind.value for e in finding.evidence}))
        lines.append(
            f"{finding.deal_id} [{finding.category}] {finding.summary} | evidence={kinds} | "
            f"observability={finding.observability.value}"
        )
    return tuple(lines)


def analyze_tournament(
    deals: Sequence[TournamentDeal],
    findings: Sequence[AnalysisFinding],
) -> TournamentAnalysis:
    if not deals:
        raise ValueError("at least one deal is required")
    event_ids = {deal.event_id for deal in deals}
    if len(event_ids) != 1:
        raise ValueError("all deals must belong to one event")

    seen: set[str] = set()
    for deal in deals:
        validate_deal_integrity(deal)
        if deal.deal_id in seen:
            raise DealIntegrityError(f"duplicate scoped deal identity: {deal.deal_id}")
        seen.add(deal.deal_id)

    unknown_finding_deals = {f.deal_id for f in findings} - seen
    if unknown_finding_deals:
        raise DealIntegrityError(f"findings reference unknown deals: {sorted(unknown_finding_deals)}")

    ranked = rank_findings(findings)
    return TournamentAnalysis(
        event_id=next(iter(event_ids)),
        findings=tuple(findings),
        ranked_findings=ranked,
        category_totals=aggregate_categories(findings),
        student_summary=build_student_summary(ranked),
        teacher_summary=build_teacher_summary(ranked),
    )
