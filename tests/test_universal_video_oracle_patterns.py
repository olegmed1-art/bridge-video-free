from pathlib import Path

import pytest

from universal_video import runner
from universal_video.comparison import compare_transcripts
from universal_video.contract import VideoContractError, validate_job


def test_oracle_style_scene_controls_are_bounded(tmp_path: Path):
    media = tmp_path / "lesson.mp4"
    media.write_bytes(b"x")
    job = validate_job(
        {
            "job_id": "scene-controls",
            "profile": "educational",
            "source": {"kind": "local_path", "path": str(media)},
            "options": {"frame_strategy": "hybrid", "scene_sensitivity": 70, "min_scene_seconds": 8},
        },
        allowed_local_root=str(tmp_path),
    )
    assert job.options["frame_strategy"] == "hybrid"
    assert job.options["scene_sensitivity"] == 70


def test_invalid_scene_strategy_fails_closed(tmp_path: Path):
    media = tmp_path / "lesson.mp4"
    media.write_bytes(b"x")
    with pytest.raises(VideoContractError):
        validate_job(
            {
                "job_id": "bad-scene",
                "profile": "educational",
                "source": {"kind": "local_path", "path": str(media)},
                "options": {"frame_strategy": "magic"},
            },
            allowed_local_root=str(tmp_path),
        )


def test_zero_scene_sensitivity_is_preserved_as_explicit_disable(tmp_path: Path):
    media = tmp_path / "lesson.mp4"
    media.write_bytes(b"x")
    job = validate_job(
        {
            "job_id": "no-scenes",
            "profile": "educational",
            "source": {"kind": "local_path", "path": str(media)},
            "options": {"frame_strategy": "hybrid", "scene_sensitivity": 0},
        },
        allowed_local_root=str(tmp_path),
    )
    assert job.options["scene_sensitivity"] == 0
    assert runner._scene_timestamps(media, sensitivity=0, min_scene_seconds=5) == []


def test_provider_comparison_routes_disagreement_to_review():
    result = compare_transcripts(
        [{"start": 0, "end": 4, "text": "контракт четыре пики"}],
        [{"start": 0.2, "end": 4.1, "text": "контракт четыре червы"}],
    )
    assert result["summary"]["disagree"] == 1
    assert result["summary"]["review_required"] == 1


def test_scene_timestamps_apply_minimum_clip_length(monkeypatch):
    class Result:
        returncode = 0
        stderr = "pts_time:2.0 pts_time:3.0 pts_time:9.5 pts_time:16.0"

    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: Result())
    assert runner._scene_timestamps(Path("x.mp4"), sensitivity=50, min_scene_seconds=6) == [2.0, 9.5, 16.0]
