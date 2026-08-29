import pytest

from bridge_vision.shadow_pbn import ShadowPbnError, render_shadow_pbn


def candidate(hands, *, board=1, identity="board-1"):
    return {
        "hands": hands,
        "confidence": 0.99,
        "evidence": {
            "canonical_promotion_allowed": False,
            "deal_identity": {"kind": "EXPLICIT_BOARD", "scope": "diana-14", "value": identity},
            "board_metadata": {
                "status": "CONFIRMED",
                "board_number": board,
                "dealer": ("N", "E", "S", "W")[(board - 1) % 4],
                "vulnerability": "NONE" if board == 1 else "NS",
            },
        },
    }


def record(hands, *, frame="f1.jpg", board=1, identity="board-1"):
    return {
        "status": "PARTIAL_BOARD_OBSERVATION",
        "frame_file": frame,
        "candidates": [candidate(hands, board=board, identity=identity)],
    }


def test_partial_observations_are_shown_without_false_standard_deal():
    text = render_shadow_pbn([record({"S": ["AS", "KH", "3C"]})], source="Diana 14")
    assert '[Board "1"]' in text
    assert '[Dealer "N"]' in text
    assert '[X-Observed-S "A.K.-.3"]' in text
    assert '[X-UnknownCount-S "10"]' in text
    assert '[X-ObservedCount "3"]' in text
    assert '[X-DealStatus "PARTIAL_OBSERVED_NO_STANDARD_DEAL_TAG"]' in text
    assert '[Deal "' not in text


def test_same_board_accumulates_only_accepted_candidate_cards():
    text = render_shadow_pbn([
        record({"S": ["AS"]}, frame="f1.jpg"),
        record({"S": ["AS", "KS"], "W": ["AH"]}, frame="f2.jpg"),
    ])
    assert '[X-Observed-S "AK.-.-.-"]' in text
    assert '[X-Observed-W "-.A.-.-"]' in text
    assert '[X-ObservedCount "3"]' in text
    assert '[X-SourceFrames "f1.jpg,f2.jpg"]' in text


def test_board_change_creates_a_separate_pbn_block():
    text = render_shadow_pbn([
        record({"S": ["AS"]}),
        record({"E": ["KH"]}, frame="f2.jpg", board=2, identity="board-2"),
    ])
    assert text.count('[Event "Video card recognition SHADOW"]') == 2
    assert '[Board "1"]' in text and '[Board "2"]' in text


def test_conflict_record_is_not_exported_as_found_cards():
    text = render_shadow_pbn([{"status": "CONFLICT", "candidates": [candidate({"S": ["AS"]})]}])
    assert "No accepted card observations" in text
    assert "X-Observed-S" not in text


def test_cross_seat_temporal_conflict_fails_closed():
    with pytest.raises(ShadowPbnError, match="cross-seat temporal"):
        render_shadow_pbn([
            record({"S": ["AS"]}, frame="f1.jpg"),
            record({"N": ["AS"]}, frame="f2.jpg"),
        ])


def test_non_shadow_candidate_is_rejected():
    bad = record({"S": ["AS"]})
    bad["candidates"][0]["evidence"]["canonical_promotion_allowed"] = True
    with pytest.raises(ShadowPbnError, match="shadow boundary"):
        render_shadow_pbn([bad])
