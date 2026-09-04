"""Fail-closed two-lane resolver for SCHOOL CANON and WORLD knowledge."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Callable, Iterable, Mapping

CANON_GAP = "CANON_GAP"
CANON_CONFLICT = "CANON_CONFLICT"
WORLD_FALLBACK = "WORLD_FALLBACK"
WORLD_CONFLICT = "WORLD_CONFLICT"
UNRESOLVED_GAP = "UNRESOLVED_GAP"


@dataclass(frozen=True)
class ResolutionProfile:
    system_profile: str
    system_version: str
    learner_level: str
    auction_context_id: str
    effective_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    activation_scope: str = "default"

    def __post_init__(self) -> None:
        for value in (self.system_profile, self.system_version, self.learner_level,
                      self.auction_context_id, self.activation_scope):
            if not value.strip():
                raise ValueError("resolution profile fields must be non-empty")
        if self.effective_at.tzinfo is None:
            raise ValueError("effective_at must be timezone-aware")


@dataclass(frozen=True)
class KnowledgeRule:
    rule_id: str
    authority_class: str
    action: str
    system_profile: str
    system_version: str
    learner_level: str
    auction_context_id: str
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    priority: int = 0
    specificity: int = 0
    confidence: str = "unknown"
    applicable: bool = True
    provenance: dict[str, Any] = field(default_factory=dict)
    auction_pattern: dict[str, Any] = field(default_factory=dict)
    hand_constraints: dict[str, Any] = field(default_factory=dict)
    public_context_constraints: dict[str, Any] = field(default_factory=dict)

    def matches(
        self,
        profile: ResolutionProfile,
        *,
        acting_hand: Mapping[str, Any] | None = None,
        public_auction: Mapping[str, Any] | None = None,
        public_context: Mapping[str, Any] | None = None,
    ) -> bool:
        if not self.applicable:
            return False
        if (self.system_profile, self.system_version, self.learner_level, self.auction_context_id) != (
            profile.system_profile, profile.system_version, profile.learner_level, profile.auction_context_id
        ):
            return False
        if ((self.effective_from is not None and profile.effective_at < self.effective_from)
                or (self.effective_to is not None and profile.effective_at >= self.effective_to)):
            return False
        auction_predicate = {
            key: value for key, value in self.auction_pattern.items()
            if key != "context_id"
        }
        return (
            _predicate_matches(self.hand_constraints, acting_hand or {})
            and _predicate_matches(auction_predicate, public_auction or {})
            and _predicate_matches(
                self.public_context_constraints, public_context or {}
            )
        )


@dataclass(frozen=True)
class CanonGapReceipt:
    gap_id: str
    school_id: str
    request_fingerprint: str
    profile_fingerprint: str
    effective_at: datetime
    committed_at: datetime


@dataclass(frozen=True)
class Resolution:
    outcome: str
    selected: KnowledgeRule | None
    canon_candidates: tuple[KnowledgeRule, ...]
    world_candidates: tuple[KnowledgeRule, ...]
    trace: dict[str, Any]


def learner_response(resolution: Resolution) -> dict[str, Any]:
    if resolution.outcome == "CANON_MATCH":
        return {"status": "ANSWER", "authority": "SCHOOL_CANON", "action": resolution.selected.action if resolution.selected else None, "message": "Ответ дан по утверждённому правилу Школы."}
    if resolution.outcome == CANON_CONFLICT:
        return {"status": "PENDING_CANON_CLARIFICATION", "authority": "SCHOOL_CANON", "action": None, "message": "В утверждённых правилах Школы есть конфликт для этой ситуации. Единая рекомендация пока не выдана.", "conflicting_rule_ids": [r.rule_id for r in resolution.canon_candidates]}
    if resolution.outcome == WORLD_FALLBACK:
        return {"status": "ANSWER", "authority": "WORLD_FALLBACK", "action": resolution.selected.action if resolution.selected else None, "message": "В утверждённом каноне нет правила; показан внешний ответ, не являющийся правилом Школы."}
    if resolution.outcome == WORLD_CONFLICT:
        return {"status": "WORLD_CONFLICT", "authority": "WORLD_EXTERNAL", "action": None, "message": "Внешние источники расходятся; автоматическая рекомендация не выдана.", "alternative_rule_ids": [r.rule_id for r in resolution.world_candidates]}
    return {"status": "UNRESOLVED_GAP", "authority": None, "action": None, "message": "Подходящего подтверждённого ответа пока нет."}


def _predicate_matches(expected: Any, actual: Any) -> bool:
    """Evaluate the bounded JSON predicates stored in trusted Canon rows."""
    if expected in ({}, [], None):
        return True
    if isinstance(expected, Mapping):
        operators = set(expected) & {"eq", "in", "min", "max", "contains"}
        if operators:
            if "eq" in expected and actual != expected["eq"]:
                return False
            if "in" in expected and actual not in expected["in"]:
                return False
            if "min" in expected and actual < expected["min"]:
                return False
            if "max" in expected and actual > expected["max"]:
                return False
            if "contains" in expected:
                wanted = expected["contains"]
                if isinstance(wanted, list):
                    if not isinstance(actual, list) or not all(item in actual for item in wanted):
                        return False
                elif wanted not in actual:
                    return False
            return True
        return isinstance(actual, Mapping) and all(
            key in actual and _predicate_matches(value, actual[key])
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and expected == actual
    return expected == actual


def _rank(
    rules: Iterable[KnowledgeRule],
    profile: ResolutionProfile,
    *,
    acting_hand: Mapping[str, Any] | None = None,
    public_auction: Mapping[str, Any] | None = None,
    public_context: Mapping[str, Any] | None = None,
) -> tuple[KnowledgeRule, ...]:
    return tuple(sorted((r for r in rules if r.matches(
        profile,
        acting_hand=acting_hand,
        public_auction=public_auction,
        public_context=public_context,
    )), key=lambda r: (-r.priority, -r.specificity, r.rule_id)))


def _winner_or_conflict(rules: tuple[KnowledgeRule, ...]) -> tuple[KnowledgeRule | None, bool]:
    if not rules:
        return None, False
    top = rules[0]
    tied = [r for r in rules if (r.priority, r.specificity) == (top.priority, top.specificity)]
    actions = {r.action for r in tied}
    return (top if len(actions) == 1 else None), len(actions) > 1


def _profile_fingerprint(profile: ResolutionProfile) -> str:
    raw = json.dumps(
        [profile.system_profile, profile.system_version, profile.learner_level,
         profile.auction_context_id, profile.activation_scope],
        ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _request_fingerprint(*, acting_seat: str, acting_hand: dict[str, Any],
                         public_auction: dict[str, Any], public_context: dict[str, Any]) -> str:
    raw = json.dumps(
        {"acting_seat": acting_seat, "acting_hand": acting_hand,
         "public_auction": public_auction, "public_context": public_context},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _gap_fingerprint(request_fingerprint: str, profile: ResolutionProfile) -> str:
    return hashlib.sha256(f"{request_fingerprint}:{_profile_fingerprint(profile)}".encode()).hexdigest()


class PostgresCanonRuleStore:
    """Sealed adapter: Canon candidates can only originate in the active Canon catalog."""
    def __init_subclass__(cls, **kwargs):
        raise TypeError("PostgresCanonRuleStore is sealed")

    def __init__(self, connection_factory: Callable[[], Any]):
        self._connection_factory = connection_factory

    def fetch(self, school_id: str, profile: ResolutionProfile) -> tuple[KnowledgeRule, ...]:
        """Compatibility surface; never uses a caller-captured timestamp."""
        return self.fetch_current(school_id, profile)[1]

    def fetch_current(
        self,
        school_id: str,
        profile: ResolutionProfile,
    ) -> tuple[ResolutionProfile, tuple[KnowledgeRule, ...]]:
        """Bind authoritative DB time and fetch the matching trusted catalog."""
        connection = self._connection_factory()
        with connection:
            with connection.cursor() as cur:
                cur.execute("SELECT clock_timestamp()")
                row = cur.fetchone()
                if row is None or not isinstance(row[0], datetime) or row[0].tzinfo is None:
                    raise RuntimeError("authoritative Canon resolution time is unavailable")
                bound_profile = replace(profile, effective_at=row[0])
                cur.execute(
                    """SELECT c.rule_id,c.action::text,kv.bidding_system_key,c.method_version,
                              kv.level_scope->>'level',c.auction_pattern->>'context_id',
                              c.valid_from,c.valid_to,c.priority,c.specificity,
                              c.auction_pattern,c.hand_constraints,c.public_context_constraints
                         FROM bidding.get_school_runtime_rule_catalog_at(%s,%s,%s) c
                         JOIN public.knowledge_version kv USING(knowledge_version_id)
                        WHERE kv.bidding_system_key=%s AND c.method_version=%s
                          AND kv.level_scope->>'level'=%s AND c.auction_pattern->>'context_id'=%s
                          AND c.valid_from<=%s AND (c.valid_to IS NULL OR c.valid_to>%s)""",
                    (school_id, bound_profile.activation_scope, bound_profile.effective_at,
                     bound_profile.system_profile, bound_profile.system_version,
                     bound_profile.learner_level, bound_profile.auction_context_id,
                     bound_profile.effective_at, bound_profile.effective_at),
                )
                rows = cur.fetchall()
        rules = tuple(KnowledgeRule(
            str(r[0]), "school_canon", r[1], r[2], r[3], r[4], r[5],
            r[6], r[7], r[8], r[9], "verified",
            auction_pattern=dict(r[10] or {}),
            hand_constraints=dict(r[11] or {}),
            public_context_constraints=dict(r[12] or {}),
        ) for r in rows)
        return bound_profile, rules


class PostgresCanonGapStore:
    """Trusted boundary: commit on one connection, verify on a fresh connection."""
    def __init_subclass__(cls, **kwargs):
        raise TypeError("PostgresCanonGapStore is sealed")

    def __init__(self, connection_factory: Callable[[], Any]):
        self._connection_factory = connection_factory

    def persist_and_verify(self, school_id: str, request_fingerprint: str,
                           profile: ResolutionProfile) -> CanonGapReceipt:
        fingerprint = _profile_fingerprint(profile)
        writer = self._connection_factory()
        with writer:
            with writer.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                            (f"{school_id}:{request_fingerprint}",))
                cur.execute(
                    """SELECT knowledge_gap_id,profile_fingerprint,effective_at
                       FROM bidding.world_canon_gap_binding
                       WHERE school_id=%s AND request_fingerprint=%s""",
                    (school_id, request_fingerprint),
                )
                existing = cur.fetchone()
                if existing is not None:
                    gap_id = str(existing[0])
                    if existing[1] != fingerprint:
                        raise RuntimeError("request fingerprint is already bound to a different resolution profile")
                    effective_at = existing[2]
                else:
                    cur.execute(
                        """INSERT INTO public.knowledge_gap(school_id,question,context_scope,status)
                           VALUES (%s,'CANON_GAP',%s::jsonb,'open') RETURNING knowledge_gap_id""",
                        (school_id, json.dumps({"request_fingerprint": request_fingerprint})),
                    )
                    gap_id = str(cur.fetchone()[0])
                    cur.execute(
                        """INSERT INTO bidding.world_canon_gap_binding(
                             knowledge_gap_id,school_id,request_fingerprint,system_profile_key,
                             system_version,learner_level,auction_context_id,effective_at,profile_fingerprint)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (gap_id, school_id, request_fingerprint, profile.system_profile,
                         profile.system_version, profile.learner_level, profile.auction_context_id,
                         profile.effective_at, fingerprint),
                    )
                    effective_at = profile.effective_at
            writer.commit()

        reader = self._connection_factory()
        if reader is writer:
            raise RuntimeError("post-commit gap verification requires a fresh connection")
        with reader:
            with reader.cursor() as cur:
                cur.execute(
                    """SELECT knowledge_gap_id,school_id,request_fingerprint,profile_fingerprint,effective_at,created_at
                       FROM bidding.world_canon_gap_binding
                       WHERE knowledge_gap_id=%s AND school_id=%s AND request_fingerprint=%s
                         AND profile_fingerprint=%s""",
                    (gap_id, school_id, request_fingerprint, fingerprint),
                )
                row = cur.fetchone()
        if row is None:
            raise RuntimeError("committed CANON_GAP binding was not visible on an independent connection")
        if row[4] != effective_at or not isinstance(row[4], datetime) or row[4].tzinfo is None:
            raise RuntimeError("committed CANON_GAP effective time is unavailable")
        return CanonGapReceipt(str(row[0]), str(row[1]), row[2], row[3], row[4], row[5])


def resolve_two_lane(*, school_id: str, acting_seat: str, acting_hand: dict[str, Any],
                     public_auction: dict[str, Any], public_context: dict[str, Any],
                     profile: ResolutionProfile,
                     canon_store: PostgresCanonRuleStore,
                     gap_store: PostgresCanonGapStore,
                     world_supplier: Callable[[CanonGapReceipt, ResolutionProfile], Iterable[KnowledgeRule]]) -> Resolution:
    """Resolve Canon first; commit its gap before invoking a lazy WORLD supplier."""
    visible_request_fingerprint = _request_fingerprint(
        acting_seat=acting_seat, acting_hand=acting_hand,
        public_auction=public_auction, public_context=public_context,
    )
    if type(canon_store) is not PostgresCanonRuleStore:
        raise TypeError("canon_store must be the sealed active-catalog PostgresCanonRuleStore")
    profile, fetched_canon = canon_store.fetch_current(school_id, profile)
    request_fingerprint = _gap_fingerprint(visible_request_fingerprint, profile)
    canon = _rank(
        fetched_canon, profile, acting_hand=acting_hand,
        public_auction=public_auction, public_context=public_context,
    )
    canon_winner, canon_conflict = _winner_or_conflict(canon)
    trace: dict[str, Any] = {"canon_stage": "searched", "canon_rule_ids": [r.rule_id for r in canon],
        "world_searched": False, "school_id": school_id, "request_fingerprint": request_fingerprint,
        "profile": {"system_profile": profile.system_profile, "system_version": profile.system_version,
                    "learner_level": profile.learner_level, "auction_context_id": profile.auction_context_id,
                    "effective_at": profile.effective_at.isoformat(),
                    "activation_scope": profile.activation_scope}}
    if canon_conflict:
        trace["canon_stage"] = CANON_CONFLICT
        return Resolution(CANON_CONFLICT, None, canon, (), trace)
    if canon_winner:
        trace["canon_stage"] = "CANON_MATCH"
        return Resolution("CANON_MATCH", canon_winner, canon, (), trace)

    if type(gap_store) is not PostgresCanonGapStore:
        raise TypeError("gap_store must be the sealed database-backed PostgresCanonGapStore")
    receipt = gap_store.persist_and_verify(school_id, request_fingerprint, profile)
    if (receipt.school_id != school_id
            or receipt.request_fingerprint != request_fingerprint
            or receipt.profile_fingerprint != _profile_fingerprint(profile)
            or receipt.effective_at.tzinfo is None
            or receipt.committed_at.tzinfo is None):
        raise RuntimeError("CANON_GAP needs an independently read, request-bound post-commit receipt before WORLD")
    trace.update({"canon_stage": CANON_GAP, "knowledge_gap_id": receipt.gap_id})
    world_profile = replace(profile, effective_at=receipt.effective_at)
    recheck_profile, rechecked_rules = canon_store.fetch_current(school_id, profile)
    rechecked_canon = _rank(
        rechecked_rules, recheck_profile, acting_hand=acting_hand,
        public_auction=public_auction, public_context=public_context,
    )
    recheck_winner, recheck_conflict = _winner_or_conflict(rechecked_canon)
    if recheck_conflict:
        trace.update({"canon_stage": CANON_CONFLICT, "canon_rechecked": True})
        return Resolution(CANON_CONFLICT, None, rechecked_canon, (), trace)
    if recheck_winner:
        trace.update({"canon_stage": "CANON_MATCH", "canon_rechecked": True})
        return Resolution("CANON_MATCH", recheck_winner, rechecked_canon, (), trace)
    trace["canon_rechecked"] = True
    world = _rank(
        (r for r in world_supplier(receipt, world_profile) if r.authority_class == "external"),
        world_profile,
        acting_hand=acting_hand,
        public_auction=public_auction,
        public_context=public_context,
    )
    trace.update({"world_searched": True, "world_rule_ids": [r.rule_id for r in world]})
    world_winner, world_conflict = _winner_or_conflict(world)
    if world_conflict:
        trace["world_stage"] = WORLD_CONFLICT
        return Resolution(WORLD_CONFLICT, None, canon, world, trace)
    if world_winner and world_winner.confidence.lower() in {"high", "verified", "reproducible"}:
        trace.update({"world_stage": WORLD_FALLBACK, "selected_world_rule_id": world_winner.rule_id})
        return Resolution(WORLD_FALLBACK, world_winner, canon, world, trace)
    trace["world_stage"] = UNRESOLVED_GAP
    return Resolution(UNRESOLVED_GAP, None, canon, world, trace)
