import copy

import pytest

from bridge_vision.bridgit_holdout_measurement import HoldoutMeasurementError
from bridge_vision.bridgit_holdout_portable import (
    FREEZE_RECEIPT_VERSION,
    PORTABLE_EVALUATOR_VERSION,
    evaluator_source_sha256,
    frozen_thresholds_sha256,
    score_portable_frozen_holdout,
)


def pair(card, seat):
    return {"card": card, "seat": seat}


def case(case_id, card, rejection_kind="NONE", *, accepted=False, source="s1", layout="l1"):
    gold = [pair(card, "N")]
    return {
        "case_id": case_id,
        "source_session_id": source,
        "layout_family": layout,
        "theme_family": "dark",
        "resolution_group": "1920x1080",
        "tags": [],
        "gold_card_seat_pairs": gold,
        "accepted_card_seat_pairs": gold if accepted else [],
        "rejection_kind": rejection_kind,
        "full_layout_emitted": False,
        "full_layout_forbidden": True,
    }


def freeze_receipt():
    return {
        "freeze_receipt_version": FREEZE_RECEIPT_VERSION,
        "recognizer_head_git_sha": "b" * 40,
        "profile_sha256": "1" * 64,
        "config_sha256": "2" * 64,
        "train_template_exclusion_manifest_sha256": "3" * 64,
        "holdout_manifest_sha256": "4" * 64,
        "human_gold_sha256": "5" * 64,
        "recognizer_output_sha256": "6" * 64,
        "evaluator_source_sha256": evaluator_source_sha256(),
        "portable_evaluator_version": PORTABLE_EVALUATOR_VERSION,
        "measurement_scorer_version": "bridgit-holdout-measurement-v2",
        "thresholds_sha256": frozen_thresholds_sha256(),
        "frozen_before_holdout_reveal": True,
        "threshold_tuning_on_holdout": False,
        "fourth_hand_reconstruction_allowed": False,
        "logical_inference_as_visual_allowed": False,
    }


def bundle(*cases):
    return {
        "result_scope": "SHADOW_ONLY",
        "canonical_promotion_allowed": False,
        "frozen_before_holdout_reveal": True,
        "freeze": freeze_receipt(),
        "cases": list(cases),
    }


def test_portable_report_exposes_requested_metrics_and_splits_unknown_review():
    report = score_portable_frozen_holdout(
        bundle(
            case("ok", "AH", accepted=True, source="s1", layout="wide"),
            case("unknown", "KH", "UNKNOWN", source="s2", layout="wide"),
            case("review", "QH", "REVIEW", source="s2", layout="compact"),
        )
    )
    assert report["total"] == 3
    assert report["accepted_correct"] == 1
    assert report["accepted_wrong"] == 0
    assert report["unknown"] == 1
    assert report["needs_review"] == 1
    assert report["wrong_card_and_seat"] == 0
    assert set(report["per_source"]) == {"s1", "s2"}
    assert set(report["per_layout"]) == {"compact", "wide"}
    assert report["portable_freeze_valid"] is True
    assert "final_status" not in report


def test_implicit_unresolved_none_is_unknown_not_review():
    report = score_portable_frozen_holdout(bundle(case("missing", "AH")))
    assert report["unknown"] == 1
    assert report["needs_review"] == 0
    assert report["unknown_or_review"] == 1


def test_portable_scoring_is_deterministic_under_case_order():
    left = case("a", "AH", accepted=True, source="s2", layout="b")
    right = case("b", "KH", "UNKNOWN", source="s1", layout="a")
    report_a = score_portable_frozen_holdout(bundle(left, right))
    report_b = score_portable_frozen_holdout(bundle(right, left))
    assert report_a == report_b


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("recognizer_head_git_sha", "x" * 40, "recognizer_head_git_sha"),
        ("profile_sha256", "x" * 64, "profile_sha256"),
        ("evaluator_source_sha256", "0" * 64, "evaluator source digest"),
        ("thresholds_sha256", "0" * 64, "threshold digest"),
        ("portable_evaluator_version", "other", "evaluator version"),
        ("measurement_scorer_version", "other", "scorer version"),
        ("frozen_before_holdout_reveal", False, "frozen_before_holdout_reveal"),
        ("threshold_tuning_on_holdout", True, "threshold_tuning_on_holdout"),
        ("fourth_hand_reconstruction_allowed", True, "fourth_hand_reconstruction_allowed"),
        ("logical_inference_as_visual_allowed", True, "logical_inference_as_visual_allowed"),
    ],
)
def test_freeze_receipt_fails_closed(field, value, message):
    data = bundle(case("a", "AH", accepted=True))
    data = copy.deepcopy(data)
    data["freeze"][field] = value
    with pytest.raises(HoldoutMeasurementError, match=message):
        score_portable_frozen_holdout(data)


def test_missing_freeze_receipt_fails_closed():
    data = bundle(case("a", "AH", accepted=True))
    del data["freeze"]
    with pytest.raises(HoldoutMeasurementError, match="freeze must be an object"):
        score_portable_frozen_holdout(data)
