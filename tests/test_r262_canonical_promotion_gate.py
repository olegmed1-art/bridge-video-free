import json
from pathlib import Path


def test_r262_canonical_promotion_gate_stays_fail_closed():
    gate = json.loads(
        Path("ops/r262-canonical-promotion-gate.json").read_text(encoding="utf-8")
    )
    prod = json.loads(
        Path("ops/r262-production-promotion.json").read_text(encoding="utf-8")
    )

    assert gate["revision"] == "3.1-free-r26.2"
    assert gate["production_status"] == "ACTIVE"
    assert gate["canonical_promotion_state"] == "BLOCKED_HOLDOUT"
    assert gate["current_evidence"]["independent_frozen_holdout_present"] is False
    assert gate["current_evidence"]["cross_source_accuracy_measured"] is False

    boundary = gate["authority_boundary"]
    assert boundary["canonical_promotion_allowed"] is False
    assert boundary["school_canon_write_allowed"] is False
    assert boundary["world_write_allowed"] is False
    assert boundary["derive_fourth_hand"] is False
    assert boundary["deck_complement"] is False
    assert boundary["hidden_hand_inference"] is False
    assert boundary["unknown_hidden_cards_remain_unknown"] is True

    holdout = gate["required_holdout"]
    assert holdout["minimum_cases"] >= 24
    assert holdout["minimum_independent_source_sessions"] >= 4
    assert holdout["gold_must_be_frozen_before_recognizer_output"] is True
    assert holdout["holdout_threshold_tuning_allowed"] is False
    assert holdout["frame_and_decoded_pixel_hash_overlap_with_development_allowed"] is False

    acceptance = gate["acceptance"]
    assert acceptance["card_seat_precision_min"] >= 0.995
    assert acceptance["visible_target_recall_min"] >= 0.95
    assert acceptance["seat_errors_max"] == 0
    assert acceptance["false_complete_deals_max"] == 0

    assert prod["promotion_scope"] == "PRODUCTION_RUNTIME"
    assert prod["promotion_boundary"]["canonical_promotion_allowed"] is False
