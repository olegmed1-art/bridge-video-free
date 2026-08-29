from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DENSE = ROOT / ".github/workflows/field-diana14-3-1-test-r5-dense.yml"
CALLER = ROOT / ".github/workflows/universal-video-ci.yml"


def test_dense_field_is_one_marker_guarded_reusable_shadow_workflow():
    dense = DENSE.read_text(encoding="utf-8")
    caller = CALLER.read_text(encoding="utf-8")

    assert "workflow_call:" in dense
    assert "frame_interval_seconds': 3" in dense
    assert "'planned_frames': 2318" in dense
    assert "'extracted_frames': 2318" in dense
    assert "'profile': 'bridge_lesson_3_1_test'" in dense
    assert "'canonical_promotion_allowed': False" in dense
    assert "'production_activation_allowed': False" in dense
    assert "'next_video_auto_start_allowed': False" in dense

    assert "dense-diana14-shadow:" in caller
    assert "<!-- diana14-dense-r5 -->" in caller
    assert "github.event.pull_request.head.sha == '9cd2f31cbfb9583acfabb55d2370a480f6855b49'" in caller
    assert "field-diana14-3-1-test-r5-dense.yml" in caller
    assert "secrets: inherit" in caller
