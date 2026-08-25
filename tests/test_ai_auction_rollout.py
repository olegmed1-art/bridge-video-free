import pytest

from bridge_school_api.ai_auction_rollout import (
    AuctionRolloutError,
    analyze_auction,
    ben_request_sha256,
    normalize_call,
    rollout_worlds,
)


def _ben_response(request, call, score=1.0):
    return {
        "bid": call,
        "candidates": [{"call": call, "insta_score": score}],
        "request_sha256": ben_request_sha256(request),
    }


def test_normalization_and_auction_completion():
    assert normalize_call("1NT") == "1N"
    assert normalize_call("dbl") == "X"
    state = analyze_auction("N", ["1H", "PASS", "2H", "PASS", "PASS", "PASS"])
    assert state.complete is True
    assert state.contract == "2H"
    assert state.declarer == "N"


def test_passed_out_and_doubled_contract():
    passed = analyze_auction("E", ["P", "P", "P", "P"])
    assert passed.complete and passed.contract is None and passed.declarer is None
    doubled = analyze_auction("N", ["1S", "X", "PASS", "PASS", "PASS"])
    assert doubled.contract == "1SX" and doubled.declarer == "N"


@pytest.mark.parametrize("calls", [
    ["1S", "1H"],
    ["X"],
    ["1H", "PASS", "X"],
    ["PASS", "PASS", "PASS", "PASS", "PASS"],
])
def test_illegal_auctions_fail_closed(calls):
    with pytest.raises(AuctionRolloutError):
        analyze_auction("N", calls)


def test_rollout_completes_each_world_without_claiming_dds_evidence():
    worlds = [{
        "world_index": 0,
        "hands": {
            "N": "AKQJT98765432...",
            "E": ".AKQJT98765432..",
            "S": "..AKQJT98765432.",
            "W": "...AKQJT98765432",
        },
    }]
    replies = iter(["PASS", "PASS", "PASS"])

    def bidder(request):
        call = next(replies)
        return _ben_response(request, call)

    result = rollout_worlds(
        worlds=worlds,
        dealer="N",
        auction=[],
        decision_seat="N",
        candidate_call="1NT",
        ben_bidder=bidder,
    )
    assert result["complete"] is True
    assert result["evidence_class"] == "BEN_AUCTION_ROLLOUT"
    assert result["vulnerability"] == "NONE"
    assert result["dds_evaluated"] is False
    assert result["worlds"][0]["contract"] == "1N"
    assert result["worlds"][0]["declarer"] == "N"
    assert len(result["worlds"][0]["world_fingerprint"]) == 64
    assert len(result["worlds"][0]["deal_pbn_sha256"]) == 64
    assert len(result["worlds"][0]["ben_request_sha256s"]) == 3


def test_rollout_rejects_wrong_turn_bad_ben_contract_and_call_overrun():
    world = {"hands": {
        "N": "AKQJT98765432...",
        "E": ".AKQJT98765432..",
        "S": "..AKQJT98765432.",
        "W": "...AKQJT98765432",
    }}
    with pytest.raises(AuctionRolloutError, match="decision seat"):
        rollout_worlds(worlds=[world], dealer="N", auction=[], decision_seat="E",
                       candidate_call="PASS", ben_bidder=lambda *_: {})
    with pytest.raises(AuctionRolloutError, match="no candidates"):
        rollout_worlds(worlds=[world], dealer="N", auction=[], decision_seat="N",
                       candidate_call="1C", ben_bidder=lambda request: {
                           "bid": "PASS",
                           "candidates": [],
                           "request_sha256": ben_request_sha256(request),
                       })

    bids = iter(["1D", "1H", "1S"])

    def endless(request):
        call = next(bids)
        return _ben_response(request, call)

    with pytest.raises(AuctionRolloutError, match="call limit"):
        rollout_worlds(worlds=[world], dealer="N", auction=[], decision_seat="N",
                       candidate_call="1C", ben_bidder=endless, max_calls_per_world=2)


def test_rollout_rejects_unscored_selected_call_and_duplicate_worlds():
    world = {"hands": {
        "N": "AKQJT98765432...",
        "E": ".AKQJT98765432..",
        "S": "..AKQJT98765432.",
        "W": "...AKQJT98765432",
    }}

    with pytest.raises(AuctionRolloutError, match="finite candidate score"):
        rollout_worlds(
            worlds=[world], dealer="N", auction=[], decision_seat="N",
            candidate_call="1C",
            ben_bidder=lambda request: {
                "bid": "PASS",
                "candidates": [{"call": "PASS"}],
                "request_sha256": ben_request_sha256(request),
            },
        )

    with pytest.raises(AuctionRolloutError, match="finite candidate score"):
        rollout_worlds(
            worlds=[world], dealer="N", auction=[], decision_seat="N",
            candidate_call="1C",
            ben_bidder=lambda request: _ben_response(request, "PASS", True),
        )

    with pytest.raises(AuctionRolloutError, match="not bound"):
        rollout_worlds(
            worlds=[world], dealer="N", auction=[], decision_seat="N",
            candidate_call="1C",
            ben_bidder=lambda _request: {
                "bid": "PASS",
                "candidates": [{"call": "PASS", "insta_score": 1.0}],
                "request_sha256": "0" * 64,
            },
        )

    with pytest.raises(AuctionRolloutError, match="duplicate"):
        rollout_worlds(
            worlds=[world, dict(world)], dealer="N", auction=[], decision_seat="N",
            candidate_call="PASS",
            ben_bidder=lambda request: _ben_response(request, "PASS"),
        )
