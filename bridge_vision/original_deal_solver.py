"""Bounded exact ownership constraints; model uniqueness is not visual truth.

No scores, hidden-card guesses, suit distributions or absence-in-frame rules.
The matching witness is private: callers receive *all* feasible owner domains.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import json
from typing import Any

from bridge_contracts.video_deal import FULL_DECK, SEATS

RECOVERY_VERSION = "bridge-original-deal-recovery-v1"


class RecoveryInputError(ValueError):
    pass


def feasible_owner_domains(
    deck: Sequence[str], capacities: Mapping[str, int],
    domains: Mapping[str, Sequence[str]],
    *, feasibility_only: bool = False,
) -> dict[str, Any]:
    """Exact finite-domain result, at most 52 cards / four seats / 209 matchings.

    The smaller deck/capacity interface exists for independent exhaustive tests.
    The production wrapper below always supplies the standard 52-card deck.
    """
    if (not isinstance(deck, (list, tuple)) or not 1 <= len(deck) <= 52
            or any(not isinstance(c, str) or not c for c in deck)
            or len(set(deck)) != len(deck) or not isinstance(capacities, Mapping)
            or not 1 <= len(capacities) <= 4
            or any(not isinstance(s, str) or not s or type(n) is not int or not 0 <= n <= 52
                   for s, n in capacities.items())
            or sum(capacities.values()) != len(deck)
            or not isinstance(domains, Mapping) or set(domains) != set(deck)):
        raise RecoveryInputError("INVALID_OWNERSHIP_MODEL")
    seats = tuple(sorted(capacities))
    normalized = {}
    for card in deck:
        owners = domains[card]
        if (not isinstance(owners, (list, tuple))
                or any(not isinstance(s, str) or s not in capacities for s in owners)
                or len(set(owners)) != len(owners)):
            raise RecoveryInputError("INVALID_OWNER_DOMAIN")
        normalized[card] = tuple(sorted(owners))
    cards = sorted(deck, key=lambda c: (len(normalized[c]), c))
    calls = 0

    def feasible(force: tuple[str, str] | None = None) -> bool:
        nonlocal calls
        calls += 1
        matched: dict[str, list[str]] = {s: [] for s in seats}

        def augment(card: str, visited: set[str]) -> bool:
            owners = (force[1],) if force and card == force[0] else normalized[card]
            for seat in owners:
                if seat in visited:
                    continue
                visited.add(seat)
                if len(matched[seat]) < capacities[seat]:
                    matched[seat].append(card)
                    return True
                for i, incumbent in enumerate(matched[seat]):
                    if augment(incumbent, visited):
                        matched[seat][i] = card
                        return True
            return False

        return all(augment(card, set()) for card in cards)

    if not feasible():
        return {"status": "CONFLICT", "domains": {}, "matching_calls": calls}
    if feasibility_only:
        return {"status": "FEASIBLE", "matching_calls": calls}
    possible = {card: list(normalized[card]) if len(normalized[card]) == 1 else
                [seat for seat in normalized[card] if feasible((card, seat))]
                for card in sorted(deck)}
    return {"status": "UNIQUE" if all(len(v) == 1 for v in possible.values()) else "MULTIPLE",
            "domains": possible, "matching_calls": calls}


def recover_original_deal(facts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Recover only forced owners, conditional on independently reviewed facts.

    A fact is {card, seat, evidence_id}. Repeated facts do not add votes. The
    temporal layer alone constructs these from independently bound receipts.
    """
    if not isinstance(facts, (list, tuple)) or len(facts) > 52 * 2048:
        raise RecoveryInputError("FACTS_INVALID_OR_LIMIT_EXCEEDED")
    support: dict[str, dict[str, set[str]]] = {}
    ids: dict[str, tuple[str, str]] = {}
    conflicts = []
    sources: dict[str, str] = {}
    derivations: dict[str, dict[str, Any]] = {}
    payloads: dict[str, str] = {}
    for item in facts:
        if not isinstance(item, Mapping):
            raise RecoveryInputError("FACT_INVALID")
        card, seat, ref = item.get("card"), item.get("seat"), item.get("evidence_id")
        if (not isinstance(card, str) or card not in FULL_DECK
                or not isinstance(seat, str) or seat not in SEATS
                or not isinstance(ref, str) or not 1 <= len(ref) <= 512):
            raise RecoveryInputError("FACT_INVALID")
        if ref in ids and ids[ref] != (card, seat):
            conflicts.append({"reason": "EVIDENCE_ID_REUSED", "evidence_id": ref})
        ids[ref] = card, seat
        source = item.get("source", "VISUAL")
        if source not in ("VISUAL", "TEACHER_SPEECH", "INFERRED_ORDER_GAP", "INFERRED_ORDER_BOUNDARY", "INFERRED_ORDER_CONSTRAINT", "INFERRED_COMPONENT_CONSTRAINT"):
            raise RecoveryInputError("FACT_SOURCE_INVALID")
        derivation = item.get("derivation") if source.startswith("INFERRED_") else None
        if source.startswith("INFERRED_") and not isinstance(derivation, Mapping):
            raise RecoveryInputError("DERIVATION_REQUIRED")
        try:
            payload = json.dumps([card, seat, source, derivation], sort_keys=True, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise RecoveryInputError("FACT_NOT_JSON") from exc
        if ref in payloads and payloads[ref] != payload:
            conflicts.append({"reason": "EVIDENCE_PAYLOAD_CHANGED", "evidence_id": ref})
        payloads[ref] = payload
        if derivation is not None:
            derivations[ref] = deepcopy(dict(derivation))
        sources[ref] = source
        support.setdefault(card, {}).setdefault(seat, set()).add(ref)
    for card, owners in sorted(support.items()):
        if len(owners) > 1:
            conflicts.append({"reason": "SAME_CARD_DIFFERENT_SEATS", "card": card,
                              "seats": sorted(owners)})
    constraints = {"deck": sorted(FULL_DECK), "each_card_exactly_once": True,
                   "original_hand_sizes": dict.fromkeys(SEATS, 13),
                   "premise_evidence_ids": sorted(ids)}
    result: dict[str, Any] = {
        "version": RECOVERY_VERSION, "status": "CONFLICT", "solution_status": "CONFLICT",
        "observed": {c: {s: sorted(r for r in refs if not sources[r].startswith("INFERRED_"))
                         for s, refs in sorted(owners.items())
                         if any(not sources[r].startswith("INFERRED_") for r in refs)}
                     for c, owners in sorted(support.items())
                     if any(not sources[r].startswith("INFERRED_") for refs in owners.values() for r in refs)},
        "evidence_sources": dict(sorted(sources.items())),
        "inferred": {}, "domains": {}, "deal": None, "conflicts": conflicts,
        "constraints": constraints, "verification": "CONDITIONAL_ON_REVIEWED_EVIDENCE",
        "canonical_promotion_allowed": False, "real_video_accuracy_evaluated": False,
    }
    if conflicts:
        return result
    domains = {c: sorted(support[c]) if c in support else list(SEATS) for c in FULL_DECK}
    solved = feasible_owner_domains(sorted(FULL_DECK), dict.fromkeys(SEATS, 13), domains)
    result["matching_calls"] = solved["matching_calls"]
    if solved["status"] == "CONFLICT":
        result["conflicts"].append({"reason": "NO_52_CARD_13_PER_SEAT_SOLUTION"})
        return result
    result["domains"] = solved["domains"]
    result["solution_status"] = solved["status"]
    result["status"] = "VERIFIED_FULL" if solved["status"] == "UNIQUE" else "VERIFIED_PARTIAL"
    inferred = {c: {"seat": owners[0], "method": "ONLY_FEASIBLE_OWNER",
                    "constraints_ref": "constraints", "conditional_on_premises": True,
                    "premise_evidence_ids": sorted(ids)}
                for c, owners in solved["domains"].items() if len(owners) == 1 and c not in support}
    for card, owners in support.items():
        if card not in result["observed"]:
            seat = next(iter(owners))
            refs = sorted(owners[seat])
            inferred[card] = {"seat": seat, "method": sources[refs[0]],
                              "constraints_ref": "constraints", "conditional_on_premises": True,
                              "premise_evidence_ids": refs,
                              "derivations": [derivations[r] for r in refs]}
    result["inferred"] = inferred
    hands = {}
    for seat in SEATS:
        observed = sorted(c for c in support if seat in support[c]
                          and any(sources[ref] == "VISUAL" for ref in support[c][seat]))
        declared = sorted(c for c in support if seat in support[c] and c not in observed
                          and any(sources[ref] == "TEACHER_SPEECH" for ref in support[c][seat]))
        derived = sorted(c for c, item in inferred.items() if item["seat"] == seat)
        hands[seat] = {"observed_cards": observed, "declared_cards": declared, "inferred_cards": derived,
                       "cards": sorted(observed + declared + derived),
                       "unknown_count": 13 - len(observed + declared + derived)}
    # Deliberately not the legacy canonical video_deal contract: it would mark
    # supplied inferred cards as observed. Consumers must preserve this envelope.
    result["deal"] = {"contract_version": RECOVERY_VERSION, "hands": hands}
    return result
