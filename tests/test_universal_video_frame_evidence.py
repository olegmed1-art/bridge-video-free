from universal_video.runner import (
    BRIDGE_EVIDENCE_REGIONS,
    FRAME_EVIDENCE_SCHEMA,
    plan_frame_evidence,
)


def test_bridge_frame_packet_contains_adjacent_frames_and_crop_regions():
    packet = plan_frame_evidence(
        301.0,
        interval_seconds=120,
        include_neighbors=True,
    )

    assert packet["schema"] == FRAME_EVIDENCE_SCHEMA
    assert packet["strategy"] == "anchor-neighbors-v1"
    assert packet["regions"] == BRIDGE_EVIDENCE_REGIONS
    middle = next(bundle for bundle in packet["bundles"] if bundle["anchor_time"] == 120.0)
    assert [member["role"] for member in middle["members"]] == ["BEFORE", "CENTER", "AFTER"]
    assert [member["time"] for member in middle["members"]] == [118.5, 120.0, 121.5]


def test_frame_packet_is_bounded_for_twelve_hour_video():
    packet = plan_frame_evidence(
        12 * 3600,
        interval_seconds=15,
        include_neighbors=True,
    )

    assert len(packet["timestamps"]) <= 300
    assert len(packet["bundles"]) <= 100
    assert packet["bundles"][0]["anchor_time"] == 0.0
    assert packet["bundles"][-1]["anchor_time"] == 12 * 3600 - 0.5


def test_non_bridge_packet_keeps_anchor_only_contract():
    packet = plan_frame_evidence(250.0, interval_seconds=120)

    assert packet["strategy"] == "anchor-only-v1"
    assert packet["regions"] == {}
    assert all([member["role"] for member in bundle["members"]] == ["CENTER"] for bundle in packet["bundles"])
