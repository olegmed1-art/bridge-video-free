import json
from pathlib import Path

from bridge_contracts.video_deal import canonicalize_video_deal
from bridge_vision.multiframe import reconstruct_deals
from tools.bridge_video_deals import reconstruct_job


def rec(cards, *, frame, time=0, board_id=None):
    hands = {seat: values for seat, values in cards.items()}
    out = {
        "status": "PARTIAL_BOARD_OBSERVATION",
        "deal": canonicalize_video_deal({"hands": hands}).to_dict(),
        "frame_file": frame,
        "frame_sha256": (frame[0] if frame else "a") * 64,
        "time": time,
    }
    if board_id is not None:
        out["board_id"] = board_id
    return out


def test_time_proximity_alone_never_links_frames():
    records = [
        rec({"N": ["AS", "KS", "QS", "JS"]}, frame="a.jpg", time=10),
        rec({"N": ["AH", "KH", "QH", "JH"]}, frame="b.jpg", time=10.1),
    ]
    result = reconstruct_deals(records).to_dict()
    assert result["deal_count"] == 2


def test_strong_same_seat_overlap_fuses_and_accumulates_evidence():
    records = [
        rec({"N": ["AS", "KS", "QS", "JS", "TS"]}, frame="a.jpg", time=10),
        rec({"N": ["AS", "KS", "QS", "JS", "9S"], "S": ["AH"]}, frame="b.jpg", time=40),
    ]
    result = reconstruct_deals(records).to_dict()
    assert result["deal_count"] == 1
    deal = result["deals"][0]
    assert set(deal["deal"]["hands"]["N"]["cards"]) == {"AS", "KS", "QS", "JS", "TS", "9S"}
    assert deal["deal"]["hands"]["S"]["cards"] == ["AH"]


def test_cross_seat_conflict_does_not_merge():
    records = [
        rec({"N": ["AS", "KS", "QS", "JS"]}, frame="a.jpg"),
        rec({"S": ["AS", "KS", "QS", "JS"]}, frame="b.jpg"),
    ]
    result = reconstruct_deals(records).to_dict()
    assert result["deal_count"] == 2


def test_explicit_board_identity_can_link_without_card_overlap():
    records = [
        rec({"N": ["AS"]}, frame="a.jpg", board_id="17"),
        rec({"S": ["KH"]}, frame="b.jpg", board_id="17"),
    ]
    result = reconstruct_deals(records).to_dict()
    assert result["deal_count"] == 1
    assert result["deals"][0]["explicit_board_key"] == "board_id:17"


def test_job_tool_writes_compact_deals_artifact(tmp_path: Path):
    records = [
        rec({"N": ["AS", "KS", "QS", "JS"]}, frame="a.jpg"),
        rec({"N": ["AS", "KS", "QS", "JS", "TS"]}, frame="b.jpg"),
    ]
    (tmp_path / "bridge_positions.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in records), encoding="utf-8"
    )
    summary = reconstruct_job(tmp_path)
    assert summary["status"] == "COMPLETED"
    assert summary["deal_count"] == 1
    artifact = json.loads((tmp_path / "bridge_deals.json").read_text(encoding="utf-8"))
    assert artifact["deal_count"] == 1
