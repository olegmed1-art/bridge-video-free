import pytest

from bridge_contracts.video_frame import (
    BRIDGE_VIDEO_FRAME_CONTRACT_VERSION,
    BridgeVideoFrameContractError,
    canonicalize_frame_recognition,
)


def test_existing_report_parser_shape_becomes_canonical_frame():
    record = canonicalize_frame_recognition(
        {
            "status": "PARTIAL_BOARD_OBSERVATION",
            "hands": {"N": ["AS", "KH"], "S": ["QD", "JC"]},
            "recognized_card_count": 4,
            "state_fingerprint": "0123456789abcdefabcd",
        },
        time=123.5,
        frame_file="frame-0003-000123.jpg",
        frame_sha256="a" * 64,
    ).to_dict()

    assert record["contract_version"] == BRIDGE_VIDEO_FRAME_CONTRACT_VERSION
    assert record["parser_status"] == "PARTIAL_BOARD_OBSERVATION"
    assert record["recognized_card_count"] == 4
    assert record["time"] == 123.5
    assert record["frame_file"] == "frame-0003-000123.jpg"
    assert record["deal"]["hands"]["N"]["cards"] == ["AS", "KH"]
    assert record["deal"]["hands"]["S"]["cards"] == ["QD", "JC"]
    assert record["deal"]["derivations"] == []


def test_reported_card_count_must_match_recognized_hands():
    with pytest.raises(BridgeVideoFrameContractError, match="does not match"):
        canonicalize_frame_recognition(
            {
                "status": "PARTIAL_BOARD_OBSERVATION",
                "hands": {"N": ["AS", "KS", "QS", "JS"]},
                "recognized_card_count": 5,
            }
        )


def test_conflict_and_unavailable_cannot_expose_cards():
    with pytest.raises(BridgeVideoFrameContractError, match="must not expose"):
        canonicalize_frame_recognition(
            {"status": "CONFLICT", "hands": {"N": ["AS"]}}
        )

    record = canonicalize_frame_recognition(
        {"status": "UNAVAILABLE", "hands": {}}
    ).to_dict()
    assert record["deal"] is None
    assert record["recognized_card_count"] == 0


def test_partial_status_requires_existing_parser_minimum_four_cards():
    with pytest.raises(BridgeVideoFrameContractError, match="fewer than four"):
        canonicalize_frame_recognition(
            {
                "status": "PARTIAL_BOARD_OBSERVATION",
                "hands": {"N": ["AS", "KS", "QS"]},
                "recognized_card_count": 3,
            }
        )


def test_frame_metadata_is_fail_closed():
    with pytest.raises(BridgeVideoFrameContractError, match="frame time"):
        canonicalize_frame_recognition(
            {"status": "INSUFFICIENT", "hands": {}},
            time=-1,
        )
    with pytest.raises(BridgeVideoFrameContractError, match="frame_sha256"):
        canonicalize_frame_recognition(
            {"status": "INSUFFICIENT", "hands": {}},
            frame_sha256="not-a-sha",
        )
