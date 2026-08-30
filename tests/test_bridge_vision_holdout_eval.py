import pytest

from bridge_vision.holdout_eval import evaluate_labelled_outcomes

SHA = "a" * 64


def _row(observation_id, gold, predicted=None, reason=None, **extra):
    return {
        "frame_sha256": SHA, "observation_id": observation_id,
        "gold": gold, "predicted": predicted, "reason": reason, **extra,
    }


def test_holdout_pass_requires_precision_recall_and_zero_seat_errors():
    rows = [_row(str(i), "A", "A", gold_seat="N", predicted_seat="N") for i in range(20)]
    result = evaluate_labelled_outcomes(rows, channel="rank", dataset_partition="HOLDOUT")
    assert result["status"] == "PASS"
    assert result["precision"] == result["recall"] == 1.0
    assert result["counts"]["seat_errors"] == 0
    assert result["quality_gate_passed"] is True
    assert result["production_activation_allowed"] is False


def test_rejection_is_fn_and_cannot_pass_on_low_coverage():
    rows = [_row(str(i), "A", "A") for i in range(19)]
    rows.append(_row("rejected", "K", reason="LOW_RANK_CONFIDENCE"))
    result = evaluate_labelled_outcomes(rows, channel="rank", dataset_partition="HOLDOUT")
    assert result["status"] == "PASS"  # exactly the required 0.95 recall
    rows.append(_row("ambiguous", "Q", reason="AMBIGUOUS_GLYPH"))
    result = evaluate_labelled_outcomes(rows, channel="rank", dataset_partition="HOLDOUT")
    assert result["status"] == "FAIL"
    assert result["counts"]["fn"] == 2
    assert result["counts"]["rejected_low_confidence"] == 1
    assert result["counts"]["rejected_ambiguous"] == 1


def test_wrong_acceptance_counts_fp_fn_and_confusion():
    result = evaluate_labelled_outcomes(
        [_row("one", "H", "D")], channel="suit", dataset_partition="HOLDOUT"
    )
    assert result["status"] == "FAIL"
    assert result["counts"]["accepted_wrong"] == 1
    assert result["counts"]["fp"] == result["counts"]["fn"] == 1
    assert result["confusion"] == {"H": {"D": 1}}


def test_training_partition_and_missing_human_label_are_rejected():
    with pytest.raises(ValueError, match="HOLDOUT"):
        evaluate_labelled_outcomes([], channel="card", dataset_partition="TRAIN")
    with pytest.raises(ValueError, match="gold label"):
        evaluate_labelled_outcomes(
            [_row("one", "", "AS")], channel="card", dataset_partition="HOLDOUT"
        )


def test_seat_error_fails_even_for_correct_card_identity():
    result = evaluate_labelled_outcomes(
        [_row("one", "AS", "AS", gold_seat="N", predicted_seat="E")],
        channel="card", dataset_partition="HOLDOUT",
    )
    assert result["precision"] == result["recall"] == 1.0
    assert result["counts"]["seat_errors"] == 1
    assert result["status"] == "FAIL"


def test_thresholds_cannot_be_lowered_to_manufacture_pass():
    with pytest.raises(ValueError, match="cannot be lowered"):
        evaluate_labelled_outcomes(
            [], channel="card", dataset_partition="HOLDOUT", min_recall=0.5
        )


def test_channel_labels_and_seats_are_validated():
    with pytest.raises(ValueError, match="invalid for its channel"):
        evaluate_labelled_outcomes(
            [_row("one", "RED", "H")], channel="suit", dataset_partition="HOLDOUT"
        )
    with pytest.raises(ValueError, match="gold_seat"):
        evaluate_labelled_outcomes(
            [_row("one", "AS", "AS", gold_seat="X")],
            channel="card", dataset_partition="HOLDOUT",
        )
