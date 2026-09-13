"""Evidence-preserving PBN that never reconstructs hidden/fourth hands."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from bridge_contracts.video_auction import validate_auction_prefix
from bridge_contracts.video_deal import SEATS, canonicalize_video_deal

SCHEMA = "bridge-3.1-free-evidence-pbn/v2"
RANK_ORDER = "AKQJT98765432"
SUIT_ORDER = "SHDC"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DealPbnError(ValueError):
    pass


def _tag(name: str, value: Any) -> str:
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ")
    return f'[{name} "{text}"]'


def _cards(value: Any, seat: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        value = value.get("cards") or []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise DealPbnError(f"hand {seat} must be an array")
    return list(value)


def _hand(cards: Sequence[str]) -> str:
    by_suit = {suit: [] for suit in SUIT_ORDER}
    for card in cards:
        if not isinstance(card, str) or len(card) != 2 or card[0] not in RANK_ORDER or card[1] not in by_suit:
            raise DealPbnError("PBN hand contains an invalid card")
        by_suit[card[1]].append(card[0])
    return ".".join("".join(rank for rank in RANK_ORDER if rank in by_suit[suit]) or "-" for suit in SUIT_ORDER)


def _identity_key(raw: Any) -> tuple[str, str, int, str]:
    if not isinstance(raw, Mapping) or raw.get("kind") != "SOURCE_BOUND_BOARD_INSTANCE":
        raise DealPbnError("source-bound deal identity is required")
    if isinstance(raw.get("board_number"), bool):
        raise DealPbnError("invalid deal identity")
    try:
        key = (
            str(raw.get("scope") or ""), str(raw.get("instance_id") or ""),
            int(raw.get("board_number")), str(raw.get("anchor_frame_sha256") or ""),
        )
    except (TypeError, ValueError) as exc:
        raise DealPbnError("invalid deal identity") from exc
    if not key[0] or not key[1] or key[2] < 1 or not _SHA256.fullmatch(key[3]):
        raise DealPbnError("invalid deal identity")
    return key


def _observed_complete(deal: Mapping[str, Any], observed: Mapping[str, Any]) -> bool:
    pairs = {(seat, card) for seat in SEATS for card in observed["hands"][seat]["cards"]}
    if len(pairs) != 52:
        return False
    verification = deal.get("verification")
    if isinstance(verification, Mapping) and verification.get("status") == "HUMAN_VERIFIED":
        seats = {str(seat).upper() for seat in verification.get("verified_seats") or []}
        frame_sha = str(verification.get("reference_frame_sha256") or "")
        if seats == set(SEATS) and _SHA256.fullmatch(frame_sha):
            return True
    raw = deal.get("card_observations")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return False
    proven: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, Mapping) or item.get("evidence_class") != "OBSERVED_VISUAL":
            return False
        pair = (str(item.get("seat") or "").upper(), str(item.get("card") or "").upper())
        frames = item.get("frame_sha256s")
        channels = item.get("channels")
        if pair not in pairs or pair in proven:
            return False
        if not isinstance(frames, Sequence) or isinstance(frames, (str, bytes)) or len(set(frames)) < 2:
            return False
        if any(not _SHA256.fullmatch(str(frame)) for frame in frames):
            return False
        if not isinstance(channels, Mapping) or set(channels) != {"rank", "suit", "full_card"}:
            return False
        if len({str(value) for value in channels.values()}) != 3:
            return False
        proven.add(pair)
    return proven == pairs


def _auction_lines(
    auction: Any, *, identity_key: tuple[str, str, int, str], source_id: str,
) -> tuple[list[str], bool]:
    if not isinstance(auction, Mapping):
        return [_tag("X-AuctionStatus", "UNAVAILABLE")], False
    status = str(auction.get("status") or "REVIEW").upper()
    calls = auction.get("calls") or []
    dealer = str(auction.get("dealer") or "").upper()
    try:
        legality = validate_auction_prefix(calls, dealer=dealer) if calls else None
        auction_identity = _identity_key(auction.get("deal_identity"))
    except Exception as exc:
        raise DealPbnError("auction violates its evidence/mechanics contract") from exc
    if auction_identity != identity_key:
        raise DealPbnError("auction belongs to a different deal instance")
    if auction.get("source_id") != source_id:
        raise DealPbnError("auction belongs to a different video source")
    if status == "COMPLETE_CONFIRMED":
        if legality is None or not legality["terminated"] or auction.get("accepted_as_standard_pbn") is not True:
            raise DealPbnError("confirmed auction is not complete and evidence-approved")
        calls_text = ["Pass" if call == "PASS" else call for call in legality["normalized_calls"]]
        return [_tag("Auction", legality["dealer"]), *(" ".join(calls_text[i:i + 4]) for i in range(0, len(calls_text), 4))], True
    lines = [_tag("X-AuctionStatus", status)]
    if legality is not None:
        lines += [_tag("X-AuctionDealer", legality["dealer"]), _tag("X-AuctionCalls", " ".join(legality["normalized_calls"]))]
    return lines, False


def render_deals_pbn(
    deals: Sequence[Mapping[str, Any]], *, source_name: str, algorithm_revision: str,
) -> tuple[str, dict[str, Any]]:
    if isinstance(deals, (str, bytes)) or not isinstance(deals, Sequence):
        raise DealPbnError("deals must be an array")
    if not str(source_name).strip() or not str(algorithm_revision).strip():
        raise DealPbnError("source name and algorithm revision are required")
    blocks: list[str] = []
    standard_deals = confirmed_auctions = observed_cards = 0
    for ordinal, deal in enumerate(deals, start=1):
        if not isinstance(deal, Mapping):
            raise DealPbnError("deal must be an object")
        identity = _identity_key(deal.get("deal_identity"))
        source_id = str(deal.get("source_id") or "")
        if not source_id:
            raise DealPbnError("source-bound deal source is required")
        board_context = deal.get("board_context")
        if not isinstance(board_context, Mapping) or board_context.get("status") != "CONFIRMED":
            raise DealPbnError("confirmed source-bound board context is required")
        if int(board_context.get("board_number")) != identity[2]:
            raise DealPbnError("board context disagrees with deal identity")
        if board_context.get("source_id") != source_id:
            raise DealPbnError("board context belongs to a different video source")
        dealer = str(board_context.get("dealer") or "").upper()
        vulnerability = str(board_context.get("vulnerability") or "").upper()
        if dealer not in SEATS or vulnerability not in {"NONE", "NS", "EW", "BOTH"}:
            raise DealPbnError("invalid dealer or vulnerability")
        raw_hands = deal.get("hands") or {}
        if not isinstance(raw_hands, Mapping):
            raise DealPbnError("deal hands must be an object")
        hands = {seat: _cards(raw_hands.get(seat), seat) for seat in SEATS}
        try:
            observed = canonicalize_video_deal({"hands": hands}).to_dict()
        except Exception as exc:
            raise DealPbnError("deal violates the canonical card contract") from exc
        count = sum(len(observed["hands"][seat]["cards"]) for seat in SEATS)
        observed_cards += count
        complete_observed = _observed_complete(deal, observed)
        evidence = deal.get("deal_evidence") or {}
        if not isinstance(evidence, Mapping):
            raise DealPbnError("deal evidence must be an object")
        if evidence and (
            evidence.get("result_scope") != "SHADOW_ONLY"
            or evidence.get("production_activation_allowed") is not False
            or evidence.get("canonical_promotion_allowed") is not False
        ):
            raise DealPbnError("deal evidence is outside SHADOW_ONLY boundaries")
        if isinstance(evidence, Mapping) and evidence.get("complete_without_derivation") is True and not complete_observed:
            raise DealPbnError("complete deal claim lacks direct visual or human proof")
        lines = [
            _tag("Event", "Bridge Video 3.1 FREE evidence review"), _tag("Site", "Video"),
            _tag("Board", identity[2]), _tag("Dealer", dealer),
            _tag("Vulnerable", {"NONE": "None", "BOTH": "All"}.get(vulnerability, vulnerability)),
            _tag("X-DealInstance", identity[1]), _tag("X-DealAnchorFrameSHA256", identity[3]),
            _tag("X-Source", source_name), _tag("X-AlgorithmRevision", algorithm_revision),
            _tag("X-ResultScope", "SHADOW_ONLY"), _tag("X-ProductionActivationAllowed", "false"),
            _tag("X-ObservedCardCount", count),
        ]
        for seat in SEATS:
            cards = observed["hands"][seat]["cards"]
            lines += [_tag(f"X-Observed-{seat}", _hand(cards)), _tag(f"X-Unknown-{seat}", observed["hands"][seat]["unknown_count"])]
        if complete_observed:
            lines += [
                _tag("Deal", "N:" + " ".join(_hand(observed["hands"][seat]["cards"]) for seat in SEATS)),
                _tag("X-DealProvenance", "OBSERVED_OR_HUMAN_VERIFIED_COMPLETE"),
            ]
            standard_deals += 1
        else:
            lines.append(_tag("X-DealStatus", "PARTIAL_OBSERVATION" if count else "UNAVAILABLE"))
        auction_lines, confirmed = _auction_lines(
            deal.get("auction"), identity_key=identity, source_id=source_id,
        )
        lines.extend(auction_lines)
        confirmed_auctions += int(confirmed)
        blocks.append("\n".join(lines))
    text = "\n\n".join(blocks) + ("\n" if blocks else "")
    return text, {
        "schema": SCHEMA, "result_scope": "SHADOW_ONLY", "deal_count": len(deals),
        "observed_card_count": observed_cards, "standard_deal_count": standard_deals,
        "derived_deal_count": 0, "confirmed_auction_count": confirmed_auctions,
        "hidden_or_fourth_hand_reconstruction_performed": False,
        "canonical_promotion_performed": False, "production_activation_performed": False,
    }


__all__ = ["DealPbnError", "SCHEMA", "render_deals_pbn"]
