from __future__ import annotations

"""Corrected runtime facade for DDS learning methodology v2.3.

The canonical PBN parser used by the pilot expected a complete 52-card deal.
Stage-2 continuation tasks necessarily contain partial positions. This facade
installs one strict parser that accepts both forms: it always enforces unique
physical cards and at most 13 cards per hand; when 52 cards are present, it also
enforces exactly 13 per hand.
"""

import copy
from typing import Sequence

import v23_core as core


def parse_position(pbn: str) -> dict[int, list[str]]:
    pbn = pbn.strip()
    if len(pbn) < 3 or pbn[1] != ":":
        raise ValueError(f"Expected seat-prefixed PBN deal, got {pbn!r}")
    start = core._seat_index(pbn[0])
    raw_hands = pbn[2:].split()
    if len(raw_hands) != 4:
        raise ValueError("A DDS position must contain four hand fields")
    hands: dict[int, list[str]] = {}
    seen: set[str] = set()
    counts: list[int] = []
    for offset, raw in enumerate(raw_hands):
        suits = raw.split(".")
        if len(suits) != 4:
            raise ValueError(f"Bad PBN hand: {raw!r}")
        normalized: list[str] = []
        count = 0
        for suit_index, cards in enumerate(suits):
            if any(rank not in core.RANKS for rank in cards):
                raise ValueError(f"Bad rank in PBN hand: {raw!r}")
            if len(set(cards)) != len(cards):
                raise ValueError(f"Repeated rank inside suit: {raw!r}")
            cards = "".join(sorted(cards, key=core.RANKS.index))
            normalized.append(cards)
            count += len(cards)
            for rank in cards:
                card = f"{core.SUITS[suit_index]}{rank}"
                if card in seen:
                    raise ValueError(f"Duplicate physical card: {card}")
                seen.add(card)
        if count > 13:
            raise ValueError(f"A hand cannot contain more than 13 cards, found {count}")
        counts.append(count)
        hands[(start + offset) % 4] = normalized
    total = len(seen)
    if total == 52 and counts != [13, 13, 13, 13]:
        raise ValueError(f"Complete deal must be 13/13/13/13, found {counts}")
    if total > 52:
        raise ValueError(f"Position cannot contain more than 52 cards, found {total}")
    return hands


# All v23_core functions resolve parse_deal through their module globals at call
# time, so this patch applies consistently to legal-line validation, continuation
# construction and information masking.
core.parse_deal = parse_position


def validate_line_bearing_prediction(task: dict, prediction: dict, *, require_line: bool = True) -> dict:
    line = prediction.get("line") or []
    if not isinstance(line, list):
        raise ValueError("prediction.line must be a list of card tokens")
    if require_line and not line:
        raise ValueError("Stage-2 play prediction must contain a pre-DDS legal line")
    first = task.get("next_to_play", task.get("first_to_play", task.get("leader")))
    if first is None:
        raise ValueError("Task must define next_to_play/first_to_play/leader")
    initial_trick = [(x["seat"], x["card"]) for x in task.get("current_trick", [])]
    result = core.validate_play_line(
        task["deal"],
        first_to_play=first,
        strain=task["strain"],
        line=line,
        initial_trick=initial_trick,
    )
    hands = parse_position(task["deal"])
    current = [(core._seat_index(seat), core.normalize_card(card)) for seat, card in initial_trick]
    legal = core.legal_cards(hands, core._seat_index(first), current)
    candidates = [core.normalize_card(card) for card in prediction.get("candidates", [])]
    illegal = sorted(set(candidates) - set(legal))
    if illegal:
        raise ValueError(f"Prediction contains illegal initial candidates: {illegal}")
    probability = prediction.get("confidence_probability")
    if probability is not None and not (0.0 <= float(probability) <= 1.0):
        raise ValueError("confidence_probability must be in [0,1]")
    result["candidate_cards"] = candidates
    result["confidence_probability"] = None if probability is None else float(probability)
    return result


def make_continuation_task(base_task: dict, play_prefix: Sequence[str], *, decision_id: str, information_mode: str = "double_dummy") -> dict:
    # Delegate after installing the partial-position parser, then revalidate the
    # visible-information mask as a fail-closed postcondition.
    task = core.make_continuation_task(
        copy.deepcopy(base_task),
        play_prefix,
        decision_id=decision_id,
        information_mode=information_mode,
    )
    mask = core.audit_information_mask(task)
    if mask["status"] == "error":
        raise ValueError(f"Information mask leaked a hidden hand: {mask}")
    return task


# Re-export the stable public API while overriding the two corrected entrypoints.
ALGORITHM_VERSION = core.ALGORITHM_VERSION
FamilyLineage = core.FamilyLineage
parse_deal = parse_position
render_deal = core.render_deal
normalize_card = core.normalize_card
hand_cards = core.hand_cards
legal_cards = core.legal_cards
trick_winner = core.trick_winner
validate_play_line = core.validate_play_line
family_id_for = core.family_id_for
assign_fold = core.assign_fold
stamp_root_task = core.stamp_root_task
derive_task = core.derive_task
audit_lineage = core.audit_lineage
crossfit_training_families = core.crossfit_training_families
mask_information = core.mask_information
audit_information_mask = core.audit_information_mask
calibration_report = core.calibration_report
fit_histogram_calibrator = core.fit_histogram_calibrator
apply_calibrator = core.apply_calibrator
plan_shards = core.plan_shards
audit_shards = core.audit_shards
optimal_actions = core.optimal_actions
find_counterexamples = core.find_counterexamples
assess_rule_candidate = core.assess_rule_candidate
deterministic_permutation = core.deterministic_permutation
negative_control_report = core.negative_control_report
DEFAULT_STAGE2_CAPABILITIES = core.DEFAULT_STAGE2_CAPABILITIES
audit_stage2_readiness = core.audit_stage2_readiness
write_readiness = core.write_readiness
