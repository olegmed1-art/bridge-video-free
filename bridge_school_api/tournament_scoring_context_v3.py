from __future__ import annotations

import copy
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from .tournament_adapters_v3 import TournamentAdapterError
from .tournament_real_sources_v3 import normalize_30041_facts, validate_29912_report_contract


REPORT_SCHEMA_V1 = "tournament-longitudinal-real-evidence-v1"
REPORT_SCHEMA_V2 = "tournament-longitudinal-real-evidence-v2"


class TournamentScoringContextError(ValueError):
    pass


@dataclass(frozen=True)
class ScoringObservation:
    deal_id: str
    metric: str
    value: float
    unit: str
    neutral_value: float | None
    centered_value: float | None
    comparable_scope: str
    provenance: Mapping[str, Any] = field(default_factory=dict)


def _finite_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise TournamentScoringContextError(f"boolean is not a valid {field_name}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TournamentScoringContextError(f"invalid {field_name}: {value!r}") from exc
    if not math.isfinite(number):
        raise TournamentScoringContextError(f"non-finite {field_name}: {value!r}")
    return number


def scoring_observations_30041(source: Mapping[str, Any]) -> tuple[ScoringObservation, ...]:
    """Extract source-native board percentage context from the audited 30041 source.

    Pair percentage is an observed tournament-result metric. Centering it on 50 is
    simple arithmetic only; it is not interpreted as loss caused by a DDS3 finding.
    """
    batch = normalize_30041_facts(source)
    if batch.event_id != "30041" or batch.session_id != "round-2" or batch.scoring != "MP":
        raise TournamentScoringContextError("unexpected canonical 30041 batch identity")

    out: list[ScoringObservation] = []
    for deal in batch.deals:
        raw = deal.source_provenance.get("pair_percentage")
        if raw is None:
            continue
        pct = _finite_number(raw, field_name="pair_percentage")
        if not 0.0 <= pct <= 100.0:
            raise TournamentScoringContextError(f"pair_percentage outside [0, 100]: {pct}")
        out.append(
            ScoringObservation(
                deal_id=deal.deal_id,
                metric="pair_percentage",
                value=pct,
                unit="percentage",
                neutral_value=50.0,
                centered_value=pct - 50.0,
                comparable_scope="event:30041",
                provenance={
                    "event": 30041,
                    "session": "round-2",
                    "board": deal.board_number,
                    "status": deal.source_provenance.get("status"),
                    "source_kind": batch.source.get("kind"),
                },
            )
        )

    # Exact audited source: 21 played + 1 adjusted/average result, 2 unplayed.
    if len(out) != 22:
        raise TournamentScoringContextError(f"expected 22 30041 scoring observations, got {len(out)}")
    if len({x.deal_id for x in out}) != len(out):
        raise TournamentScoringContextError("duplicate 30041 scoring deal identity")
    return tuple(out)


def scoring_observations_29912(report: Mapping[str, Any]) -> tuple[ScoringObservation, ...]:
    """Extract source-native per-board matchpoint context from audited 29912 evidence.

    The source-native MP values are deliberately session-scoped. No assumption is
    made that the scale is comparable across rounds or with 30041 percentages.
    """
    try:
        validate_29912_report_contract(report)
    except TournamentAdapterError as exc:
        raise TournamentScoringContextError(str(exc)) from exc

    out: list[ScoringObservation] = []
    for session in report["sessions"]:
        round_no = int(session["round"])
        boards = session.get("boards")
        if not isinstance(boards, Sequence) or isinstance(boards, (str, bytes)):
            raise TournamentScoringContextError(f"round {round_no} boards must be a sequence")
        for board in boards:
            if not isinstance(board, Mapping):
                raise TournamentScoringContextError("29912 board must be a mapping")
            board_no = int(board["board"])
            value = _finite_number(board.get("pair_matchpoints"), field_name="pair_matchpoints")
            consistency = board.get("source_consistency")
            consistency_ok = isinstance(consistency, Mapping) and consistency.get("ok") is True
            out.append(
                ScoringObservation(
                    deal_id=f"29912:round-{round_no}:{board_no}",
                    metric="pair_matchpoints",
                    value=value,
                    unit="source-native MP",
                    neutral_value=None,
                    centered_value=None,
                    comparable_scope=f"event:29912:round-{round_no}",
                    provenance={
                        "event": 29912,
                        "round": round_no,
                        "board": board_no,
                        "pair_direction": board.get("pair_direction"),
                        "pair_score": board.get("pair_score"),
                        "source_consistency_ok": consistency_ok,
                    },
                )
            )

    if len(out) != 100:
        raise TournamentScoringContextError(f"expected 100 29912 scoring observations, got {len(out)}")
    if len({x.deal_id for x in out}) != len(out):
        raise TournamentScoringContextError("duplicate 29912 scoring deal identity")
    return tuple(out)


def build_real_scoring_context(
    source_30041: Mapping[str, Any],
    dds3_29912: Mapping[str, Any],
) -> Mapping[str, ScoringObservation]:
    observations = (*scoring_observations_30041(source_30041), *scoring_observations_29912(dds3_29912))
    by_deal: dict[str, ScoringObservation] = {}
    for observation in observations:
        if observation.deal_id in by_deal:
            raise TournamentScoringContextError(f"duplicate cross-source scoring identity: {observation.deal_id}")
        by_deal[observation.deal_id] = observation
    return by_deal


def _observation_dict(observation: ScoringObservation) -> dict[str, Any]:
    return asdict(observation) | {"provenance": dict(observation.provenance)}


def serialize_scoring_context(context: Mapping[str, ScoringObservation]) -> dict[str, Any]:
    counts = {"30041": 0, "29912": 0}
    for deal_id in context:
        event_id = deal_id.split(":", 1)[0]
        if event_id not in counts:
            raise TournamentScoringContextError(f"unexpected scoring event: {event_id}")
        counts[event_id] += 1
    return {
        "policy": {
            "causal_attribution_allowed": False,
            "cross_event_aggregation_allowed": False,
            "source_native_units_preserved": True,
            "populates_tournament_impact": False,
        },
        "coverage": {**counts, "total": sum(counts.values())},
        "observations": {deal_id: _observation_dict(context[deal_id]) for deal_id in sorted(context)},
    }


def attach_scoring_context(
    report: Mapping[str, Any],
    context: Mapping[str, ScoringObservation],
) -> dict[str, Any]:
    """Attach non-causal source-native scoring context to every technical finding.

    This function never writes ``tournament_impact`` and never turns a tournament
    score into a skill/error attribution. Missing context for a finding fails closed.
    """
    if report.get("schema") != REPORT_SCHEMA_V1:
        raise TournamentScoringContextError(f"expected {REPORT_SCHEMA_V1}")
    out = copy.deepcopy(dict(report))
    events = out.get("events")
    if not isinstance(events, Mapping):
        raise TournamentScoringContextError("report events must be a mapping")

    coverage: dict[str, dict[str, int]] = {}
    for event_id, event in events.items():
        if not isinstance(event, Mapping):
            raise TournamentScoringContextError(f"event {event_id} must be a mapping")
        findings = event.get("findings")
        if not isinstance(findings, list):
            raise TournamentScoringContextError(f"event {event_id} findings must be a list")
        with_context = 0
        for finding in findings:
            if not isinstance(finding, dict):
                raise TournamentScoringContextError("finding must be a mapping")
            deal_id = str(finding.get("deal_id") or "")
            observation = context.get(deal_id)
            if observation is None:
                raise TournamentScoringContextError(f"missing scoring context for finding {deal_id}")
            finding["scoring_context"] = _observation_dict(observation)
            with_context += 1
        coverage[str(event_id)] = {"findings": len(findings), "with_context": with_context}

    serialized = serialize_scoring_context(context)
    serialized["finding_coverage"] = coverage
    out["base_schema"] = REPORT_SCHEMA_V1
    out["schema"] = REPORT_SCHEMA_V2
    out["scoring_context"] = serialized
    policy = out.setdefault("policy", {})
    if not isinstance(policy, dict):
        raise TournamentScoringContextError("report policy must be a mapping")
    policy.update(
        {
            "scoring_context_is_causal_loss": False,
            "cross_event_scoring_aggregation_allowed": False,
            "scoring_context_populates_tournament_impact": False,
        }
    )
    return out
