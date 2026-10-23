"""Serialize a pinned reviewed recovery envelope; never solve or choose owners.

The trusted caller supplies independent expected hashes. Hash equality proves
integrity, not correctness of labels. Ownership remains conditional on the
producer's independently reviewed premises and never promotes school canon.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from bridge_contracts.video_deal import FULL_DECK, SEATS
from .pbn import _render_hands

SIDECAR_SCHEMA = "bridge-reviewed-ownership-sidecar-v1"
RECOVERY_VERSION = "bridge-original-deal-recovery-v1"
TEMPORAL_VERSION = "bridge-temporal-recovery-v1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_SEAL = object()


def _need(value, code):
    if not value:
        raise ValueError("PBN_RECOVERY_" + code)


def _canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def _unique_pairs(items):
    result = {}
    for key, value in items:
        _need(key not in result, "DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _read(path, expected):
    _need(type(expected) is str and bool(_SHA.fullmatch(expected)), "EXPECTED_HASH_REQUIRED")
    path = Path(path)
    _need(0 < path.stat().st_size <= 16 * 1024 * 1024, "FILE_SIZE")
    raw = path.read_bytes()
    _need(hashlib.sha256(raw).hexdigest() == expected, "FILE_HASH")
    def constant(_):
        raise ValueError("PBN_RECOVERY_NONFINITE_JSON")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs, parse_constant=constant)


@dataclass(frozen=True, init=False)
class QualifiedRecovery:
    """Frozen admitted bytes, not an untrusted JSON boolean or human attestation."""
    _envelope: bytes
    _binding: bytes
    def __init__(self, envelope, binding, *, _seal=None):
        _need(_seal is _SEAL, "USE_PINNED_LOADER")
        object.__setattr__(self, "_envelope", _canonical(envelope))
        object.__setattr__(self, "_binding", _canonical(binding))

    def payload(self):
        return json.loads(self._envelope)

    def matches(self, record):
        binding = json.loads(self._binding)
        return all(record.get(key) == value for key, value in binding.items())


def _ownership(recovery):
    _need(isinstance(recovery, Mapping) and recovery.get("version") == RECOVERY_VERSION, "VERSION")
    _need(recovery.get("status") in ("VERIFIED_FULL", "VERIFIED_PARTIAL") and
          recovery.get("solution_status") == ("UNIQUE" if recovery["status"] == "VERIFIED_FULL" else "MULTIPLE"), "STATUS")
    _need(recovery.get("verification") == "CONDITIONAL_ON_REVIEWED_EVIDENCE" and
          recovery.get("canonical_promotion_allowed") is False and not recovery.get("conflicts"), "QUALIFICATION")
    domains = recovery.get("domains")
    _need(isinstance(domains, Mapping) and set(domains) == set(FULL_DECK), "DOMAINS")
    for owners in domains.values():
        _need(type(owners) is list and 1 <= len(owners) <= 4 and all(type(s) is str and s in SEATS for s in owners)
              and len(set(owners)) == len(owners), "OWNER_DOMAIN")
    constraints = recovery.get("constraints", {})
    _need(isinstance(constraints, Mapping), "CONSTRAINTS")
    deck = constraints.get("deck")
    _need(type(deck) is list and len(deck) == 52 and all(type(c) is str and c in FULL_DECK for c in deck)
          and len(set(deck)) == 52 and constraints.get("each_card_exactly_once") is True
          and constraints.get("original_hand_sizes") == dict.fromkeys(SEATS, 13), "CONSTRAINTS")
    sources = recovery.get("evidence_sources", {})
    refs = constraints.get("premise_evidence_ids", [])
    _need(isinstance(sources, Mapping) and type(refs) is list and all(type(ref) is str for ref in refs)
          and len(set(refs)) == len(refs) and set(refs) == set(sources), "PREMISE_REFS")
    _need(isinstance(recovery.get("observed"), Mapping) and isinstance(recovery.get("inferred"), Mapping), "PROVENANCE_MAPS")
    deal = recovery.get("deal", {})
    _need(isinstance(deal, Mapping) and deal.get("contract_version") == RECOVERY_VERSION, "DEAL_VERSION")
    hands = deal.get("hands")
    _need(isinstance(hands, Mapping) and set(hands) == set(SEATS), "HANDS")
    counts = dict(observed_card_count=0, declared_card_count=0, inferred_card_count=0)
    result, seen = {}, set()
    for seat in SEATS:
        hand = hands[seat]
        _need(isinstance(hand, Mapping), "HAND")
        groups = [hand.get(k) for k in ("observed_cards", "declared_cards", "inferred_cards")]
        _need(all(type(v) is list and all(type(c) is str and c in FULL_DECK for c in v) for v in groups), "CARD_GROUPS")
        cards = sum(groups, [])
        _need(len(cards) <= 13 and len(set(cards)) == len(cards) and not (set(cards) & seen)
              and type(hand.get("cards")) is list and sorted(hand["cards"]) == sorted(cards)
              and type(hand.get("unknown_count")) is int and hand["unknown_count"] == 13 - len(cards), "CARD_PARTITION")
        _need(all(domains[card] == [seat] for card in cards), "AMBIGUOUS_OWNERSHIP")
        for card in groups[0] + groups[1]:
            support = recovery.get("observed", {}).get(card, {}).get(seat, [])
            required_source = "VISUAL" if card in groups[0] else "TEACHER_SPEECH"
            _need(bool(support) and all(ref in sources for ref in support)
                  and any(sources[ref] == required_source for ref in support), "OBSERVED_PROVENANCE")
        for card in groups[2]:
            proof = recovery.get("inferred", {}).get(card, {})
            _need(isinstance(proof, Mapping), "INFERRED_PROVENANCE")
            premises = proof.get("premise_evidence_ids")
            _need(proof.get("seat") == seat and proof.get("conditional_on_premises") is True
                  and type(proof.get("method")) is str and bool(proof["method"])
                  and type(premises) is list and bool(premises) and set(premises) <= set(refs), "INFERRED_PROVENANCE")
        seen.update(cards); result[seat] = cards
        for key, values in zip(counts, groups): counts[key] += len(values)
    _need(seen == {c for c, owners in domains.items() if len(owners) == 1}, "SINGLETON_COVERAGE")
    _need((len(seen) == 52) == (recovery["status"] == "VERIFIED_FULL"), "FALSE_COMPLETENESS")
    return result, counts


def _premise_bindings(scope, recovery, receipts, *, frames=None):
    """Check references against reviewed assertions, without rerunning a solver."""
    facts = scope.get("facts")
    _need(type(facts) is list, "FACTS_REQUIRED")
    by_ref = {}
    for fact in facts:
        _need(isinstance(fact, Mapping) and type(fact.get("evidence_id")) is str,
              "FACT_REFERENCE")
        _need(type(fact.get("card")) is str and fact["card"] in FULL_DECK
              and type(fact.get("seat")) is str and fact["seat"] in SEATS, "FACT_OWNERSHIP_SHAPE")
        ref = fact["evidence_id"]
        _need(ref not in by_ref or by_ref[ref] == fact, "FACT_REFERENCE_COLLISION")
        by_ref[ref] = fact
        source = fact.get("source", "VISUAL")
        if source == "VISUAL":
            matches = []
            for event, receipt in receipts.items():
                for card in receipt.get("cards", []):
                    if (isinstance(card, Mapping) and
                            ref == event + ":" + str(card.get("observation_index"))):
                        matches.append((receipt, card))
            _need(len(matches) == 1, "VISUAL_REVIEW_REFERENCE")
            receipt, card = matches[0]
            seat_map = receipt.get("screen_to_seat", {})
            owner = seat_map.get(card.get("screen_seat")) if isinstance(seat_map, Mapping) else None
            if frames is not None and card.get("kind") == "PLAYED":
                from .recovery_pbn_evidence import played_owner
                owner = played_owner(fact, receipt, card, frames[ref.rsplit(":", 1)[0]])
            _need(isinstance(seat_map, Mapping) and
                  fact.get("card") == card.get("card") and
                  fact.get("seat") == owner and
                  fact.get("kind") == card.get("kind") and
                  fact.get("review_ref") == card.get("evidence_ref") and
                  all(fact.get(k) == receipt.get(k) for k in ("frame_sha256", "timestamp_ms")) and
                  all(fact.get(k) == card.get(k) for k in ("played_at_ms", "seat_time_ref")),
                  "VISUAL_REVIEW_OWNERSHIP")
        elif source == "TEACHER_SPEECH":
            declaration = fact.get("declaration", {})
            _need(isinstance(declaration, Mapping), "DECLARATION_REQUIRED")
            reviewed = declaration.get("review_evidence")
            original = {k: v for k, v in declaration.items() if k != "review_evidence"}
            _need(isinstance(reviewed, Mapping) and reviewed.get("evidence_ref") == ref and
                  reviewed.get("declaration_sha256") == hashlib.sha256(_canonical(original)).hexdigest() and
                  any(reviewed in receipt.get("speech_reviews", []) for receipt in receipts.values()) and
                  fact.get("card") == declaration.get("card") and fact.get("seat") == declaration.get("seat") and
                  fact.get("kind") == "TEACHER_SPEECH" and declaration.get("source") == source,
                  "DECLARATION_REVIEW_OWNERSHIP")
        else:
            from .recovery_pbn_evidence import DERIVED_SOURCES
            _need(frames is not None and source in DERIVED_SOURCES,
                  "UNSUPPORTED_DERIVED_PREMISE")
    sources = recovery["evidence_sources"]
    _need(set(by_ref) == set(sources) and all(
        sources[ref] == fact.get("source", "VISUAL") for ref, fact in by_ref.items()), "FACT_SOURCE_COVERAGE")
    expected_observed = {}
    for ref, fact in by_ref.items():
        if fact.get("source", "VISUAL").startswith("INFERRED_"):
            continue
        expected_observed.setdefault(fact["card"], {}).setdefault(fact["seat"], set()).add(ref)
    observed = recovery["observed"]
    _need(set(observed) == set(expected_observed), "OBSERVED_REVIEW_COVERAGE")
    for card, seats in expected_observed.items():
        _need(isinstance(observed[card], Mapping) and set(observed[card]) == set(seats), "OBSERVED_REVIEW_OWNER")
        for seat, refs in seats.items():
            values = observed[card][seat]
            _need(type(values) is list and len(values) == len(refs) and set(values) == refs,
                  "OBSERVED_REVIEW_SUPPORT")
    if frames is not None:
        from .recovery_pbn_evidence import derived_bindings, played_events
        derived_bindings(recovery, scope, receipts, frames, by_ref)
        played_events(scope, by_ref)


def load_reviewed_recovery(sidecar_path, *, expected_sha256, expected_temporal_sha256, review_manifest_path,
                           expected_review_manifest_sha256, expected_producer_pins,
                           target_binding) -> QualifiedRecovery:
    """Admit a separately pinned sidecar and independently frozen review input.

    target_binding keys: input_row_sha256, source_video_sha256, frame_sha256,
    timestamp_ms. expected_temporal_sha256 binds the complete canonical result
    captured directly from the pinned producer by the trusted caller, or from
    its independently qualified receipt. It MUST NOT be copied from sidecar
    claims. Its canonical encoding is ensure_ascii=True, sort_keys=True,
    separators=(",", ":"), allow_nan=False, encoded as ASCII. The consumer does
    not independently solve ownership. No video, pixels or ASR are read.
    """
    sidecar = _read(sidecar_path, expected_sha256)
    manifest = _read(review_manifest_path, expected_review_manifest_sha256)
    _need(isinstance(sidecar, Mapping) and isinstance(manifest, Mapping), "OBJECTS_REQUIRED")
    _need(isinstance(target_binding, Mapping) and set(target_binding) == {
        "input_row_sha256", "source_video_sha256", "frame_sha256", "timestamp_ms"}, "TARGET_BINDING")
    _need(all(type(target_binding[k]) is str and _SHA.fullmatch(target_binding[k]) for k in (
        "input_row_sha256", "source_video_sha256", "frame_sha256"))
        and type(target_binding["timestamp_ms"]) is int and target_binding["timestamp_ms"] >= 0, "TARGET_TYPES")
    _need(sidecar.get("schema") == SIDECAR_SCHEMA and sidecar.get("row_sha256") == target_binding["input_row_sha256"], "ROW_BINDING")
    _need(isinstance(expected_producer_pins, Mapping) and bool(expected_producer_pins)
          and {"bridge_vision/temporal_deal_recovery.py", "bridge_vision/original_deal_solver.py"} <= set(expected_producer_pins)
          and all(type(k) is str and type(v) is str and _SHA.fullmatch(v) for k, v in expected_producer_pins.items())
          and sidecar.get("producer_pins") == dict(expected_producer_pins), "PRODUCER_PINS")
    _need(manifest.get("version") == TEMPORAL_VERSION and manifest.get("authority") == "INDEPENDENT_REVIEW"
          and type(manifest.get("reviewer_ref")) is str and bool(manifest["reviewer_ref"].strip())
          and manifest.get("source_video_sha256") == target_binding["source_video_sha256"], "REVIEW_MANIFEST")
    temporal = sidecar.get("temporal_recovery", {})
    _need(type(expected_temporal_sha256) is str and _SHA.fullmatch(expected_temporal_sha256)
          and hashlib.sha256(_canonical(temporal)).hexdigest() == expected_temporal_sha256,
          "PINNED_PRODUCER_RESULT")
    _need(isinstance(temporal, Mapping) and temporal.get("version") == TEMPORAL_VERSION and temporal.get("status") == "SCOPED_OBSERVATIONS"
          and temporal.get("geometry_enabled") is False and temporal.get("canonical_promotion_allowed") is False
          and type(temporal.get("scopes")) is list and len(temporal["scopes"]) == 1, "SCOPED_OUTPUT")
    scope = temporal["scopes"][0]
    _need(isinstance(scope, Mapping) and type(scope.get("identity")) is list and len(scope["identity"]) >= 4, "SCOPE_IDENTITY")
    recovery = scope.get("recovery")
    from .recovery_pbn_evidence import extended_profile, scope_frames, ref
    extended = extended_profile(expected_producer_pins)
    unresolved = extended and isinstance(recovery, Mapping) and recovery.get("status") == "UNRESOLVED_LIMIT"
    if unresolved:
        _need(recovery.get("version") == RECOVERY_VERSION and recovery.get("solution_status") == "UNKNOWN"
              and recovery.get("verification") == "CONDITIONAL_ON_REVIEWED_EVIDENCE"
              and recovery.get("canonical_promotion_allowed") is False and not recovery.get("conflicts"), "UNRESOLVED_STATUS")
    else:
        _ownership(recovery)
    _need(scope.get("status") == recovery["status"] and not scope.get("conflicts"), "SCOPE_STATUS")
    evidence = scope.get("evidence", {})
    semantic_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True, allow_nan=False).encode()).hexdigest()
    _need(isinstance(evidence, Mapping) and evidence.get("manifest_sha256") == semantic_hash and evidence.get("reviewer_ref") == manifest["reviewer_ref"]
          and evidence.get("source_video_sha256") == target_binding["source_video_sha256"]
          and evidence.get("source_verification") == "BYTES_HASHED"
          and evidence.get("semantic_verification") == "INDEPENDENT_REVIEW_ASSERTIONS", "REVIEW_PROVENANCE")
    receipts = scope.get("review_receipts", {})
    _need(isinstance(receipts, Mapping) and bool(receipts) and set(receipts) == set(scope.get("event_ids", [])), "EVENT_RECEIPTS")
    matches = []
    for event, proof in receipts.items():
        _need(isinstance(proof, Mapping) and proof in manifest.get("events", []), "RECEIPT_NOT_IN_MANIFEST")
        _need(scope["identity"][:4] == [proof.get(k) for k in ("source_video_sha256", "episode_id", "board_epoch", "board_number")], "SCOPE_IDENTITY")
        if all(proof.get(k) == v for k, v in target_binding.items()): matches.append(event)
    _need(len(matches) == 1, "FRAME_ROW_SCOPE_BINDING")
    frames = scope_frames(temporal, scope, receipts) if extended else None
    if extended:
        _need(ref(receipts[matches[0]].get("input_row_binding_ref")), "EXACT_ROW_BINDING_REF")
    _premise_bindings(scope, recovery, receipts, frames=frames)
    result = {"sidecar_sha256": expected_sha256, "producer_result_sha256": expected_temporal_sha256,
              "review_manifest_raw_sha256": expected_review_manifest_sha256,
              "review_provenance": evidence, "recovery": recovery, "producer_pins": dict(expected_producer_pins),
              "scope_identity": scope["identity"], "review_observations": scope.get("review_observations", [])}
    if extended:
        result.update(temporal_recovery=temporal, native_ownership_admitted=not unresolved,
                      evidence_authority="INDEPENDENT_REVIEW_ASSERTIONS",
                      evidence_usage="FROZEN_SYNTHETIC_VALIDATION" if scope["identity"][7] == "SYNTHETIC_REGRESSION" else "GROUNDED_REPLAY",
                      human_identity_authenticated=False, runtime_authority_enrolled=False)
    return QualifiedRecovery(result, dict(target_binding), _seal=_SEAL)


def render_qualified_recovery(admitted, record, provenance):
    _need(type(admitted) is QualifiedRecovery and admitted.matches(record), "QUALIFIED_OBJECT_BINDING")
    payload = admitted.payload(); recovery = payload["recovery"]
    unresolved = payload.get("native_ownership_admitted") is False
    hands, counts = ({seat: [] for seat in SEATS}, dict(observed_card_count=0, declared_card_count=0, inferred_card_count=0)) if unresolved else _ownership(recovery)
    metadata = {**dict(provenance), "qualified_recovery": payload,
                "ownership_truth_scope": "CONDITIONAL_ON_INDEPENDENTLY_REVIEWED_PREMISES"}
    record = {**record, "board_number": payload["scope_identity"][3]}
    upstream_counts = {}
    if unresolved:
        upstream_hands = (recovery.get("deal") or {}).get("hands", {})
        upstream_counts = {"upstream_" + key: sum(len(h.get(group, [])) for h in upstream_hands.values())
                           for key, group in (("observed_card_count", "observed_cards"),
                                              ("declared_card_count", "declared_cards"),
                                              ("inferred_card_count", "inferred_cards"))}
    return _render_hands(record, hands, ["UNRESOLVED_LIMIT_NO_NATIVE_OWNERSHIP"] if unresolved else [], metadata, ownership={**counts,
        **upstream_counts,
        "ownership_card_count": sum(map(len, hands.values())),
        "representation": "QUALIFIED_EVIDENCE_UNKNOWN" if unresolved else "QUALIFIED_CONDITIONAL_OWNERSHIP_" + ("FULL" if recovery["status"] == "VERIFIED_FULL" else "PARTIAL")})
