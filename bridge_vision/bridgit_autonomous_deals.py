"""Autonomous, source-bound reconstruction of Bridgit deals over time.

The pixel observers remain responsible for producing direct ``HAND`` and
``PLAYED`` card observations plus a stable visual deal marker.  This module
segments a video without operator-supplied board identities, fuses repeated
observations, reconstructs each original hand, and emits PBN only when the
result is mathematically unique.

No language model, screenshot review, auction assumption, turn-order guess, or
probabilistic hidden-hand split participates in this module.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from bridge_contracts.video_deal import (
    FULL_DECK,
    SEATS,
    BridgeVideoDealContractError,
    CanonicalHand,
    CanonicalVideoDeal,
    canonicalize_video_deal,
)
from bridge_vision.multiframe import validate_full_deal

AUTONOMOUS_DEALS_SCHEMA = "bridgit-autonomous-deals/v1"
AUTONOMOUS_DEALS_VERSION = "bridgit-autonomous-deals-v1"
EXACT_COMPLEMENT_VERSION = "bridgit-autonomous-exact-complement-v1"
MAX_FRAMES = 100_000
MIN_HAND_RESET_CARDS = 8
MIN_HAND_RESET_CHANGED_CARDS = 8
MIN_HAND_RESET_FRAMES = 2
MIN_HAND_REPLACEMENT_BASELINE_CARDS = 4
MIN_HAND_REPLACEMENT_FULL_CARDS = 13
MIN_HAND_REPLACEMENT_DISAPPEARED_CARDS = 1
MIN_HAND_REAPPEARED_CARDS = 4
MIN_HAND_REAPPEARED_ABSENCE_FRAMES = 3
_SOURCES = frozenset({"HAND", "PLAYED"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SUITS = "SHDC"
_RANKS = "AKQJT98765432"


class AutonomousDealsError(ValueError):
    """Input cannot be used without weakening an evidence gate."""


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha(value: Any, field: str) -> str:
    text = str(value or "")
    if not _SHA256.fullmatch(text):
        raise AutonomousDealsError(f"{field} must be lowercase SHA-256")
    return text


def _card(value: Any) -> str:
    try:
        normalized = canonicalize_video_deal({"hands": {"N": [value]}}).to_dict()
    except BridgeVideoDealContractError as exc:
        raise AutonomousDealsError("invalid observed card") from exc
    return normalized["hands"]["N"]["cards"][0]


def _identity(scope: str, marker: str, occurrence: int) -> dict[str, str]:
    return {
        "kind": "AUTONOMOUS_VIDEO_SEGMENT",
        "scope": scope,
        "value": f"{occurrence:04d}-{marker[:16]}",
    }


def _hand_to_pbn(cards: Iterable[str]) -> str:
    by_suit = {suit: [] for suit in _SUITS}
    for card in cards:
        by_suit[card[1]].append(card[0])
    return ".".join("".join(sorted(by_suit[suit], key=_RANKS.index)) for suit in _SUITS)


def _to_pbn(deal: Mapping[str, Any]) -> str:
    hands = deal["hands"]
    return "N:" + " ".join(_hand_to_pbn(hands[seat]["cards"]) for seat in SEATS)


def _card_sort_key(card: str) -> tuple[int, int]:
    return _SUITS.index(card[1]), _RANKS.index(card[0])


def _complete_exact_complement(
    by_seat: Mapping[str, list[str]],
    *,
    evidence_sha256: str,
    deal_identity: Mapping[str, str],
) -> CanonicalVideoDeal:
    """Complete one seat only when three source-observed hands are complete."""

    observed = canonicalize_video_deal({"hands": by_seat})
    incomplete = [seat for seat in SEATS if observed.hands[seat].unknown_count]
    if len(incomplete) != 1:
        raise BridgeVideoDealContractError(
            "exact complement requires exactly one incomplete seat"
        )
    missing_seat = incomplete[0]
    if any(
        observed.hands[seat].unknown_count for seat in SEATS if seat != missing_seat
    ):
        raise BridgeVideoDealContractError(
            "exact complement requires three complete observed seats"
        )
    observed_cards = {card for hand in observed.hands.values() for card in hand.cards}
    computed = sorted(FULL_DECK - observed_cards, key=_card_sort_key)
    missing_hand = observed.hands[missing_seat]
    if len(computed) != missing_hand.unknown_count:
        raise BridgeVideoDealContractError(
            "deck complement does not match the incomplete seat capacity"
        )
    completed = dict(observed.hands)
    completed[missing_seat] = CanonicalHand(
        cards=tuple(sorted((*missing_hand.cards, *computed), key=_card_sort_key)),
        unknown_count=0,
    )
    derivation = {
        "kind": "EXACT_DECK_COMPLEMENT",
        "version": EXACT_COMPLEMENT_VERSION,
        "seat": missing_seat,
        "computed_cards": computed,
        "observed_card_count": len(observed_cards),
        "evidence_sha256": evidence_sha256,
        "deal_identity": dict(deal_identity),
    }
    return CanonicalVideoDeal(hands=completed, derivations=(derivation,))


def _normalize_frame(raw: Mapping[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise AutonomousDealsError("frame observation must be an object")
    frame_sha = _sha(raw.get("frame_sha256"), "frame_sha256")
    pixel_sha = _sha(raw.get("decoded_pixel_sha256"), "decoded_pixel_sha256")
    marker = _sha(raw.get("deal_marker_sha256"), "deal_marker_sha256")
    timestamp = raw.get("timestamp_ms")
    if isinstance(timestamp, bool):
        raise AutonomousDealsError("timestamp_ms must be a non-negative integer")
    try:
        timestamp_ms = int(timestamp)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AutonomousDealsError(
            "timestamp_ms must be a non-negative integer"
        ) from exc
    if timestamp_ms < 0:
        raise AutonomousDealsError("timestamp_ms must be a non-negative integer")

    raw_cards = raw.get("cards") or []
    if not isinstance(raw_cards, list) or len(raw_cards) > 52:
        raise AutonomousDealsError("frame cards must be an array of at most 52")
    cards = []
    frame_seen: set[str] = set()
    for position, item in enumerate(raw_cards):
        if not isinstance(item, Mapping):
            raise AutonomousDealsError("card observation must be an object")
        card = _card(item.get("card"))
        seat = str(item.get("seat") or "").upper()
        source = str(item.get("source") or "").upper()
        if seat not in SEATS:
            raise AutonomousDealsError("card observation requires a logical seat")
        if source not in _SOURCES:
            raise AutonomousDealsError("card source must be HAND or PLAYED")
        try:
            confidence = float(item.get("confidence"))
        except (TypeError, ValueError, OverflowError) as exc:
            raise AutonomousDealsError("card confidence must be numeric") from exc
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise AutonomousDealsError("card confidence is outside [0,1]")
        evidence_sha = _sha(item.get("evidence_pixel_sha256"), "evidence_pixel_sha256")
        if card in frame_seen:
            raise AutonomousDealsError("one frame repeats or conflicts on a card")
        frame_seen.add(card)
        cards.append(
            {
                "card": card,
                "seat": seat,
                "source": source,
                "confidence": confidence,
                "evidence_pixel_sha256": evidence_sha,
                "position": position,
            }
        )
    cards.sort(key=lambda item: (item["card"], item["seat"], item["source"]))
    return {
        "index": index,
        "frame_sha256": frame_sha,
        "decoded_pixel_sha256": pixel_sha,
        "deal_marker_sha256": marker,
        "timestamp_ms": timestamp_ms,
        "cards": cards,
    }


def _visible_hand_signature(
    frame: Mapping[str, Any], min_confidence: float
) -> frozenset[tuple[str, str]]:
    return frozenset(
        (str(item["seat"]), str(item["card"]))
        for item in frame["cards"]
        if item["source"] == "HAND" and item["confidence"] >= min_confidence
    )


def _is_visible_hand_reset(
    baseline: frozenset[tuple[str, str]], candidate: frozenset[tuple[str, str]]
) -> bool:
    if min(len(baseline), len(candidate)) < MIN_HAND_RESET_CARDS:
        return False
    novel = len(candidate - baseline)
    disappeared = len(baseline - candidate)
    union = len(baseline | candidate)
    similarity = len(baseline & candidate) / union if union else 1.0
    return (
        novel >= MIN_HAND_RESET_CHANGED_CARDS
        and disappeared >= MIN_HAND_RESET_CHANGED_CARDS
        and similarity < 0.50
    )


def _same_reset_candidate(
    left: frozenset[tuple[str, str]], right: frozenset[tuple[str, str]]
) -> bool:
    union = len(left | right)
    return bool(union) and len(left & right) / union >= 0.75


def _is_visible_seat_replacement(
    baseline: frozenset[tuple[str, str]], candidate: frozenset[tuple[str, str]]
) -> bool:
    """Detect a stable full-hand replacement after the old hand was depleted.

    A lesson can redeal while only a few cards from the preceding hand remain.
    Requiring eight cards to disappear therefore misses a replacement that has
    substantial overlap with that depleted hand.  A full 13-card candidate is
    safe to treat separately when the same seat was already visible, at least
    eight cards are new, and at least one prior card disappears.  A newly
    exposed dummy has no same-seat baseline and cannot trigger this rule.
    """

    for seat in SEATS:
        previous = {card for owner, card in baseline if owner == seat}
        current = {card for owner, card in candidate if owner == seat}
        if not (
            MIN_HAND_REPLACEMENT_BASELINE_CARDS
            <= len(previous)
            < MIN_HAND_REPLACEMENT_FULL_CARDS
            and len(current) == MIN_HAND_REPLACEMENT_FULL_CARDS
        ):
            continue
        novel = len(current - previous)
        disappeared = len(previous - current)
        union = len(previous | current)
        similarity = len(previous & current) / union if union else 1.0
        if (
            novel >= MIN_HAND_RESET_CHANGED_CARDS
            and disappeared >= MIN_HAND_REPLACEMENT_DISAPPEARED_CARDS
            and similarity < 0.50
        ):
            return True
    return False


def _is_visible_hand_replenishment(
    signature: frozenset[tuple[str, str]],
    absence_streaks: Mapping[tuple[str, str], int],
) -> bool:
    """Detect a replay/redeal even when the visible hands are unchanged.

    A card removed from a visible hand cannot reappear during the same play.
    Bridgit lessons can restart the same N/S cards while redealing the hidden
    defenders, so an identity-only reset test is insufficient.  Requiring
    several cards to have been absent for several independently sampled frames
    keeps transient observer dropouts from creating a new segment.
    """

    if len(signature) < MIN_HAND_RESET_CARDS:
        return False
    reappeared = sum(
        absence_streaks.get(item, 0) >= MIN_HAND_REAPPEARED_ABSENCE_FRAMES
        for item in signature
    )
    return reappeared >= MIN_HAND_REAPPEARED_CARDS


def _segment_frames(
    frames: list[dict[str, Any]], min_confidence: float
) -> list[dict[str, Any]]:
    """Split on marker changes and confirmed visible-hand resets.

    A played card can only disappear from an original hand.  A large burst of
    new HAND cards together with a large disappearance therefore identifies a
    redeal even when the board-number pixels remain unchanged.  Two mutually
    consistent frames are required so a single recognition outlier cannot
    create a segment.
    """

    groups: list[tuple[str, list[dict[str, Any]]]] = []
    marker_group: list[dict[str, Any]] = []
    marker: str | None = None
    for frame in frames:
        current_marker = frame["deal_marker_sha256"]
        if marker_group and current_marker != marker:
            groups.append((str(marker), marker_group))
            marker_group = []
        marker = current_marker
        marker_group.append(frame)
    if marker_group:
        groups.append((str(marker), marker_group))

    raw_segments: list[tuple[str, list[dict[str, Any]]]] = []
    for group_marker, group_frames in groups:
        current: list[dict[str, Any]] = []
        baseline: frozenset[tuple[str, str]] = frozenset()
        absence_streaks: dict[tuple[str, str], int] = {}
        pending: list[dict[str, Any]] = []
        pending_signature: frozenset[tuple[str, str]] = frozenset()
        for frame in group_frames:
            signature = _visible_hand_signature(frame, min_confidence)
            replenished = _is_visible_hand_replenishment(
                signature, absence_streaks
            )
            for item in frozenset(baseline | frozenset(absence_streaks)):
                absence_streaks[item] = (
                    0 if item in signature else absence_streaks.get(item, 0) + 1
                )

            if pending and _same_reset_candidate(pending_signature, signature):
                pending.append(frame)
                pending_signature = frozenset(pending_signature | signature)
                if len(pending) >= MIN_HAND_RESET_FRAMES:
                    raw_segments.append((group_marker, current))
                    current = pending
                    baseline = frozenset().union(
                        *(
                            _visible_hand_signature(item, min_confidence)
                            for item in pending
                        )
                    )
                    absence_streaks = {item: 0 for item in baseline}
                    pending = []
                    pending_signature = frozenset()
                continue
            if pending:
                current.extend(pending)
                baseline = frozenset(
                    baseline.union(
                        *(
                            _visible_hand_signature(item, min_confidence)
                            for item in pending
                        )
                    )
                )
                pending = []
                pending_signature = frozenset()

            if current and (
                _is_visible_hand_reset(baseline, signature)
                or _is_visible_seat_replacement(baseline, signature)
                or replenished
            ):
                pending = [frame]
                pending_signature = signature
                continue
            current.append(frame)
            baseline = frozenset(baseline | signature)
            for item in signature:
                absence_streaks[item] = 0
        current.extend(pending)
        if current:
            raw_segments.append((group_marker, current))

    return [
        {"marker": marker, "occurrence": occurrence, "frames": segment_frames}
        for occurrence, (marker, segment_frames) in enumerate(raw_segments, 1)
    ]


def _reconstruct_segment(
    segment: Mapping[str, Any],
    *,
    source_scope: str,
    min_confidence: float,
    min_temporal_support: int,
    allow_exact_complement: bool,
) -> dict[str, Any]:
    frames = segment["frames"]
    marker = str(segment["marker"])
    identity = _identity(source_scope, marker, int(segment["occurrence"]))
    votes: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    raw_owners: dict[str, set[str]] = defaultdict(set)
    evidence_claims: dict[str, tuple[str, str]] = {}
    decoded_frames: dict[str, str] = {}
    rejected: list[dict[str, Any]] = []
    evidence_conflicts: list[dict[str, Any]] = []
    for frame in frames:
        previous_frame = decoded_frames.get(frame["decoded_pixel_sha256"])
        if previous_frame is not None:
            rejected.append(
                {
                    "frame_sha256": frame["frame_sha256"],
                    "reason": "DUPLICATE_DECODED_PIXELS",
                    "duplicates_frame_sha256": previous_frame,
                }
            )
            continue
        decoded_frames[frame["decoded_pixel_sha256"]] = frame["frame_sha256"]
        for item in frame["cards"]:
            if item["confidence"] < min_confidence:
                rejected.append(
                    {
                        "frame_sha256": frame["frame_sha256"],
                        "card": item["card"],
                        "seat": item["seat"],
                        "reason": "LOW_CONFIDENCE",
                    }
                )
                continue
            claim = (item["card"], item["seat"])
            evidence_sha = item["evidence_pixel_sha256"]
            previous_claim = evidence_claims.get(evidence_sha)
            if previous_claim is not None and previous_claim != claim:
                evidence_conflicts.append(
                    {
                        "evidence_pixel_sha256": evidence_sha,
                        "claims": [list(previous_claim), list(claim)],
                        "reason": "EVIDENCE_REUSED_ACROSS_CLAIMS",
                    }
                )
                continue
            evidence_claims[evidence_sha] = claim
            raw_owners[item["card"]].add(item["seat"])
            previous_vote = votes[claim].get(evidence_sha)
            if previous_vote is not None:
                rejected.append(
                    {
                        "frame_sha256": frame["frame_sha256"],
                        "card": item["card"],
                        "seat": item["seat"],
                        "reason": "DUPLICATE_CARD_PIXELS",
                        "duplicates_frame_sha256": previous_vote["frame_sha256"],
                        "evidence_pixel_sha256": evidence_sha,
                    }
                )
                continue
            votes[claim][evidence_sha] = {
                "frame_sha256": frame["frame_sha256"],
                "decoded_pixel_sha256": frame["decoded_pixel_sha256"],
                "timestamp_ms": frame["timestamp_ms"],
                "source": item["source"],
                "confidence": item["confidence"],
                "evidence_pixel_sha256": evidence_sha,
            }

    accepted: list[dict[str, Any]] = []
    for (card, seat), frame_votes in sorted(votes.items()):
        items = list(frame_votes.values())
        if len(items) < min_temporal_support:
            rejected.append(
                {
                    "card": card,
                    "seat": seat,
                    "reason": "PENDING_TEMPORAL_SUPPORT",
                    "support": len(items),
                }
            )
            continue
        accepted.append(
            {
                "card": card,
                "seat": seat,
                "sources": sorted({item["source"] for item in items}),
                "support": len(items),
                "first_timestamp_ms": min(item["timestamp_ms"] for item in items),
                "last_timestamp_ms": max(item["timestamp_ms"] for item in items),
                "minimum_confidence": min(item["confidence"] for item in items),
                "evidence_pixel_sha256s": sorted(
                    {item["evidence_pixel_sha256"] for item in items}
                ),
            }
        )

    conflicts = list(evidence_conflicts)
    conflicts.extend(
        [
            {
                "card": card,
                "seats": sorted(seats),
                "reason": "CROSS_SEAT_CONFLICT",
            }
            for card, seats in sorted(raw_owners.items())
            if len(seats) > 1
        ]
    )
    by_seat: dict[str, list[str]] = {seat: [] for seat in SEATS}
    for item in accepted:
        by_seat[item["seat"]].append(item["card"])
    for seat in SEATS:
        if len(by_seat[seat]) > 13:
            conflicts.append(
                {
                    "seat": seat,
                    "count": len(by_seat[seat]),
                    "reason": "HAND_LIMIT_EXCEEDED",
                }
            )

    evidence = {
        "deal_identity": identity,
        "deal_marker_sha256": marker,
        "frames": [
            {
                "frame_sha256": frame["frame_sha256"],
                "decoded_pixel_sha256": frame["decoded_pixel_sha256"],
                "timestamp_ms": frame["timestamp_ms"],
            }
            for frame in frames
        ],
        "accepted": accepted,
    }
    evidence_sha = _canonical_hash(evidence)
    if conflicts:
        return {
            "status": "CONFLICT",
            "deal_identity": identity,
            "deal_marker_sha256": marker,
            "first_timestamp_ms": frames[0]["timestamp_ms"],
            "last_timestamp_ms": frames[-1]["timestamp_ms"],
            "observed_card_count": 0,
            "deal": None,
            "pbn": None,
            "evidence_sha256": evidence_sha,
            "accepted": [],
            "rejected": rejected,
            "conflicts": conflicts,
        }

    deal = canonicalize_video_deal({"hands": by_seat}).to_dict()
    status = "PARTIAL"
    completion = "NONE"
    if all(deal["hands"][seat]["unknown_count"] == 0 for seat in SEATS):
        status = "COMPLETE_OBSERVED"
        completion = "ALL_CARDS_OBSERVED"
    elif allow_exact_complement:
        try:
            deal = _complete_exact_complement(
                by_seat,
                evidence_sha256=evidence_sha,
                deal_identity=identity,
            ).to_dict()
        except BridgeVideoDealContractError:
            pass
        else:
            status = "COMPLETE_DERIVED_EXACT"
            completion = "ONE_SEAT_EXACT_DECK_COMPLEMENT"

    validation = validate_full_deal(deal)
    pbn = _to_pbn(deal) if validation["full_board"] else None
    if status.startswith("COMPLETE") and not validation["full_board"]:
        raise AutonomousDealsError("complete result failed the independent deck check")
    return {
        "status": status,
        "completion": completion,
        "deal_identity": identity,
        "deal_marker_sha256": marker,
        "first_timestamp_ms": frames[0]["timestamp_ms"],
        "last_timestamp_ms": frames[-1]["timestamp_ms"],
        "observed_card_count": len(accepted),
        "deal": deal,
        "pbn": pbn,
        "validation": validation,
        "evidence_sha256": evidence_sha,
        "accepted": accepted,
        "rejected": rejected,
        "conflicts": [],
    }


def reconstruct_autonomous_deals(
    raw_frames: Iterable[Mapping[str, Any]],
    *,
    source_scope: str,
    min_confidence: float = 0.95,
    min_temporal_support: int = 2,
    allow_exact_complement: bool = True,
) -> dict[str, Any]:
    """Reconstruct source-ordered deals without human-supplied frame grouping."""

    scope = str(source_scope or "").strip()
    if not scope or len(scope) > 256:
        raise AutonomousDealsError("source_scope is required")
    if not 0.0 <= min_confidence <= 1.0:
        raise AutonomousDealsError("min_confidence is outside [0,1]")
    if min_temporal_support < 2 or min_temporal_support > 32:
        raise AutonomousDealsError("min_temporal_support is outside 2..32")
    if not isinstance(allow_exact_complement, bool):
        raise AutonomousDealsError("allow_exact_complement must be boolean")

    frames = []
    for index, raw in enumerate(raw_frames):
        if index >= MAX_FRAMES:
            raise AutonomousDealsError("frame count exceeds the bound")
        frames.append(_normalize_frame(raw, index))
    if not frames:
        raise AutonomousDealsError("at least one frame is required")
    frames.sort(key=lambda item: (item["timestamp_ms"], item["frame_sha256"]))
    seen_frames: set[str] = set()
    seen_timestamps: set[int] = set()
    for frame in frames:
        if frame["frame_sha256"] in seen_frames:
            raise AutonomousDealsError("duplicate source frame")
        if frame["timestamp_ms"] in seen_timestamps:
            raise AutonomousDealsError("duplicate source timestamp")
        seen_frames.add(frame["frame_sha256"])
        seen_timestamps.add(frame["timestamp_ms"])

    deals = [
        _reconstruct_segment(
            segment,
            source_scope=scope,
            min_confidence=min_confidence,
            min_temporal_support=min_temporal_support,
            allow_exact_complement=allow_exact_complement,
        )
        for segment in _segment_frames(frames, min_confidence)
    ]
    counts = {
        status: sum(deal["status"] == status for deal in deals)
        for status in (
            "COMPLETE_OBSERVED",
            "COMPLETE_DERIVED_EXACT",
            "PARTIAL",
            "CONFLICT",
        )
    }
    receipt = {
        "schema": AUTONOMOUS_DEALS_SCHEMA,
        "version": AUTONOMOUS_DEALS_VERSION,
        "source_scope": scope,
        "status": (
            "COMPLETE"
            if deals and counts["PARTIAL"] == 0 and counts["CONFLICT"] == 0
            else "REVIEW"
        ),
        "deal_count": len(deals),
        "status_counts": counts,
        "deals": deals,
        "uses_language_model": False,
        "requires_screenshot_review": False,
        "exact_complement_enabled": bool(allow_exact_complement),
        "canonical_promotion_allowed": False,
    }
    receipt["receipt_sha256"] = _canonical_hash(receipt)
    return receipt


__all__ = [
    "AUTONOMOUS_DEALS_SCHEMA",
    "AUTONOMOUS_DEALS_VERSION",
    "EXACT_COMPLEMENT_VERSION",
    "MAX_FRAMES",
    "MIN_HAND_RESET_CARDS",
    "MIN_HAND_RESET_CHANGED_CARDS",
    "MIN_HAND_RESET_FRAMES",
    "AutonomousDealsError",
    "reconstruct_autonomous_deals",
]
