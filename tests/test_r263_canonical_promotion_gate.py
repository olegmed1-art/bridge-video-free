import json
from pathlib import Path


def test_r263_canonical_promotion_requires_independent_offline_holdout():
    gate = json.loads(
        Path("ops/r263-canonical-promotion-gate.json").read_text(encoding="utf-8")
    )
    promotion = json.loads(
        Path("ops/r263-production-promotion.json").read_text(encoding="utf-8")
    )

    assert gate["revision"] == promotion["revision"] == "3.1-free-r26.3"
    assert gate["production_status"] == "ACTIVE"
    assert gate["canonical_promotion_state"] == "BLOCKED_HOLDOUT"

    separation = gate["runtime_separation"]
    assert separation["human_labelled_holdout_is_runtime_prerequisite"] is False
    assert separation["human_verification_required"] is False
    assert separation["recognizer_profile"] == "AUTONOMOUS_HASH_GEOMETRY_V1"
    assert separation["offline_human_gold_allowed"] is True

    current = gate["current_evidence"]
    assert current["independent_frozen_holdout_present"] is False
    assert current["cross_source_accuracy_measured"] is False

    required = gate["required_holdout"]
    assert required["minimum_cases"] >= 24
    assert required["minimum_independent_source_sessions"] >= 4
    assert required["complete_visible_deals"] >= 12
    assert required["fail_closed_non_complete_cases"] >= 12
    assert required["gold_must_be_frozen_before_recognizer_output"] is True
    assert required["holdout_threshold_tuning_allowed"] is False
    assert required["development_inputs_must_be_excluded"] is True
    assert required["frame_and_decoded_pixel_hash_overlap_with_development_allowed"] is False

    acceptance = gate["acceptance"]
    assert acceptance["card_seat_precision_min"] >= 0.995
    assert acceptance["visible_target_recall_min"] >= 0.95
    assert acceptance["seat_errors_max"] == 0
    assert acceptance["false_complete_deals_max"] == 0

    boundary = gate["authority_boundary"]
    assert boundary["canonical_promotion_allowed"] is False
    assert boundary["school_canon_write_allowed"] is False
    assert boundary["world_write_allowed"] is False
    assert boundary["derive_fourth_hand"] is False
    assert boundary["deck_complement"] is False
    assert boundary["hidden_hand_inference"] is False
    assert boundary["unknown_hidden_cards_remain_unknown"] is True
