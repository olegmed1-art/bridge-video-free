import hashlib
import json

import pytest
from bridge_contracts.video_learning_feedback import VideoLearningFeedbackError, build_learning_feedback


def _master():
    return {"source": {"sha256": "a" * 64}, "human_corrections": [{
        "correction_id": "c-1", "kind": "ASR", "input_ref": "segment-7",
        "corrected_value": "форсирует", "reviewer_ref": "teacher:diana", "evidence_refs": ["segment-7"],
    }]}


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _quality(master=None):
    master = master or _master()
    correction = master["human_corrections"][0]
    receipt = {
        "correction_id": correction["correction_id"],
        "kind": correction["kind"],
        "reviewer_ref": correction["reviewer_ref"],
        "source_sha256": master["source"]["sha256"],
        "input_ref": correction["input_ref"],
        "corrected_value_sha256": _digest(correction["corrected_value"]),
        "evidence_refs": correction["evidence_refs"],
        "status": "VERIFIED",
    }
    receipt["receipt_sha256"] = _digest(receipt)
    return {"correction_review_receipts": [receipt]}


def _resolver(quality):
    receipts = {item["receipt_sha256"]: item for item in quality["correction_review_receipts"]}
    return lambda receipt_sha: receipts.get(receipt_sha)


def test_creates_versioned_examples_but_never_trains_or_deploys():
    master = _master()
    quality = _quality(master)
    result = build_learning_feedback(
        master, quality, correction_receipt_resolver=_resolver(quality)
    )
    assert len(result["training_examples"]) == 1
    assert result["authority"]["training_execution_allowed"] is False
    assert result["model_improvement_proposal"]["status"] == "HOLDOUT_NOT_PROVEN"


def test_holdout_proposal_requires_baseline_comparison_and_rollback():
    master = _master()
    master["model_evaluation"] = {
        "candidate_model_version": "asr-v2", "baseline_model_version": "asr-v1",
        "holdout_id": "holdout-2026-09", "rollback_model_version": "asr-v1",
        "metrics": {"wer": {"baseline": 0.8, "candidate": 0.7,
                              "direction": "LOWER_IS_BETTER", "minimum_delta": 0.05}},
    }
    quality = _quality(master)
    result = build_learning_feedback(
        master, quality, correction_receipt_resolver=_resolver(quality)
    )
    assert result["model_improvement_proposal"]["status"] == "HOLDOUT_PASS_CANDIDATE"
    assert result["model_improvement_proposal"]["deployment_allowed"] is False


def test_rejects_unreviewed_or_unknown_label_kind():
    master = _master(); master["human_corrections"][0]["kind"] = "INVENTED"
    quality = _quality(master)
    with pytest.raises(VideoLearningFeedbackError, match="unsupported"):
        build_learning_feedback(
            master, quality, correction_receipt_resolver=_resolver(quality)
        )


def test_rejects_forged_review_receipt_and_versions_changed_content():
    master = _master()
    quality = _quality(master)
    quality["correction_review_receipts"][0]["reviewer_ref"] = "forged"
    with pytest.raises(VideoLearningFeedbackError, match="digest mismatch"):
        build_learning_feedback(
            master, quality, correction_receipt_resolver=_resolver(quality)
        )

    first_quality = _quality(master)
    first = build_learning_feedback(
        master, first_quality,
        correction_receipt_resolver=_resolver(first_quality),
    )["training_examples"][0]
    changed = _master()
    changed["human_corrections"][0]["corrected_value"] = "не форсирует"
    changed_quality = _quality(changed)
    second = build_learning_feedback(
        changed, changed_quality,
        correction_receipt_resolver=_resolver(changed_quality),
    )["training_examples"][0]
    assert first["training_example_id"] != second["training_example_id"]


def test_correction_receipt_cannot_be_replayed_for_another_kind():
    master = _master()
    quality = _quality(master)
    master["human_corrections"][0]["kind"] = "PEDAGOGY"
    with pytest.raises(VideoLearningFeedbackError, match="binding mismatch"):
        build_learning_feedback(
            master, quality, correction_receipt_resolver=_resolver(quality)
        )


def test_holdout_metric_direction_is_enforced():
    master = _master()
    master["model_evaluation"] = {
        "candidate_model_version": "asr-v2", "baseline_model_version": "asr-v1",
        "holdout_id": "holdout-2026-09", "rollback_model_version": "asr-v1",
        "metrics": {"wer": {"baseline": 0.8, "candidate": 0.85,
                              "direction": "LOWER_IS_BETTER", "minimum_delta": 0.0}},
    }
    quality = _quality(master)
    result = build_learning_feedback(
        master, quality, correction_receipt_resolver=_resolver(quality)
    )
    assert result["model_improvement_proposal"]["status"] == "HOLDOUT_NOT_PROVEN"


@pytest.mark.parametrize("candidate", [
    True, "inf", float("inf"), float("nan"), 10**1000,
])
def test_holdout_rejects_non_numeric_or_nonfinite_metrics(candidate):
    master = _master()
    master["model_evaluation"] = {
        "candidate_model_version": "asr-v2", "baseline_model_version": "asr-v1",
        "holdout_id": "holdout-2026-09", "rollback_model_version": "asr-v1",
        "metrics": {"wer": {"baseline": 0.8, "candidate": candidate,
                              "direction": "HIGHER_IS_BETTER", "minimum_delta": 0.0}},
    }
    quality = _quality(master)
    result = build_learning_feedback(
        master, quality, correction_receipt_resolver=_resolver(quality)
    )
    assert result["model_improvement_proposal"]["status"] == "HOLDOUT_NOT_PROVEN"


def test_holdout_rejects_nonfinite_computed_delta():
    master = _master()
    master["model_evaluation"] = {
        "candidate_model_version": "asr-v2", "baseline_model_version": "asr-v1",
        "holdout_id": "holdout-2026-09", "rollback_model_version": "asr-v1",
        "metrics": {"score": {"baseline": -1e308, "candidate": 1e308,
                                "direction": "HIGHER_IS_BETTER", "minimum_delta": 0.0}},
    }
    quality = _quality(master)
    result = build_learning_feedback(
        master, quality, correction_receipt_resolver=_resolver(quality)
    )
    assert result["model_improvement_proposal"]["status"] == "HOLDOUT_NOT_PROVEN"


def test_self_hashed_receipt_is_not_trusted_without_authoritative_resolver():
    master = _master()
    quality = _quality(master)
    with pytest.raises(VideoLearningFeedbackError, match="trusted correction"):
        build_learning_feedback(master, quality)
    with pytest.raises(VideoLearningFeedbackError, match="trusted storage"):
        build_learning_feedback(
            master, quality, correction_receipt_resolver=lambda _: None
        )
