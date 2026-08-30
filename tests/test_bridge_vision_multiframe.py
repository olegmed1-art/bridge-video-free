import json
from pathlib import Path

from bridge_contracts.video_deal import canonicalize_video_deal
from bridge_vision.multiframe import reconstruct_deals
from tools.bridge_video_deals import reconstruct_job


def rec(cards, *, frame, time=0, board_id=None, board_number=None, board_scope=None):
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
    if board_number is not None:
        out["board_number"] = board_number
    if board_scope is not None:
        out["board_scope"] = board_scope
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
    assert deal["status"] == "REVIEW"
    assert deal["validation"]["status"] == "REVIEW"
    assert result["status"] == "REVIEW"


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


def test_three_hands_shown_across_frames_derive_only_the_missing_fourth_hand():
    ranks = "AKQJT98765432"
    records = [
        rec({"N": [f"{rank}S" for rank in ranks]}, frame="a.jpg", board_id="same-deal"),
        rec({"E": [f"{rank}H" for rank in ranks]}, frame="b.jpg", board_id="same-deal"),
        rec({"S": [f"{rank}D" for rank in ranks]}, frame="c.jpg", board_id="same-deal"),
    ]
    result = reconstruct_deals(records).to_dict()
    assert result["deal_count"] == 1
    reconstructed = result["deals"][0]
    assert reconstructed["observed_card_count"] == 39
    assert len(reconstructed["deal"]["hands"]["W"]["cards"]) == 13
    derivation = reconstructed["deal"]["derivations"][0]
    assert derivation["provenance_class"] == "DERIVED"
    assert derivation["confidence"]["source_observation_floor"] is None
    assert reconstructed["status"] == "VERIFIED_FULL_BOARD"
    assert reconstructed["validation"]["seat_counts"] == {"N": 13, "E": 13, "S": 13, "W": 13}
    assert result["status"] == "COMPLETED"
    assert result["verified_full_board_count"] == 1
    assert result["canonical_promotion_allowed"] is False


def test_explicit_identity_may_disappear_when_card_evidence_is_strong():
    records = [
        rec({"N": ["AS", "KS", "QS", "JS"]}, frame="a.jpg", board_id="17"),
        rec({"N": ["AS", "KS", "QS", "JS", "TS"]}, frame="b.jpg"),
    ]
    result = reconstruct_deals(records).to_dict()
    assert result["deal_count"] == 1
    assert result["deals"][0]["explicit_board_key"] == "board_id:17"


def test_bare_board_number_is_not_strong_identity():
    records = [
        rec({"N": ["AS"]}, frame="a.jpg", board_number="1"),
        rec({"S": ["KH"]}, frame="b.jpg", board_number="1"),
    ]
    result = reconstruct_deals(records).to_dict()
    assert result["deal_count"] == 0
    assert result["review_frame_count"] == 2


def test_scoped_board_number_is_strong_identity():
    records = [
        rec({"N": ["AS"]}, frame="a.jpg", board_number="1", board_scope="session-a"),
        rec({"S": ["KH"]}, frame="b.jpg", board_number="1", board_scope="session-a"),
    ]
    result = reconstruct_deals(records).to_dict()
    assert result["deal_count"] == 1
    assert result["deals"][0]["explicit_board_key"] == "board_number:session-a:1"


def test_duplicate_frame_evidence_is_not_counted_twice():
    first = rec({"N": ["AS", "KS", "QS", "JS"]}, frame="a.jpg")
    duplicate = dict(first)
    duplicate["time"] = 999
    result = reconstruct_deals([first, duplicate]).to_dict()
    assert result["deal_count"] == 1
    assert result["deals"][0]["frame_indices"] == [0]
    assert result["review_frames"][0]["reason"] == "DUPLICATE_FRAME_EVIDENCE"


def test_job_tool_writes_compact_deals_artifact(tmp_path: Path):
    records = [
        rec({"N": ["AS", "KS", "QS", "JS"]}, frame="a.jpg"),
        rec({"N": ["AS", "KS", "QS", "JS", "TS"]}, frame="b.jpg"),
    ]
    (tmp_path / "bridge_positions.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in records), encoding="utf-8"
    )
    summary = reconstruct_job(tmp_path)
    assert summary["status"] == "REVIEW"
    assert summary["deal_count"] == 1
    assert summary["verified_full_board_count"] == 0
    assert summary["review_deal_count"] == 1
    assert summary["canonical_promotion_allowed"] is False
    artifact = json.loads((tmp_path / "bridge_deals.json").read_text(encoding="utf-8"))
    assert artifact["deal_count"] == 1
