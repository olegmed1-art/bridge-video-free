import pytest
from bridge_contracts.video_learning_feedback import VideoLearningFeedbackError, build_learning_feedback


def _master():
    return {"source": {"sha256": "a" * 64}, "human_corrections": [{
        "correction_id": "c-1", "kind": "ASR", "input_ref": "segment-7",
        "corrected_value": "форсирует", "reviewer_ref": "teacher:diana", "evidence_refs": ["segment-7"],
    }]}


def test_creates_versioned_examples_but_never_trains_or_deploys():
    result = build_learning_feedback(_master(), {})
    assert len(result["training_examples"]) == 1
    assert result["authority"]["training_execution_allowed"] is False
    assert result["model_improvement_proposal"]["status"] == "HOLDOUT_NOT_PROVEN"


def test_holdout_proposal_requires_baseline_comparison_and_rollback():
    master = _master()
    master["model_evaluation"] = {
        "candidate_model_version": "asr-v2", "baseline_model_version": "asr-v1",
        "holdout_id": "holdout-2026-09", "rollback_model_version": "asr-v1",
        "metrics": {"wer": {"baseline": 0.8, "candidate": 0.85}},
    }
    result = build_learning_feedback(master, {})
    assert result["model_improvement_proposal"]["status"] == "HOLDOUT_PASS_CANDIDATE"
    assert result["model_improvement_proposal"]["deployment_allowed"] is False


def test_rejects_unreviewed_or_unknown_label_kind():
    master = _master(); master["human_corrections"][0]["kind"] = "INVENTED"
    with pytest.raises(VideoLearningFeedbackError, match="unsupported"):
        build_learning_feedback(master, {})
