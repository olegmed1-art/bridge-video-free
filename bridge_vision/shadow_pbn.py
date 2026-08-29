"""Deterministic PBN view of accepted SHADOW card observations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bridge_contracts.video_deal import SEATS, canonicalize_video_deal
from bridge_vision.auction_observer import aggregate_auction_observations

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


def _evidence_sources(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    sources: list[Mapping[str, Any]] = []
    for key in ("candidates", "diagnostics"):
        values = record.get(key) or []
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise ShadowPbnError(f"record {key} must be an array")
        sources.extend(item for item in values if isinstance(item, Mapping))
    return sources


def _auction_observations(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    observations: list[Mapping[str, Any]] = []
    for source in _evidence_sources(record):
        evidence = source.get("evidence") or {}
        if not isinstance(evidence, Mapping):
            continue
        observation = evidence.get("auction_observation")
        if observation is not None:
            if not isinstance(observation, Mapping):
                raise ShadowPbnError("auction observation must be an object")
            observations.append(observation)
    return observations


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
    multimodal = record.get("transcript_card_observations") or []
    if not isinstance(multimodal, Sequence) or isinstance(multimodal, (str, bytes)):
        raise ShadowPbnError("transcript card observations must be an array")
    for observation in multimodal:
        if not isinstance(observation, Mapping):
            raise ShadowPbnError("transcript card observation must be an object")
        if observation.get("accepted_as_observation") is not True:
            continue
        if (
            observation.get("canonical_promotion_allowed") is not False
            or observation.get("provenance_class")
            not in {"OBSERVED_MULTIMODAL", "OBSERVED_VISUAL_WITH_SPEECH_CORROBORATION"}
        ):
            raise ShadowPbnError("invalid accepted transcript card provenance")
        seat = str(observation.get("seat") or "").upper()
        if seat not in SEATS:
            raise ShadowPbnError("invalid transcript card seat")
        normalized = canonicalize_video_deal({"hands": {seat: [observation.get("card")]}}).to_dict()
        observed[seat].update(normalized["hands"][seat]["cards"])
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
    for source in _evidence_sources(record):
        evidence = source.get("evidence") or {}
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


def _accumulate_deals(records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    deals: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise ShadowPbnError("PBN input record must be an object")
        if record.get("status") == "CONFLICT":
            continue
        observed = _candidate_observations(record)
        auction_observations = _auction_observations(record)
        if not any(observed.values()) and not any(
            item.get("accepted_as_observation") is True for item in auction_observations
        ):
            continue
        identity, board_number, dealer, vulnerability = _metadata(record)
        state = deals.setdefault(identity, {
            "board_number": board_number,
            "dealer": dealer,
            "vulnerability": vulnerability,
            "hands": {seat: set() for seat in SEATS},
            "frames": set(),
            "representative_frames": [],
            "auction_observations": [],
        })
        if (state["board_number"], state["dealer"], state["vulnerability"]) != (board_number, dealer, vulnerability):
            raise ShadowPbnError("board metadata changed inside one deal identity")
        for seat in SEATS:
            state["hands"][seat].update(observed[seat])
        state["auction_observations"].extend(auction_observations)
        frame_locator = str(record.get("frame_file") or record.get("frame_sha256") or index)
        state["frames"].add(frame_locator)
        accepted_auction_calls = max(
            (
                len(item.get("calls") or [])
                for item in auction_observations
                if item.get("accepted_as_observation") is True
            ),
            default=0,
        )
        try:
            frame_time = float(record.get("time"))
        except (TypeError, ValueError):
            frame_time = float("inf")
        state["representative_frames"].append({
            "frame_file": str(record.get("frame_file") or ""),
            "frame_sha256": str(record.get("frame_sha256") or ""),
            "time": frame_time,
            "observed_card_count": sum(len(cards) for cards in observed.values()),
            "auction_call_count": accepted_auction_calls,
        })
    return deals


def _pbn_call(call: str) -> str:
    return "Pass" if call == "PASS" else call


def _auction_data(calls: Sequence[str]) -> list[str]:
    rendered = [_pbn_call(call) for call in calls]
    return [" ".join(rendered[index : index + 4]) for index in range(0, len(rendered), 4)]


def summarize_shadow_auctions(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    deals = _accumulate_deals(records)
    statuses: dict[str, int] = {}
    accepted_frames = review_frames = rejected_frames = 0
    for record in records:
        for observation in _auction_observations(record):
            if observation.get("accepted_as_observation") is True:
                accepted_frames += 1
            else:
                review_frames += 1
                if observation.get("reason") != "BOARD_AND_COMPASS_NOT_TEMPORALLY_CONFIRMED":
                    rejected_frames += 1
    for state in deals.values():
        auction = aggregate_auction_observations(state["auction_observations"])
        if auction["status"] != "UNAVAILABLE":
            statuses[auction["status"]] = statuses.get(auction["status"], 0) + 1
    return {
        "frame_observations_accepted": accepted_frames,
        "frame_observations_review": review_frames,
        "frame_observations_rejected": rejected_frames,
        "deal_statuses": statuses,
        "standard_pbn_auctions": statuses.get("COMPLETE_CONFIRMED", 0),
    }


def build_shadow_deal_views(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Build deterministic observed/reconstructed views for human SHADOW review.

    The observed view contains accepted card observations only.  The reconstructed
    view can add a fourth hand solely through the existing exact 39-to-13 deck
    subtraction contract; any other incomplete layout remains visibly incomplete.
    """
    deals = _accumulate_deals(records)
    views: list[dict[str, Any]] = []
    for ordinal, (identity, state) in enumerate(sorted(deals.items()), start=1):
        observed = canonicalize_video_deal({
            "hands": {seat: sorted(state["hands"][seat]) for seat in SEATS},
        }).to_dict()
        reconstructed = canonicalize_video_deal(
            {"hands": {seat: sorted(state["hands"][seat]) for seat in SEATS}},
            derive_fourth_hand=True,
        ).to_dict()
        derivations = reconstructed.get("derivations") or []
        observed_count = sum(len(observed["hands"][seat]["cards"]) for seat in SEATS)
        if derivations:
            reconstruction_status = "DERIVED_39_TO_13"
        elif observed_count == 52:
            reconstruction_status = "OBSERVED_COMPLETE"
        else:
            reconstruction_status = "NOT_DERIVED_INSUFFICIENT_OBSERVATIONS"
        auction = aggregate_auction_observations(state["auction_observations"])
        representative = min(
            state["representative_frames"],
            key=lambda item: (
                -int(item["observed_card_count"]),
                -int(item["auction_call_count"]),
                float(item["time"]),
                str(item["frame_file"]),
            ),
            default={
                "frame_file": "",
                "frame_sha256": "",
                "time": float("inf"),
                "observed_card_count": 0,
                "auction_call_count": 0,
            },
        )
        views.append({
            "identity": identity,
            "board_number": state["board_number"] or ordinal,
            "dealer": state["dealer"],
            "vulnerability": state["vulnerability"],
            "observed": observed,
            "observed_count": observed_count,
            "reconstructed": reconstructed,
            "reconstruction_status": reconstruction_status,
            "auction": auction,
            "source_frames": sorted(state["frames"]),
            "representative_frame": representative,
            "result_scope": "SHADOW_ONLY",
            "canonical_promotion_allowed": False,
        })
    return views


def render_shadow_pbn(records: Sequence[Mapping[str, Any]], *, source: str = "") -> str:
    """Accumulate accepted observed cards and auctions by deal into safe PBN."""
    deals = _accumulate_deals(records)

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
        auction = aggregate_auction_observations(state["auction_observations"])
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
        if auction["status"] != "UNAVAILABLE":
            lines.append(_tag("X-AuctionStatus", auction["status"]))
            if auction.get("calls"):
                lines.append(_tag("X-AuctionCalls", " ".join(_pbn_call(call) for call in auction["calls"])))
                lines.append(_tag(
                    "X-AuctionCallFrameSupport",
                    ",".join(str(value) for value in auction["call_frame_support"]),
                ))
            if auction.get("variants"):
                lines.append(_tag(
                    "X-AuctionVariants",
                    " | ".join(" ".join(_pbn_call(call) for call in variant) for variant in auction["variants"]),
                ))
            if auction.get("accepted_as_standard_pbn") is True:
                lines.append(_tag("Auction", auction["dealer"]))
                lines.extend(_auction_data(auction["calls"]))
        blocks.append("\n".join(lines))
    header = (
        "% PBN 2.1\n"
        "% Generated from accepted SHADOW observations only\n"
        "% X-ResultScope: SHADOW_ONLY\n"
        "% X-CanonicalPromotionAllowed: false\n"
    )
    if not blocks:
        return header + "% No accepted card observations or auction observations\n"
    return header + "\n" + "\n\n".join(blocks) + "\n"


__all__ = [
    "SCHEMA",
    "ShadowPbnError",
    "build_shadow_deal_views",
    "render_shadow_pbn",
    "summarize_shadow_auctions",
]
