"""Fail-closed temporal fusion for visible Bridgit card observations.

The pixel layer may report cards that are visibly present in a hand or on the
table.  This module only joins repeated, source-bound observations for one
explicit deal.  It never derives an unseen card from the deck complement or
from turn order.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence

from bridge_contracts.video_deal import SEATS, canonicalize_video_deal

SCHEMA = "bridgit-visible-card-timeline/v2"
VERSION = "bridgit-visible-card-timeline-v2"
RESULT_SCOPE = "SHADOW_ONLY"
MAX_OBSERVATIONS = 20_000
MAX_CARDS_PER_OBSERVATION = 52
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CARD = re.compile(r"^(A|K|Q|J|T|9|8|7|6|5|4|3|2)(H|C|D|S)$")
_SOURCES = frozenset({"HAND", "PLAYED"})


class VisibleTimelineError(ValueError):
    """The timeline cannot be evaluated without weakening its evidence gates."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _required_sha(value: Any, field: str) -> str:
    text = str(value or "")
    if not _SHA256.fullmatch(text):
        raise VisibleTimelineError(f"{field} must be 64 lowercase hex characters")
    return text


def _deal_identity(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise VisibleTimelineError("deal_identity must be an object")
    kind = str(value.get("kind") or "").strip()
    scope = str(value.get("scope") or "").strip()
    identity = str(value.get("value") or "").strip()
    if kind not in {"EXPLICIT_BOARD", "VISUAL_ANCHOR"}:
        raise VisibleTimelineError("deal_identity kind is not source-bound")
    if not scope or not identity or len(scope) > 128 or len(identity) > 128:
        raise VisibleTimelineError("deal_identity is incomplete")
    return {"kind": kind, "scope": scope, "value": identity}


def _card(value: Any, field: str) -> str:
    text = str(value or "").upper()
    if not _CARD.fullmatch(text):
        raise VisibleTimelineError(f"{field} is not a canonical card")
    return text


def _seat(value: Any, field: str) -> str:
    text = str(value or "").upper()
    if text not in SEATS:
        raise VisibleTimelineError(f"{field} is not a logical seat")
    return text


def _confidence(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise VisibleTimelineError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise VisibleTimelineError(f"{field} must be numeric") from exc
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise VisibleTimelineError(f"{field} is outside [0,1]")
    return result


def _empty_result(identity: Mapping[str, str], status: str) -> dict[str, Any]:
    hands = {seat: [] for seat in SEATS}
    deal = canonicalize_video_deal({"hands": hands}).to_dict()
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "result_scope": RESULT_SCOPE,
        "status": status,
        "deal_identity": dict(identity),
        "deal": deal,
        "observed_card_count": 0,
        "unknown_slots": {seat: 13 for seat in SEATS},
        "evidence": [],
        "rejected": [],
        "conflicts": [],
        "hidden_hand_inference_used": False,
        "deck_complement_used": False,
        "canonical_promotion_allowed": False,
    }


def _seal(result: dict[str, Any]) -> dict[str, Any]:
    result["receipt_sha256"] = _sha(
        {key: value for key, value in result.items() if key != "receipt_sha256"}
    )
    return result


def fuse_visible_timeline(
    observations: Sequence[Mapping[str, Any]],
    *,
    min_independent_frames: int = 2,
    min_confidence: float = 0.98,
) -> dict[str, Any]:
    """Fuse visible card ownership without inventing hidden cards.

    Every accepted ``card + seat`` pair must be seen in at least
    ``min_independent_frames`` byte-distinct *and* pixel-distinct frames.  The
    caller must assign the logical seat from verified geometry; time or play
    order is not a seat source.
    """

    if isinstance(min_independent_frames, bool) or not isinstance(
        min_independent_frames, int
    ):
        raise VisibleTimelineError("min_independent_frames must be an integer")
    if not 2 <= min_independent_frames <= 16:
        raise VisibleTimelineError("min_independent_frames must be in [2,16]")
    minimum = _confidence(min_confidence, "min_confidence")
    if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes)):
        raise VisibleTimelineError("observations must be an array")
    if not observations or len(observations) > MAX_OBSERVATIONS:
        raise VisibleTimelineError("observation count is outside the bounded range")

    identity: dict[str, str] | None = None
    identity_digest: str | None = None
    frame_pixels: dict[str, str] = {}
    pixel_frames: dict[str, str] = {}
    frame_records: dict[str, str] = {}
    votes: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    evidence_claims: dict[str, tuple[str, str]] = {}
    raw_owners: dict[str, set[str]] = defaultdict(set)
    rejected: list[dict[str, Any]] = []

    for frame_index, raw_frame in enumerate(observations):
        if not isinstance(raw_frame, Mapping):
            raise VisibleTimelineError(f"observations[{frame_index}] must be an object")
        current_identity = _deal_identity(raw_frame.get("deal_identity"))
        current_digest = _sha(current_identity)
        if identity is None:
            identity = current_identity
            identity_digest = current_digest
        elif current_digest != identity_digest:
            raise VisibleTimelineError("observations cross explicit deal identities")

        frame_sha = _required_sha(
            raw_frame.get("frame_sha256"),
            f"observations[{frame_index}].frame_sha256",
        )
        pixel_sha = _required_sha(
            raw_frame.get("decoded_pixel_sha256"),
            f"observations[{frame_index}].decoded_pixel_sha256",
        )
        try:
            timestamp_ms = int(raw_frame.get("timestamp_ms"))
        except (TypeError, ValueError) as exc:
            raise VisibleTimelineError("timestamp_ms must be an integer") from exc
        if timestamp_ms < 0 or isinstance(raw_frame.get("timestamp_ms"), bool):
            raise VisibleTimelineError("timestamp_ms must be non-negative")

        previous_frame = pixel_frames.get(pixel_sha)
        if previous_frame is not None and previous_frame != frame_sha:
            # A lossless re-encoding of the same pixels is replay, not support.
            rejected.append(
                {
                    "frame_sha256": frame_sha,
                    "reason": "DUPLICATE_DECODED_PIXELS",
                    "duplicates_frame_sha256": previous_frame,
                }
            )
            continue
        raw_cards = raw_frame.get("cards")
        if not isinstance(raw_cards, Sequence) or isinstance(raw_cards, (str, bytes)):
            raise VisibleTimelineError("frame cards must be an array")
        if len(raw_cards) > MAX_CARDS_PER_OBSERVATION:
            raise VisibleTimelineError("frame card count exceeds the bound")

        frame_cards: dict[str, str] = {}
        normalized_cards: list[dict[str, Any]] = []
        for card_index, raw in enumerate(raw_cards):
            if not isinstance(raw, Mapping):
                raise VisibleTimelineError("card observation must be an object")
            card = _card(raw.get("card"), f"cards[{card_index}].card")
            seat = _seat(raw.get("seat"), f"cards[{card_index}].seat")
            source = str(raw.get("source") or "").upper()
            if source not in _SOURCES:
                raise VisibleTimelineError("card source must be HAND or PLAYED")
            confidence = _confidence(
                raw.get("confidence"), f"cards[{card_index}].confidence"
            )
            evidence_pixel_sha = _required_sha(
                raw.get("evidence_pixel_sha256"),
                f"cards[{card_index}].evidence_pixel_sha256",
            )
            previous_seat = frame_cards.get(card)
            if previous_seat is not None:
                raise VisibleTimelineError("one frame repeats or conflicts on a card")
            frame_cards[card] = seat
            normalized_cards.append(
                {
                    "card": card,
                    "seat": seat,
                    "source": source,
                    "confidence": confidence,
                    "evidence_pixel_sha256": evidence_pixel_sha,
                }
            )
            if confidence < minimum:
                rejected.append(
                    {
                        "frame_sha256": frame_sha,
                        "card": card,
                        "seat": seat,
                        "reason": "LOW_CONFIDENCE",
                        "confidence": confidence,
                    }
                )
                continue
            claim = (card, seat)
            previous_claim = evidence_claims.get(evidence_pixel_sha)
            if previous_claim is not None and previous_claim != claim:
                raise VisibleTimelineError(
                    "one card-pixel hash is reused across different claims"
                )
            evidence_claims[evidence_pixel_sha] = claim
            raw_owners[card].add(seat)
            previous_vote = votes[(card, seat)].get(evidence_pixel_sha)
            if previous_vote is not None:
                rejected.append(
                    {
                        "frame_sha256": frame_sha,
                        "card": card,
                        "seat": seat,
                        "reason": "DUPLICATE_CARD_PIXELS",
                        "duplicates_frame_sha256": previous_vote["frame_sha256"],
                        "evidence_pixel_sha256": evidence_pixel_sha,
                    }
                )
                continue
            votes[(card, seat)][evidence_pixel_sha] = {
                "frame_sha256": frame_sha,
                "decoded_pixel_sha256": pixel_sha,
                "evidence_pixel_sha256": evidence_pixel_sha,
                "timestamp_ms": timestamp_ms,
                "source": source,
                "confidence": confidence,
            }

        record_digest = _sha(
            {
                "decoded_pixel_sha256": pixel_sha,
                "timestamp_ms": timestamp_ms,
                "cards": sorted(
                    normalized_cards,
                    key=lambda item: (item["card"], item["seat"], item["source"]),
                ),
            }
        )
        previous_record = frame_records.get(frame_sha)
        if previous_record is not None and previous_record != record_digest:
            raise VisibleTimelineError(
                "one frame hash has inconsistent observation records"
            )
        frame_records[frame_sha] = record_digest
        previous_pixel = frame_pixels.get(frame_sha)
        if previous_pixel is not None and previous_pixel != pixel_sha:
            raise VisibleTimelineError("one frame hash has multiple decoded identities")
        frame_pixels[frame_sha] = pixel_sha
        pixel_frames[pixel_sha] = frame_sha

    assert identity is not None
    result = _empty_result(identity, "PENDING_TEMPORAL_EVIDENCE")
    conflicts = [
        {"card": card, "seats": sorted(seats), "reason": "CROSS_SEAT_CONFLICT"}
        for card, seats in sorted(raw_owners.items())
        if len(seats) > 1
    ]
    if conflicts:
        result["status"] = "CONFLICT"
        result["conflicts"] = conflicts
        result["rejected"] = rejected
        return _seal(result)

    accepted: dict[str, list[str]] = {seat: [] for seat in SEATS}
    evidence: list[dict[str, Any]] = []
    for (card, seat), frame_votes in sorted(votes.items()):
        support = len(frame_votes)
        if support < min_independent_frames:
            rejected.append(
                {
                    "card": card,
                    "seat": seat,
                    "reason": "PENDING_TEMPORAL_SUPPORT",
                    "independent_frames": support,
                }
            )
            continue
        if len(accepted[seat]) >= 13:
            conflicts.append(
                {"seat": seat, "card": card, "reason": "HAND_LIMIT_EXCEEDED"}
            )
            continue
        accepted[seat].append(card)
        ordered_votes = sorted(
            frame_votes.values(),
            key=lambda item: (item["timestamp_ms"], item["frame_sha256"]),
        )
        evidence.append(
            {
                "card": card,
                "seat": seat,
                "independent_frames": support,
                "sources": sorted({item["source"] for item in ordered_votes}),
                "minimum_confidence": min(item["confidence"] for item in ordered_votes),
                "observations": ordered_votes,
            }
        )

    if conflicts:
        result["status"] = "CONFLICT"
        result["conflicts"] = conflicts
        result["rejected"] = rejected
        return _seal(result)

    deal = canonicalize_video_deal({"hands": accepted}).to_dict()
    normalized = {seat: list(deal["hands"][seat]["cards"]) for seat in SEATS}
    observed = sum(len(cards) for cards in normalized.values())
    complete = observed == 52 and all(len(normalized[seat]) == 13 for seat in SEATS)
    result.update(
        {
            "status": (
                "SHADOW_COMPLETE_VISUAL_CANDIDATE"
                if complete
                else "SHADOW_PARTIAL_TEMPORAL_OBSERVATION"
                if observed
                else "PENDING_TEMPORAL_EVIDENCE"
            ),
            "deal": deal,
            "observed_card_count": observed,
            "unknown_slots": {seat: 13 - len(normalized[seat]) for seat in SEATS},
            "evidence": evidence,
            "rejected": rejected,
        }
    )
    return _seal(result)


__all__ = [
    "MAX_CARDS_PER_OBSERVATION",
    "MAX_OBSERVATIONS",
    "RESULT_SCOPE",
    "SCHEMA",
    "VERSION",
    "VisibleTimelineError",
    "fuse_visible_timeline",
]
