"""Fail-closed two-lane resolver for SCHOOL CANON and WORLD knowledge.

This module is deliberately storage-neutral.  The database catalog supplies only
records from one authority lane at a time; the resolver preserves that ordering
and emits a complete, serialisable decision trace for later persistence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


CANON_GAP = "CANON_GAP"
CANON_CONFLICT = "CANON_CONFLICT"
WORLD_FALLBACK = "WORLD_FALLBACK"
WORLD_CONFLICT = "WORLD_CONFLICT"
UNRESOLVED_GAP = "UNRESOLVED_GAP"


@dataclass(frozen=True)
class KnowledgeRule:
    rule_id: str
    authority_class: str
    action: str
    priority: int = 0
    specificity: int = 0
    confidence: str = "unknown"
    applicable: bool = True
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Resolution:
    outcome: str
    selected: KnowledgeRule | None
    canon_candidates: tuple[KnowledgeRule, ...]
    world_candidates: tuple[KnowledgeRule, ...]
    trace: dict[str, Any]


def learner_response(resolution: Resolution) -> dict[str, Any]:
    """Return a safe, display-ready result without weakening authority gates.

    In particular, a CANON_CONFLICT is visible to the learner as an explicit
    pending clarification, never as a selected action or an invitation to use
    WORLD as a tie-breaker.
    """
    if resolution.outcome == "CANON_MATCH":
        return {"status": "ANSWER", "authority": "SCHOOL_CANON", "action": resolution.selected.action if resolution.selected else None, "message": "Ответ дан по утверждённому правилу Школы."}
    if resolution.outcome == CANON_CONFLICT:
        return {"status": "PENDING_CANON_CLARIFICATION", "authority": "SCHOOL_CANON", "action": None, "message": "В утверждённых правилах Школы есть конфликт для этой ситуации. Единая рекомендация пока не выдана.", "conflicting_rule_ids": [r.rule_id for r in resolution.canon_candidates]}
    if resolution.outcome == WORLD_FALLBACK:
        return {"status": "ANSWER", "authority": "WORLD_FALLBACK", "action": resolution.selected.action if resolution.selected else None, "message": "В утверждённом каноне нет правила; показан внешний ответ, не являющийся правилом Школы."}
    if resolution.outcome == WORLD_CONFLICT:
        return {"status": "WORLD_CONFLICT", "authority": "WORLD_EXTERNAL", "action": None, "message": "Внешние источники расходятся; автоматическая рекомендация не выдана.", "alternative_rule_ids": [r.rule_id for r in resolution.world_candidates]}
    return {"status": "UNRESOLVED_GAP", "authority": None, "action": None, "message": "Подходящего подтверждённого ответа пока нет."}


def _rank(rules: Iterable[KnowledgeRule]) -> tuple[KnowledgeRule, ...]:
    return tuple(sorted((r for r in rules if r.applicable), key=lambda r: (-r.priority, -r.specificity, r.rule_id)))


def _winner_or_conflict(rules: tuple[KnowledgeRule, ...]) -> tuple[KnowledgeRule | None, bool]:
    if not rules:
        return None, False
    top = rules[0]
    tied = [r for r in rules if (r.priority, r.specificity) == (top.priority, top.specificity)]
    actions = {r.action for r in tied}
    return (top if len(actions) == 1 else None), len(actions) > 1


def resolve_two_lane(canon_rules: Iterable[KnowledgeRule], world_rules: Iterable[KnowledgeRule]) -> Resolution:
    """Resolve canon first, then WORLD only after a recorded canon gap.

    A world rule can never repair a canon conflict and a low-confidence result
    is not treated as a fallback answer.
    """
    canon = _rank(r for r in canon_rules if r.authority_class == "school_canon")
    canon_winner, canon_conflict = _winner_or_conflict(canon)
    trace: dict[str, Any] = {"canon_stage": "searched", "canon_rule_ids": [r.rule_id for r in canon], "world_searched": False}
    if canon_conflict:
        trace["canon_stage"] = "CANON_CONFLICT"
        return Resolution(CANON_CONFLICT, None, canon, (), trace)
    if canon_winner:
        trace["canon_stage"] = "CANON_MATCH"
        return Resolution("CANON_MATCH", canon_winner, canon, (), trace)

    # Keep WORLD lazy.  In production this iterable may be a database cursor or
    # remote robot query; touching it before a recorded canon gap would violate
    # the authority-ordering contract even if its result were later discarded.
    world = _rank(r for r in world_rules if r.authority_class == "external")
    trace.update({"canon_stage": CANON_GAP, "world_searched": True, "world_rule_ids": [r.rule_id for r in world]})
    world_winner, world_conflict = _winner_or_conflict(world)
    if world_conflict:
        trace["world_stage"] = WORLD_CONFLICT
        return Resolution(WORLD_CONFLICT, None, canon, world, trace)
    if world_winner and world_winner.confidence.lower() in {"high", "verified", "reproducible"}:
        trace["world_stage"] = WORLD_FALLBACK
        return Resolution(WORLD_FALLBACK, world_winner, canon, world, trace)
    trace["world_stage"] = UNRESOLVED_GAP
    return Resolution(UNRESOLVED_GAP, None, canon, world, trace)
