"""Evidence-preserving PBN export for stable Video 3.1 FREE.

Partial observations are written only to explicit ``X-Observed-*`` tags.
The standard ``Deal`` tag is emitted only for 52 directly observed or
human-verified unique cards.  A 39-to-13 complement is shown separately as a
derived hand and never masquerades as a fully observed deal.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bridge_contracts.video_auction import validate_auction_prefix
from bridge_contracts.video_deal import SEATS, canonicalize_video_deal

SCHEMA = "bridge-3.1-free-deal-pbn/v1"
RANK_ORDER = "AKQJT98765432"
SUIT_ORDER = "SHDC"


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
        if not isinstance(card, str) or len(card) != 2 or card[1] not in by_suit:
            raise DealPbnError("PBN hand contains an invalid card")
        by_suit[card[1]].append(card[0])
    return ".".join(
        "".join(rank for rank in RANK_ORDER if rank in by_suit[suit]) or "-"
        for suit in SUIT_ORDER
    )


def _auction_lines(auction: Mapping[str, Any] | None) -> tuple[list[str], bool]:
    if not auction:
        return [_tag("X-AuctionStatus", "UNAVAILABLE")], False
    status = str(auction.get("status") or "REVIEW").upper()
    dealer = str(auction.get("dealer") or "").upper()
    calls = auction.get("calls") or []
    try:
        legality = validate_auction_prefix(calls, dealer=dealer) if calls else None
    except Exception as exc:
        raise DealPbnError("auction violates the mechanics contract") from exc
    if status == "COMPLETE_CONFIRMED":
        if legality is None or not legality["terminated"] or auction.get("accepted_as_standard_pbn") is not True:
            raise DealPbnError("confirmed auction is not complete and evidence-approved")
        display = ["Pass" if call == "PASS" else call for call in legality["normalized_calls"]]
        lines = [_tag("Auction", legality["dealer"])]
        lines.extend(" ".join(display[index:index + 4]) for index in range(0, len(display), 4))
        return lines, True
    lines = [_tag("X-AuctionStatus", status)]
    if legality is not None:
        lines.append(_tag("X-AuctionDealer", legality["dealer"]))
        lines.append(_tag("X-AuctionCalls", " ".join(legality["normalized_calls"])))
    return lines, False


def render_deals_pbn(
    deals: Sequence[Mapping[str, Any]],
    *,
    source_name: str,
    algorithm_revision: str,
) -> tuple[str, dict[str, Any]]:
    if isinstance(deals, (str, bytes)) or not isinstance(deals, Sequence):
        raise DealPbnError("deals must be an array")
    blocks: list[str] = []
    standard_deals = derived_deals = confirmed_auctions = observed_cards = 0
    for ordinal, deal in enumerate(deals, start=1):
        if not isinstance(deal, Mapping):
            raise DealPbnError("deal must be an object")
        evidence = deal.get("deal_evidence") or {}
        if not isinstance(evidence, Mapping):
            raise DealPbnError("deal evidence must be an object")
        if evidence:
            if (
                evidence.get("result_scope") != "SHADOW_ONLY"
                or evidence.get("canonical_promotion_allowed") is not False
                or evidence.get("production_activation_allowed") is not False
            ):
                raise DealPbnError("deal evidence is outside SHADOW_ONLY boundaries")
        raw_hands = deal.get("hands") or {}
        if not isinstance(raw_hands, Mapping):
            raise DealPbnError("deal hands must be an object")
        hands = {seat: _cards(raw_hands.get(seat), seat) for seat in SEATS}
        try:
            observed = canonicalize_video_deal({"hands": hands}).to_dict()
            reconstructed = canonicalize_video_deal({"hands": hands}, derive_fourth_hand=True).to_dict()
        except Exception as exc:
            raise DealPbnError("deal violates the canonical card contract") from exc
        count = sum(len(observed["hands"][seat]["cards"]) for seat in SEATS)
        observed_cards += count
        board = deal.get("board_number") or ordinal
        lines = [
            _tag("Event", "Bridge Video 3.1 FREE evidence review"),
            _tag("Site", "Video"),
            _tag("Board", board),
            _tag("X-DealId", deal.get("deal_id") or f"deal-{ordinal}"),
            _tag("X-Source", source_name),
            _tag("X-AlgorithmRevision", algorithm_revision),
            _tag("X-ResultScope", "SHADOW_ONLY"),
            _tag("X-CanonicalPromotionAllowed", "false"),
            _tag("X-ObservedCardCount", count),
        ]
        for seat in SEATS:
            cards = observed["hands"][seat]["cards"]
            lines.append(_tag(f"X-Observed-{seat}", _hand(cards)))
            lines.append(_tag(f"X-Unknown-{seat}", observed["hands"][seat]["unknown_count"]))

        complete_observed = count == 52 and evidence.get("complete_without_derivation") is True
        if complete_observed:
            deal_text = "N:" + " ".join(_hand(observed["hands"][seat]["cards"]) for seat in SEATS)
            lines.append(_tag("Deal", deal_text))
            lines.append(_tag("X-DealProvenance", "OBSERVED_COMPLETE"))
            standard_deals += 1
        elif reconstructed.get("derivations"):
            derivation = reconstructed["derivations"][0]
            seat = str(derivation["seat"])
            lines.append(_tag(f"X-Derived-{seat}", _hand(reconstructed["hands"][seat]["cards"])))
            lines.append(_tag("X-Derivation", "39_TO_13_DECK_SUBTRACTION"))
            derived_deals += 1
        else:
            lines.append(_tag("X-DealStatus", "PARTIAL_OBSERVATION" if count else "UNAVAILABLE"))

        verification = deal.get("verification") or {}
        if isinstance(verification, Mapping) and verification.get("status") == "HUMAN_VERIFIED":
            lines.append(_tag("X-VerifiedSeats", ",".join(verification.get("verified_seats") or [])))
            lines.append(_tag("X-VerificationFrameSHA256", verification.get("reference_frame_sha256") or ""))
        auction_lines, auction_confirmed = _auction_lines(deal.get("auction"))
        lines.extend(auction_lines)
        confirmed_auctions += int(auction_confirmed)
        blocks.append("\n".join(lines))

    text = ("\n\n".join(blocks) + "\n") if blocks else (
        _tag("Event", "Bridge Video 3.1 FREE evidence review") + "\n"
        + _tag("X-Source", source_name) + "\n"
        + _tag("X-AlgorithmRevision", algorithm_revision) + "\n"
        + _tag("X-ResultScope", "SHADOW_ONLY") + "\n"
        + _tag("X-DealStatus", "UNAVAILABLE") + "\n"
    )
    return text, {
        "schema": SCHEMA,
        "result_scope": "SHADOW_ONLY",
        "deal_count": len(deals),
        "observed_card_count": observed_cards,
        "standard_deal_count": standard_deals,
        "derived_deal_count": derived_deals,
        "confirmed_auction_count": confirmed_auctions,
        "canonical_promotion_performed": False,
        "production_activation_performed": False,
    }


__all__ = ["DealPbnError", "SCHEMA", "render_deals_pbn"]
