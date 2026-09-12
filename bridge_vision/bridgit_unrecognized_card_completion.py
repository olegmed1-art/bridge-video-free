"""Post-visual reconstruction of unknown card slots.

Direct visual anchors are frozen first.  Weak slots retain all pixel-supported
alternatives and are solved afterwards under deck and hand-capacity
constraints.  Bridge strategy and auction meaning are deliberately absent.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

RANKS = tuple("AKQJT98765432")
SUITS = tuple("HCDS")
SEATS = tuple("NESW")
FULL_DECK = tuple(rank + suit for suit in SUITS for rank in RANKS)
RED_SUITS = frozenset({"H", "D"})
BLACK_SUITS = frozenset({"C", "S"})
VERSION = "bridgit-post-visual-card-completion-v2"


class CardCompletionError(ValueError):
    """The retained visual candidate graph cannot produce a valid deal."""


def _score(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CardCompletionError("candidate score must be numeric") from exc
    if not math.isfinite(result) or not -1.0 <= result <= 1.0:
        raise CardCompletionError("candidate score is outside [-1,1]")
    return result


def _positive_fusion(visual: float, bonuses: Sequence[float]) -> float:
    residual = 1.0 - max(0.0, min(1.0, visual))
    for bonus in bonuses:
        residual *= 1.0 - max(0.0, min(0.95, float(bonus)))
    return round(1.0 - residual, 6)


def _top(values: Mapping[str, float]) -> tuple[str, float, float]:
    ordered = sorted(values.items(), key=lambda item: (-item[1], item[0]))
    first = ordered[0]
    second = ordered[1][1] if len(ordered) > 1 else -1.0
    return first[0], first[1], first[1] - second


def _normalize_slots(raw_slots: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if len(raw_slots) != 52:
        raise CardCompletionError("completion requires exactly 52 physical slots")
    slots: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, raw in enumerate(raw_slots):
        slot_id = str(raw.get("slot_id") or f"slot-{index:02d}")
        seat = str(raw.get("seat") or "").upper()
        if slot_id in ids or seat not in SEATS:
            raise CardCompletionError("slot id or seat is invalid")
        ids.add(slot_id)
        candidates = {str(item.get("card") or "").upper(): _score(item.get("score")) for item in raw.get("candidates") or []}
        if not candidates or any(card not in FULL_DECK for card in candidates):
            raise CardCompletionError("slot candidates are invalid")
        raw_geometry_suits = raw.get("geometry_suits")
        geometry_suit_set = (
            {str(suit).upper() for suit in raw_geometry_suits}
            if isinstance(raw_geometry_suits, Sequence) and not isinstance(raw_geometry_suits, (str, bytes))
            else {card[1] for card in candidates}
        )
        if not geometry_suit_set or any(suit not in SUITS for suit in geometry_suit_set):
            raise CardCompletionError("slot geometry suits are invalid")
        geometry_suits = sorted(geometry_suit_set, key=SUITS.index)
        rank_scores = {rank: max((value for card, value in candidates.items() if card[0] == rank), default=-1.0) for rank in RANKS}
        suit_scores = {suit: max((value for card, value in candidates.items() if card[1] == suit), default=-1.0) for suit in SUITS}
        slots.append({
            "slot_id": slot_id,
            "seat": seat,
            "x": int(raw.get("x", -1)),
            "y": int(raw.get("y", -1)),
            "candidates": candidates,
            "geometry_suits": geometry_suits,
            "rank_scores": rank_scores,
            "suit_scores": suit_scores,
            "color_family": str(raw.get("color_family") or "UNKNOWN").upper(),
            "color_calibrated": raw.get("color_calibrated") is True,
        })
    if Counter(slot["seat"] for slot in slots) != Counter({seat: 13 for seat in SEATS}):
        raise CardCompletionError("physical slots must contain 13 positions per hand")
    return slots


def _speech_bonuses(slot: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    bonuses: dict[str, float] = {}
    for event in events:
        if event.get("teacher_role_verified") is not True:
            continue
        card = str(event.get("card") or event.get("claimed_card") or "").upper().replace("10", "T")
        seat = str(event.get("explicit_seat") or event.get("claimed_seat") or "").upper()
        slot_id = event.get("slot_id")
        if card not in FULL_DECK:
            continue
        # Speech needs an explicit hand or slot binding to affect ownership.
        if slot_id != slot["slot_id"] and seat != slot["seat"]:
            continue
        confidence = min(0.90, max(0.0, float(event.get("confidence", event.get("speaker_role_confidence", 0.0)))))
        bonuses[card] = max(bonuses.get(card, 0.0), confidence)
    return bonuses


def _pointer_bonuses(slot: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> tuple[dict[str, float], list[dict[str, Any]]]:
    bonuses: dict[str, float] = {}
    conflicts: list[dict[str, Any]] = []
    for event in events:
        if (
            event.get("teacher_role_verified") is not True
            or event.get("spatial_overlap") is not True
            or not (event.get("speech_claim_id") or event.get("teacher_card_named") is True)
        ):
            continue
        card = str(event.get("claimed_card") or event.get("card") or "").upper().replace("10", "T")
        if card not in FULL_DECK:
            continue
        claimed_slot = event.get("slot_id")
        point = event.get("point")
        overlaps = claimed_slot == slot["slot_id"]
        if isinstance(point, Mapping):
            px, py = point.get("x"), point.get("y")
            overlaps = isinstance(px, (int, float)) and isinstance(py, (int, float)) and slot["x"] <= px <= slot["x"] + 19 and slot["y"] <= py <= slot["y"] + 40
        if not overlaps:
            continue
        claimed_seat = str(event.get("claimed_seat") or event.get("seat") or "").upper()
        if claimed_seat and claimed_seat != slot["seat"]:
            # A contradictory hand label cannot corroborate this slot, but it
            # also carries no negative correction.
            continue
        confidence = max(0.0, min(0.90, float(event.get("confidence", 0.0))))
        bonuses[card] = max(bonuses.get(card, 0.0), confidence)
    return bonuses, conflicts


def reconstruct_unknown_cards(
    raw_slots: Sequence[Mapping[str, Any]],
    *,
    visual_threshold: float = 0.80,
    visual_margin: float = 0.06,
    rank_threshold: float = 0.80,
    rank_margin: float = 0.06,
    teacher_pointer_events: Sequence[Mapping[str, Any]] = (),
    teacher_speech_events: Sequence[Mapping[str, Any]] = (),
    played_card_memory: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Freeze direct visual anchors, then solve only the remaining unknowns."""
    from scipy.optimize import linear_sum_assignment  # independent optimizer

    if any(not 0.0 <= float(value) <= 1.0 for value in (visual_threshold, visual_margin, rank_threshold, rank_margin)):
        raise CardCompletionError("thresholds and margins must be inside [0,1]")
    slots = _normalize_slots(raw_slots)
    diagnostics: list[dict[str, Any]] = []
    top_cards: list[tuple[str, float, float]] = [_top(slot["candidates"]) for slot in slots]
    proposed = [index for index, (_, score, margin) in enumerate(top_cards) if score >= visual_threshold and margin >= visual_margin]
    by_card: dict[str, list[int]] = {}
    for index in proposed:
        by_card.setdefault(top_cards[index][0], []).append(index)
    full_anchors: dict[int, str] = {}
    for card, indices in by_card.items():
        winner = max(indices, key=lambda index: (top_cards[index][2], top_cards[index][1], -index))
        full_anchors[winner] = card
        for index in indices:
            if index != winner:
                diagnostics.append({"kind": "DUPLICATE_VISUAL_ANCHOR_DEMOTED_TO_UNKNOWN", "slot_id": slots[index]["slot_id"], "card": card})

    rank_hints: dict[int, str] = {}
    for index, slot in enumerate(slots):
        if index in full_anchors:
            continue
        rank, score, margin = _top(slot["rank_scores"])
        if score >= rank_threshold and margin >= rank_margin:
            rank_hints[index] = rank

    anchored_cards = set(full_anchors.values())
    size = len(slots)

    order_hints: dict[int, set[str]] = {}
    order_evidence: dict[int, dict[str, Any]] = {}
    for seat in SEATS:
        for suit in SUITS:
            group = sorted(
                (index for index, slot in enumerate(slots) if slot["seat"] == seat and slot["geometry_suits"] == [suit]),
                key=lambda index: (slots[index]["x"], slots[index]["y"], slots[index]["slot_id"]),
            )
            for position, index in enumerate(group):
                if index in full_anchors:
                    continue
                left = next((candidate for candidate in reversed(group[:position]) if candidate in full_anchors and full_anchors[candidate][1] == suit), None)
                right = next((candidate for candidate in group[position + 1:] if candidate in full_anchors and full_anchors[candidate][1] == suit), None)
                lower = RANKS.index(full_anchors[left][0]) + 1 if left is not None else 0
                upper = RANKS.index(full_anchors[right][0]) if right is not None else len(RANKS)
                allowed = set(RANKS[lower:upper])
                if allowed and (left is not None or right is not None):
                    order_hints[index] = allowed
                    order_evidence[index] = {
                        "left_card": full_anchors[left] if left is not None else None,
                        "right_card": full_anchors[right] if right is not None else None,
                        "allowed_ranks": [rank for rank in RANKS if rank in allowed],
                    }

    def solve(active_rank_hints: Mapping[int, str], active_order_hints: Mapping[int, set[str]]):
        costs = [[1_000_000.0] * size for _ in range(size)]
        bonus_maps: list[dict[str, float]] = []
        for slot in slots:
            speech = _speech_bonuses(slot, teacher_speech_events)
            pointer, _ = _pointer_bonuses(slot, teacher_pointer_events)
            memory: dict[str, float] = {}
            for event in played_card_memory:
                if not (event.get("evidence_verified") is True or event.get("recognition_status") == "VISUAL_CARD_CANDIDATE"):
                    continue
                card = str(event.get("card") or "").upper().replace("10", "T")
                seat = str(event.get("seat") or "").upper()
                if card in FULL_DECK and seat == slot["seat"]:
                    memory[card] = max(memory.get(card, 0.0), min(0.80, max(0.0, float(event.get("confidence", event.get("minimum_visual_confidence", 0.0))))))
            bonus_maps.append({
                card: _positive_fusion(0.0, [speech.get(card, 0.0), pointer.get(card, 0.0), memory.get(card, 0.0)])
                for card in set(speech) | set(pointer) | set(memory)
            })
        for slot_index, slot in enumerate(slots):
            for card_index, card in enumerate(FULL_DECK):
                if card not in slot["candidates"]:
                    continue
                if slot_index in full_anchors and full_anchors[slot_index] != card:
                    continue
                if slot_index not in full_anchors and card in anchored_cards:
                    continue
                if slot_index in active_rank_hints and card[0] != active_rank_hints[slot_index]:
                    continue
                if slot_index in active_order_hints and card[0] not in active_order_hints[slot_index]:
                    continue
                if card[1] not in slot["geometry_suits"]:
                    continue
                if slot["color_calibrated"] and slot["color_family"] in {"RED", "BLACK"}:
                    allowed_suits = RED_SUITS if slot["color_family"] == "RED" else BLACK_SUITS
                    if card[1] not in allowed_suits:
                        continue
                visual = slot["candidates"][card]
                costs[slot_index][card_index] = -visual - 0.35 * bonus_maps[slot_index].get(card, 0.0)
        rows, columns = linear_sum_assignment(costs)
        if len(rows) != size or any(costs[row][column] >= 999_999.0 for row, column in zip(rows, columns)):
            return None, bonus_maps
        return {int(row): FULL_DECK[int(column)] for row, column in zip(rows, columns)}, bonus_maps

    active_rank_hints = dict(rank_hints)
    active_order_hints = dict(order_hints)
    assignment, bonus_maps = solve(active_rank_hints, active_order_hints)
    while assignment is None and active_order_hints:
        weakest = max(active_order_hints, key=lambda index: (len(active_order_hints[index]), index))
        diagnostics.append({"kind": "ORDER_INTERVAL_RELAXED_FOR_GLOBAL_CONSISTENCY", "slot_id": slots[weakest]["slot_id"], "evidence": order_evidence[weakest]})
        del active_order_hints[weakest]
        assignment, bonus_maps = solve(active_rank_hints, active_order_hints)
    while assignment is None and active_rank_hints:
        weakest = min(active_rank_hints, key=lambda index: (_top(slots[index]["rank_scores"])[2], _top(slots[index]["rank_scores"])[1], index))
        diagnostics.append({"kind": "RANK_HINT_RELAXED_FOR_GLOBAL_CONSISTENCY", "slot_id": slots[weakest]["slot_id"], "rank": active_rank_hints[weakest]})
        del active_rank_hints[weakest]
        assignment, bonus_maps = solve(active_rank_hints, active_order_hints)
    if assignment is None:
        raise CardCompletionError("candidate graph has no complete 52-card assignment")

    if set(assignment.values()) != set(FULL_DECK):
        raise CardCompletionError("optimizer did not produce a deck bijection")

    def locally_allowed(slot_index: int, card: str) -> bool:
        slot = slots[slot_index]
        if card not in slot["candidates"]:
            return False
        if slot_index in full_anchors:
            return full_anchors[slot_index] == card
        if card in anchored_cards:
            return False
        if slot_index in active_rank_hints and card[0] != active_rank_hints[slot_index]:
            return False
        if slot_index in active_order_hints and card[0] not in active_order_hints[slot_index]:
            return False
        if card[1] not in slot["geometry_suits"]:
            return False
        if slot["color_calibrated"] and slot["color_family"] in {"RED", "BLACK"}:
            allowed_suits = RED_SUITS if slot["color_family"] == "RED" else BLACK_SUITS
            return card[1] in allowed_suits
        return True

    local_options = {
        index: [card for card in FULL_DECK if locally_allowed(index, card)]
        for index in range(len(slots))
    }
    possible_slots_by_card = {
        card: [index for index in range(len(slots)) if locally_allowed(index, card)]
        for card in FULL_DECK
    }
    conflicts: list[dict[str, Any]] = []
    for event in teacher_pointer_events:
        if (
            event.get("teacher_role_verified") is not True
            or event.get("spatial_overlap") is not True
            or not (event.get("speech_claim_id") or event.get("teacher_card_named") is True)
        ):
            continue
        claimed_card = str(event.get("claimed_card") or event.get("card") or "").upper().replace("10", "T")
        claimed_seat = str(event.get("claimed_seat") or event.get("seat") or "").upper()
        for index, slot in enumerate(slots):
            point = event.get("point")
            overlaps = event.get("slot_id") == slot["slot_id"]
            if isinstance(point, Mapping):
                px, py = point.get("x"), point.get("y")
                overlaps = isinstance(px, (int, float)) and isinstance(py, (int, float)) and slot["x"] <= px <= slot["x"] + 19 and slot["y"] <= py <= slot["y"] + 40
            if overlaps and (claimed_card != assignment[index] or (claimed_seat and claimed_seat != slot["seat"])):
                conflicts.append({"kind": "CURSOR_CONFLICT", "slot_id": slot["slot_id"], "assigned_card": assignment[index], "assigned_seat": slot["seat"], "claimed_card": claimed_card, "claimed_seat": claimed_seat, "weight_penalty": 0.0})
    owner_by_card = {card: slots[index]["seat"] for index, card in assignment.items()}
    for event in played_card_memory:
        if not (event.get("evidence_verified") is True or event.get("recognition_status") == "VISUAL_CARD_CANDIDATE"):
            continue
        card = str(event.get("card") or "").upper().replace("10", "T")
        seat = str(event.get("seat") or "").upper()
        if card in owner_by_card and seat and owner_by_card[card] != seat:
            conflicts.append({"kind": "PLAY_MEMORY_CONFLICT", "card": card, "assigned_seat": owner_by_card[card], "remembered_seat": seat, "weight_penalty": 0.0})
    anchor_cards = set(full_anchors.values())
    records = []
    for index, slot in enumerate(slots):
        card = assignment[index]
        visual = slot["candidates"][card]
        bonuses = []
        sources = []
        pointer_bonus = 0.0
        play_bonus = 0.0
        speech = _speech_bonuses(slot, teacher_speech_events)
        speech_bonus = speech.get(card, 0.0)
        if speech_bonus:
            bonuses.append(speech_bonus)
            sources.append("TEACHER_SPEECH")
        pointer, _ = _pointer_bonuses(slot, teacher_pointer_events)
        if card in pointer:
            pointer_bonus = pointer[card]
            bonuses.append(pointer_bonus)
            sources.append("TEACHER_POINTER")
        for event in played_card_memory:
            remembered = str(event.get("card") or "").upper().replace("10", "T")
            if remembered == card and str(event.get("seat") or "").upper() == slot["seat"] and (event.get("evidence_verified") is True or event.get("recognition_status") == "VISUAL_CARD_CANDIDATE"):
                play_bonus = max(play_bonus, min(0.80, max(0.0, float(event.get("confidence", event.get("minimum_visual_confidence", 0.0))))))
        if play_bonus:
            bonuses.append(play_bonus)
            sources.append("PLAY_MEMORY")
        direct = full_anchors.get(index) == card
        trace = ["VISUAL_FULL_CARD", "GEOMETRY"] if direct else ["GEOMETRY", "DECK_BIJECTION", "HAND_CAPACITY_13"]
        if not direct and active_rank_hints.get(index) == card[0]:
            trace.append("VISUAL_RANK_ONLY")
        if not direct and index in active_order_hints and card[0] in active_order_hints[index]:
            trace.append("RANK_ORDER_INTERVAL")
            if order_evidence[index].get("left_card") and order_evidence[index].get("right_card"):
                trace.append(f"BETWEEN_{order_evidence[index]['left_card'][0]}_AND_{order_evidence[index]['right_card'][0]}")
        if not direct:
            other_suits = {candidate[1] for candidate in anchor_cards if candidate[0] == card[0] and candidate != card}
            if len(other_suits) == 3 and card[1] not in other_suits:
                trace.append("THREE_OTHER_SUITS_USED_THEREFORE_FOURTH_SUIT")
        if len(slot["geometry_suits"]) == 1:
            trace.append("SUIT_GROUP_GEOMETRY")
        if not direct and len(local_options[index]) == 1:
            trace.append("ONLY_LOCALLY_FEASIBLE_CARD")
        if not direct and len(possible_slots_by_card[card]) == 1:
            trace.append("ONLY_REMAINING_SLOT_FOR_CARD")
        if not direct:
            trace.append("GLOBAL_MAXIMUM_VISUAL_SUPPORT")
        trace.extend(sources)
        records.append({
            "slot_id": slot["slot_id"], "seat": slot["seat"], "x": slot["x"], "y": slot["y"],
            "card": card, "visual_recognized": direct, "visual_weight": round(visual, 6),
            "fused_weight": _positive_fusion(visual, bonuses), "speech_bonus": round(speech_bonus, 6),
            "cursor_bonus": round(pointer_bonus, 6),
            "play_memory_bonus": round(play_bonus, 6), "provenance": trace,
            "rank_hint": active_rank_hints.get(index), "visual_top_card": top_cards[index][0],
            "visual_top_score": round(top_cards[index][1], 6), "visual_top_margin": round(top_cards[index][2], 6),
        })
    return {
        "version": VERSION,
        "status": "COMPLETE_52_BY_13",
        "assignments": records,
        "visual_anchor_count": sum(item["visual_recognized"] for item in records),
        "completed_count": sum(not item["visual_recognized"] for item in records),
        "rank_hint_count": len(active_rank_hints),
        "diagnostics": diagnostics,
        "conflicts": conflicts,
        "bridge_strategy_or_auction_logic_used": False,
        "cursor_policy": "POSITIVE_ONLY_NO_MISMATCH_PENALTY",
    }


__all__ = ["CardCompletionError", "FULL_DECK", "VERSION", "reconstruct_unknown_cards"]
