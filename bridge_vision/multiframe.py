"""Evidence-based multi-frame bridge deal reconstruction.

Frames are NEVER linked because they are close in time. A frame may join an
existing deal track only through scoped explicit identity or sufficiently strong
seat+card overlap with no cross-seat conflict. Ambiguous matches remain review
items instead of being guessed into a deal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Iterable, Mapping

from bridge_contracts.video_deal import FULL_DECK, SEATS, canonicalize_video_deal

MULTIFRAME_VERSION = "bridge-vision-multiframe-v3"
FULL_DEAL_VALIDATION_VERSION = "bridge-full-deal-validation-v1"


class MultiFrameError(ValueError):
    pass


def validate_full_deal(deal: Mapping[str, Any]) -> dict[str, Any]:
    """Independently prove the 13-per-seat / 52-unique-card invariant."""

    hands = deal.get("hands") if isinstance(deal, Mapping) else None
    if not isinstance(hands, Mapping):
        raise MultiFrameError("canonical deal hands must be an object")
    seat_counts: dict[str, int] = {}
    cards: list[str] = []
    reasons: list[str] = []
    for seat in SEATS:
        hand = hands.get(seat)
        if not isinstance(hand, Mapping) or not isinstance(hand.get("cards"), (list, tuple)):
            raise MultiFrameError(f"canonical deal hand {seat} is invalid")
        seat_cards = [str(card) for card in hand["cards"]]
        seat_counts[seat] = len(seat_cards)
        cards.extend(seat_cards)
        if len(seat_cards) != 13:
            reasons.append(f"HAND_{seat}_NOT_13")
    unique_cards = set(cards)
    if len(cards) != 52:
        reasons.append("TOTAL_NOT_52")
    if len(unique_cards) != len(cards):
        reasons.append("DUPLICATE_CARDS")
    if unique_cards != set(FULL_DECK):
        reasons.append("DECK_MISMATCH")
    full = not reasons
    return {
        "version": FULL_DEAL_VALIDATION_VERSION,
        "status": "PASS" if full else "REVIEW",
        "full_board": full,
        "seat_counts": seat_counts,
        "total_cards": len(cards),
        "unique_cards": len(unique_cards),
        "uses_explicit_derivation": bool(deal.get("derivations")),
        "review_reasons": reasons,
    }


def _pairs(record: Mapping[str, Any]) -> set[tuple[str, str]]:
    deal = record.get("deal")
    if not isinstance(deal, Mapping):
        return set()
    hands = deal.get("hands")
    if not isinstance(hands, Mapping):
        return set()
    derived: set[tuple[str, str]] = set()
    derivations = deal.get("derivations")
    if isinstance(derivations, (list, tuple)):
        for item in derivations:
            if not isinstance(item, Mapping):
                continue
            seat = str(item.get("seat") or "")
            computed = item.get("computed_cards") or []
            if seat in SEATS and isinstance(computed, (list, tuple)):
                derived.update((seat, str(card)) for card in computed)
    out: set[tuple[str, str]] = set()
    for seat in SEATS:
        hand = hands.get(seat) or {}
        if not isinstance(hand, Mapping):
            continue
        cards = hand.get("cards") or []
        if not isinstance(cards, (list, tuple)):
            raise MultiFrameError("deal hand cards must be an array")
        for card in cards:
            pair = (seat, str(card))
            if pair not in derived:
                out.add(pair)
    return out


def _cross_seat_conflict(a: set[tuple[str, str]], b: set[tuple[str, str]]) -> bool:
    aa = {card: seat for seat, card in a}
    bb = {card: seat for seat, card in b}
    return any(card in bb and bb[card] != seat for card, seat in aa.items())


def _explicit_board_key(record: Mapping[str, Any]) -> str | None:
    # board_id/deal_key are assumed to be source-stable identifiers. A bare
    # board_number is not globally unique and can repeat later in one video, so
    # it becomes strong identity only when accompanied by an explicit scope.
    for key in ("board_id", "deal_key"):
        value = record.get(key)
        if value is not None and str(value).strip():
            return f"{key}:{str(value).strip()}"
    board_number = record.get("board_number")
    scope = record.get("board_scope") or record.get("source_deal_scope")
    if board_number is not None and str(board_number).strip() and scope is not None and str(scope).strip():
        return f"board_number:{str(scope).strip()}:{str(board_number).strip()}"
    return None


def _frame_identity(record: Mapping[str, Any]) -> str:
    for key in ("frame_sha256", "frame_file"):
        value = record.get(key)
        if value is not None and str(value).strip():
            return f"{key}:{str(value).strip()}"
    pairs = sorted(_pairs(record))
    return "pairs:" + repr(pairs)


@dataclass
class DealTrack:
    deal_id: str
    anchor_identity: str
    explicit_board_key: str | None = None
    frame_indices: list[int] = field(default_factory=list)
    frame_files: list[str] = field(default_factory=list)
    frame_identities: set[str] = field(default_factory=set)
    observed_pairs: set[tuple[str, str]] = field(default_factory=set)
    conflicts: list[dict[str, Any]] = field(default_factory=list)

    def canonical_deal(self) -> dict[str, Any]:
        hands = {seat: [] for seat in SEATS}
        for seat, card in sorted(self.observed_pairs):
            hands[seat].append(card)
        return canonicalize_video_deal({"hands": hands}, derive_fourth_hand=True).to_dict()

    def to_dict(self) -> dict[str, Any]:
        deal = self.canonical_deal()
        validation = validate_full_deal(deal)
        return {
            "version": MULTIFRAME_VERSION,
            "deal_id": self.deal_id,
            "status": "VERIFIED_FULL_BOARD" if validation["full_board"] else "REVIEW",
            "explicit_board_key": self.explicit_board_key,
            "frame_indices": list(self.frame_indices),
            "frame_files": list(self.frame_files),
            "observed_card_count": len(self.observed_pairs),
            "deal": deal,
            "validation": validation,
            "conflicts": list(self.conflicts),
        }


@dataclass(frozen=True)
class ReconstructionResult:
    tracks: tuple[DealTrack, ...]
    review_frames: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        deals = [track.to_dict() for track in self.tracks]
        verified = sum(item["validation"]["full_board"] for item in deals)
        review_deals = len(deals) - verified
        status = "COMPLETED" if deals and not review_deals and not self.review_frames else "REVIEW"
        return {
            "version": MULTIFRAME_VERSION,
            "status": status,
            "deal_count": len(self.tracks),
            "verified_full_board_count": verified,
            "review_deal_count": review_deals,
            "review_frame_count": len(self.review_frames),
            "canonical_promotion_allowed": False,
            "deals": deals,
            "review_frames": list(self.review_frames),
        }


def _score(track: DealTrack, record: Mapping[str, Any], *, min_shared_cards: int, min_overlap_ratio: float) -> float | None:
    pairs = _pairs(record)
    if not pairs:
        return None
    if _cross_seat_conflict(track.observed_pairs, pairs):
        return None

    board_key = _explicit_board_key(record)
    if board_key is not None and track.explicit_board_key is not None:
        return 1000.0 if board_key == track.explicit_board_key else None

    # If only one side has strong identity, evidence overlap may still bind the
    # frame. This avoids fragmenting a deal when a UI identifier disappears on
    # later frames, while never letting a mismatching explicit key through.
    shared = len(track.observed_pairs & pairs)
    smaller = min(len(track.observed_pairs), len(pairs))
    if smaller == 0 or shared < min_shared_cards:
        return None
    ratio = shared / smaller
    if ratio < min_overlap_ratio:
        return None
    return ratio + shared / 100.0


def reconstruct_deals(
    records: Iterable[Mapping[str, Any]],
    *,
    min_shared_cards: int = 4,
    min_overlap_ratio: float = 0.60,
) -> ReconstructionResult:
    """Fuse frame observations into stable deals without time-only matching."""
    if min_shared_cards < 1:
        raise ValueError("min_shared_cards must be positive")
    if not 0.0 < min_overlap_ratio <= 1.0:
        raise ValueError("min_overlap_ratio outside (0,1]")

    tracks: list[DealTrack] = []
    review: list[dict[str, Any]] = []
    seen_frames: dict[str, str] = {}

    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise MultiFrameError("frame record must be an object")
        pairs = _pairs(record)
        if not pairs:
            review.append({"frame_index": index, "reason": "NO_CARD_EVIDENCE", "frame_file": record.get("frame_file")})
            continue

        identity = _frame_identity(record)
        prior_deal = seen_frames.get(identity)
        if prior_deal is not None:
            review.append({
                "frame_index": index,
                "reason": "DUPLICATE_FRAME_EVIDENCE",
                "deal_id": prior_deal,
                "frame_file": record.get("frame_file"),
            })
            continue

        matches: list[tuple[float, DealTrack]] = []
        for track in tracks:
            score = _score(track, record, min_shared_cards=min_shared_cards, min_overlap_ratio=min_overlap_ratio)
            if score is not None:
                matches.append((score, track))
        matches.sort(key=lambda item: item[0], reverse=True)

        if len(matches) >= 2 and abs(matches[0][0] - matches[1][0]) < 1e-12:
            review.append({
                "frame_index": index,
                "reason": "AMBIGUOUS_DEAL_MATCH",
                "candidate_deal_ids": [matches[0][1].deal_id, matches[1][1].deal_id],
                "frame_file": record.get("frame_file"),
            })
            continue

        if matches:
            track = matches[0][1]
            board_key = _explicit_board_key(record)
            if board_key is not None and track.explicit_board_key is None:
                track.explicit_board_key = board_key
            track.observed_pairs |= pairs
            track.frame_indices.append(index)
            track.frame_identities.add(identity)
            seen_frames[identity] = track.deal_id
            if record.get("frame_file"):
                track.frame_files.append(str(record.get("frame_file")))
            continue

        # A new deal track may be created from strong scoped board identity or
        # enough independent card evidence. Bare board_number and time are not
        # anchors because both can repeat.
        board_key = _explicit_board_key(record)
        if board_key is None and len(pairs) < min_shared_cards:
            review.append({"frame_index": index, "reason": "INSUFFICIENT_ANCHOR_EVIDENCE", "frame_file": record.get("frame_file")})
            continue
        deal_id = "deal-" + sha256(identity.encode("utf-8")).hexdigest()[:16]
        track = DealTrack(deal_id=deal_id, anchor_identity=identity, explicit_board_key=board_key)
        track.observed_pairs |= pairs
        track.frame_indices.append(index)
        track.frame_identities.add(identity)
        seen_frames[identity] = deal_id
        if record.get("frame_file"):
            track.frame_files.append(str(record.get("frame_file")))
        tracks.append(track)

    return ReconstructionResult(tuple(tracks), tuple(review))


__all__ = [
    "FULL_DEAL_VALIDATION_VERSION",
    "MULTIFRAME_VERSION",
    "DealTrack",
    "MultiFrameError",
    "ReconstructionResult",
    "reconstruct_deals",
    "validate_full_deal",
]
