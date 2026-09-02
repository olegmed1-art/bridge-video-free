from pathlib import Path

from universal_video.finops_observation import build_video_finops_observation, directory_bytes


def test_video_finops_observation_records_usage_without_inventing_cost() -> None:
    item = build_video_finops_observation(
        status="COMPLETED",
        elapsed_seconds=1.234,
        input_bytes=100,
        output_bytes=40,
        video_seconds=60.0,
        whisper_model="small",
        source_kind="google_drive",
    )
    assert item["category"] == "VIDEO"
    assert item["provider"] == "oracle"
    assert item["wall_time_ms"] == 1234
    assert item["input_bytes"] == 100
    assert item["output_bytes"] == 40
    assert item["video_seconds"] == 60.0
    assert item["pricing_basis"] == "runtime_observed_cost_pending"
    assert item["estimated_cost_usd"] is None


def test_directory_bytes_is_bounded_to_actual_files(tmp_path: Path) -> None:
    (tmp_path / "a.bin").write_bytes(b"1234")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "b.bin").write_bytes(b"12")
    assert directory_bytes(tmp_path) == 6


def test_video_service_reserves_capacity_for_interactive_compute() -> None:
    text = Path("deploy/oracle-universal-video/universal-video.service").read_text(encoding="utf-8")
    assert "Nice=10" in text
    assert "CPUQuota=400%" in text
    assert "CPUWeight=20" in text
    assert "IOSchedulingClass=idle" in text
    assert "IOWeight=20" in text
