import pytest

from bridge_vision.bridgit_holdout_measurement import (
    HoldoutMeasurementError,
    score_holdout,
)


def pair(card, seat):
    return {"card": card, "seat": seat}


def case(
    case_id="case-001",
    *,
    gold=None,
    accepted=None,
    rejection_kind="NONE",
    full_layout_emitted=False,
    full_layout_forbidden=False,
    source="source-a",
    layout="layout-a",
    theme="theme-a",
    resolution="1686x720",
    tags=(),
):
    return {
        "case_id": case_id,
        "source_session_id": source,
        "layout_family": layout,
        "theme_family": theme,
        "resolution_group": resolution,
        "tags": list(tags),
        "gold_card_seat_pairs": gold or [],
        "accepted_card_seat_pairs": accepted or [],
        "rejection_kind": rejection_kind,
        "full_layout_emitted": full_layout_emitted,
        "full_layout_forbidden": full_layout_forbidden,
    }


def bundle(*cases):
    return {
        "result_scope": "SHADOW_ONLY",
        "canonical_promotion_allowed": False,
        "frozen_before_holdout_reveal": True,
        "cases": list(cases),
    }


def test_scores_correct_wrong_card_wrong_seat_and_strata_separately():
    report = score_holdout(
        bundle(
            case(
                gold=[pair("AH", "N"), pair("KH", "N"), pair("QH", "E")],
                accepted=[pair("AH", "N"), pair("KH", "E"), pair("2S", "W")],
                source="source-a",
                layout="desktop-wide",
                theme="dark",
                resolution="1920x1080",
                tags=("overlap",),
            )
        )
    )
    assert report["accepted_correct"] == 1
    assert report["accepted_wrong"] == 1
    assert report["accepted_wrong_card_seat"] == 2
    assert report["seat_errors"] == 1
    assert report["coverage"] == 1.0
    assert report["precision"] == pytest.approx(1 / 3)
    assert report["recall"] == pytest.approx(1 / 3)
    assert report["per_layout_metrics"]["desktop-wide"]["seat_errors"] == 1
    assert report["per_theme_metrics"]["dark"]["accepted_wrong"] == 1
    assert report["per_resolution_metrics"]["1920x1080"]["accepted_wrong_card_seat"] == 2
    assert report["per_stress_tag_metrics"]["overlap"]["accepted_correct"] == 1


def test_rejection_taxonomy_keeps_ambiguous_low_score_and_unknown_review_separate():
    report = score_holdout(
        bundle(
            case("amb", gold=[pair("AH", "N")], rejection_kind="AMBIGUOUS"),
            case("low", gold=[pair("KH", "N")], rejection_kind="LOW_SCORE"),
            case("unknown", gold=[pair("QH", "N")], rejection_kind="UNKNOWN"),
            case("review", gold=[pair("JH", "N")], rejection_kind="REVIEW"),
        )
    )
    assert report["rejected_ambiguous"] == 1
    assert report["rejected_low_score"] == 1
    assert report["unknown_or_review"] == 2
    assert report["rejected_other"] == 0
    assert report["coverage"] == 0.0
    assert report["precision"] is None
    assert report["mechanical_thresholds_pass"] is False


def test_partial_or_forbidden_full_layout_counts_false_complete():
    report = score_holdout(
        bundle(
            case(
                gold=[pair("AH", "N")],
                accepted=[pair("AH", "N")],
                full_layout_emitted=True,
                full_layout_forbidden=True,
            )
        )
    )
    assert report["false_complete_deals"] == 1
    assert report["mechanical_thresholds_pass"] is False


def test_wrong_card_in_emitted_full_layout_is_false_complete():
    report = score_holdout(
        bundle(
            case(
                gold=[pair("AH", "N")],
                accepted=[pair("2S", "N")],
                full_layout_emitted=True,
                full_layout_forbidden=False,
            )
        )
    )
    assert report["accepted_wrong"] == 1
    assert report["accepted_wrong_card_seat"] == 1
    assert report["false_complete_deals"] == 1


def test_exact_visible_targets_can_mechanically_pass_thresholds_without_claiming_readiness():
    gold = [pair(card, "N") for card in ("AH", "KH", "QH", "JH")]
    report = score_holdout(bundle(case(gold=gold, accepted=gold)))
    assert report["precision"] == 1.0
    assert report["recall"] == 1.0
    assert report["mechanical_thresholds_pass"] is True
    assert "final_status" not in report
    assert report["result_scope"] == "SHADOW_ONLY"
    assert report["canonical_promotion_allowed"] is False


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("result_scope", "PRODUCTION", "SHADOW_ONLY"),
        ("canonical_promotion_allowed", True, "canonical promotion"),
        ("frozen_before_holdout_reveal", False, "freeze not proven"),
    ],
)
def test_root_safety_gates_fail_closed(field, value, message):
    data = bundle(case(gold=[pair("AH", "N")]))
    data[field] = value
    with pytest.raises(HoldoutMeasurementError, match=message):
        score_holdout(data)


def test_duplicate_case_or_card_fails_closed():
    with pytest.raises(HoldoutMeasurementError, match="duplicate case_id"):
        score_holdout(bundle(case("same"), case("same")))
    with pytest.raises(HoldoutMeasurementError, match="duplicate accepted card"):
        score_holdout(
            bundle(
                case(
                    gold=[pair("AH", "N"), pair("KH", "N")],
                    accepted=[pair("AH", "N"), pair("AH", "E")],
                )
            )
        )


def test_unknown_rejection_kind_fails_closed():
    with pytest.raises(HoldoutMeasurementError, match="invalid rejection_kind"):
        score_holdout(
            bundle(case(gold=[pair("AH", "N")], rejection_kind="INFERRED_FOURTH_HAND"))
        )
