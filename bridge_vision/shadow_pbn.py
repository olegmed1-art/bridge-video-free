"""Deterministic PBN view of accepted SHADOW card observations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bridge_contracts.video_deal import SEATS, canonicalize_video_deal

SCHEMA = "bridge-profiled-shadow-pbn/v1"
RANK_ORDER = "AKQJT98765432"
SUIT_ORDER = "SHDC"


class ShadowPbnError(ValueError):
    pass


def _tag(name: str, value: Any) -> str:
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ")
    return f'[{name} "{text}"]'


def _hand(cards: Sequence[str]) -> str:
    by_suit = {suit: [] for suit in SUIT_ORDER}
    for card in cards:
        by_suit[card[1]].append(card[0])
    return ".".join(
        "".join(rank for rank in RANK_ORDER if rank in by_suit[suit]) or "-"
        for suit in SUIT_ORDER
    )


def _candidate_observations(record: Mapping[str, Any]) -> dict[str, set[str]]:
    observed = {seat: set() for seat in SEATS}
    candidates = record.get("candidates") or []
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
        raise ShadowPbnError("record candidates must be an array")
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise ShadowPbnError("candidate must be an object")
        evidence = candidate.get("evidence") or {}
        if not isinstance(evidence, Mapping) or evidence.get("canonical_promotion_allowed") is not False:
            raise ShadowPbnError("candidate is outside the shadow boundary")
        hands = candidate.get("hands") or {}
        if not isinstance(hands, Mapping):
            raise ShadowPbnError("candidate hands must be an object")
        normalized = canonicalize_video_deal({"hands": dict(hands)}).to_dict()["hands"]
        for seat in SEATS:
            observed[seat].update(normalized[seat]["cards"])
    card_seats: dict[str, str] = {}
    for seat in SEATS:
        for card in observed[seat]:
            previous = card_seats.setdefault(card, seat)
            if previous != seat:
                raise ShadowPbnError("cross-seat card conflict in PBN input")
    return observed


def _metadata(record: Mapping[str, Any]) -> tuple[str, int | None, str | None, str | None]:
    identities: set[str] = set()
    metadata: list[Mapping[str, Any]] = []
    for candidate in record.get("candidates") or []:
        evidence = candidate.get("evidence") or {}
        identity = evidence.get("deal_identity") or {}
        if isinstance(identity, Mapping):
            identities.add("|".join(str(identity.get(key) or "") for key in ("kind", "scope", "value")))
        board = evidence.get("board_metadata") or {}
        if isinstance(board, Mapping) and board.get("status") == "CONFIRMED":
            metadata.append(board)
    identities.discard("||")
    if len(identities) > 1:
        raise ShadowPbnError("multiple deal identities in one record")
    if not identities:
        locator = str(record.get("frame_sha256") or record.get("frame_file") or "").strip()
        if not locator:
            raise ShadowPbnError("accepted observations lack deal identity and frame locator")
        identities.add(f"UNSCOPED_FRAME|{locator}")
    if not metadata:
        return next(iter(identities)), None, None, None
    keys = {(int(item["board_number"]), str(item["dealer"]), str(item["vulnerability"])) for item in metadata}
    if len(keys) != 1:
        raise ShadowPbnError("conflicting confirmed board metadata")
    board_number, dealer, vulnerability = keys.pop()
    return next(iter(identities), f"board-{board_number}"), board_number, dealer, vulnerability


def render_shadow_pbn(records: Sequence[Mapping[str, Any]], *, source: str = "") -> str:
    """Accumulate accepted observed cards by deal and render a safe PBN view."""
    deals: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise ShadowPbnError("PBN input record must be an object")
        if record.get("status") == "CONFLICT":
            continue
        observed = _candidate_observations(record)
        if not any(observed.values()):
            continue
        identity, board_number, dealer, vulnerability = _metadata(record)
        state = deals.setdefault(identity, {
            "board_number": board_number,
            "dealer": dealer,
            "vulnerability": vulnerability,
            "hands": {seat: set() for seat in SEATS},
            "frames": set(),
        })
        if (state["board_number"], state["dealer"], state["vulnerability"]) != (board_number, dealer, vulnerability):
            raise ShadowPbnError("board metadata changed inside one deal identity")
        for seat in SEATS:
            state["hands"][seat].update(observed[seat])
        state["frames"].add(str(record.get("frame_file") or record.get("frame_sha256") or index))

    blocks: list[str] = []
    for ordinal, (identity, state) in enumerate(sorted(deals.items()), start=1):
        hands = state["hands"]
        card_seats: dict[str, str] = {}
        for seat in SEATS:
            if len(hands[seat]) > 13:
                raise ShadowPbnError("more than 13 observed cards in one hand")
            for card in hands[seat]:
                previous = card_seats.setdefault(card, seat)
                if previous != seat:
                    raise ShadowPbnError("cross-seat temporal card conflict")
        observed_count = len(card_seats)
        complete = observed_count == 52 and all(len(hands[seat]) == 13 for seat in SEATS)
        lines = [
            _tag("Event", "Video card recognition SHADOW"),
            _tag("Site", source or "Universal Video"),
            _tag("Board", state["board_number"] or ordinal),
            _tag("X-Schema", SCHEMA),
            _tag("X-ResultScope", "SHADOW_ONLY"),
            _tag("X-CanonicalPromotionAllowed", "false"),
            _tag("X-DealIdentity", identity),
            _tag("X-ObservedCount", observed_count),
        ]
        if state["dealer"]:
            lines.insert(3, _tag("Dealer", state["dealer"]))
        if state["vulnerability"]:
            vulnerable = {"NONE": "None", "BOTH": "All"}.get(state["vulnerability"], state["vulnerability"])
            lines.insert(4, _tag("Vulnerable", vulnerable))
        for seat in SEATS:
            lines.append(_tag(f"X-Observed-{seat}", _hand(sorted(hands[seat]))))
            lines.append(_tag(f"X-UnknownCount-{seat}", 13 - len(hands[seat])))
        lines.append(_tag("X-SourceFrames", ",".join(sorted(state["frames"]))))
        if complete:
            lines.append(_tag("Deal", "N:" + " ".join(_hand(sorted(hands[seat])) for seat in SEATS)))
        else:
            lines.append(_tag("X-DealStatus", "PARTIAL_OBSERVED_NO_STANDARD_DEAL_TAG"))
        blocks.append("\n".join(lines))
    header = (
        "% PBN 2.1\n"
        "% Generated from accepted SHADOW observations only\n"
        "% X-ResultScope: SHADOW_ONLY\n"
        "% X-CanonicalPromotionAllowed: false\n"
    )
    if not blocks:
        return header + "% No accepted card observations\n"
    return header + "\n" + "\n\n".join(blocks) + "\n"


__all__ = ["SCHEMA", "ShadowPbnError", "render_shadow_pbn"]
