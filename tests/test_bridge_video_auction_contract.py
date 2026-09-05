import pytest

from bridge_contracts.video_auction import VideoAuctionContractError, validate_auction_prefix


def test_dealer_order_contract_and_declarer_are_mechanical():
    result = validate_auction_prefix(["1H", "PASS", "2H", "PASS", "4H", "PASS", "PASS", "PASS"], dealer="E")
    assert [item["seat"] for item in result["history"][:4]] == ["E", "S", "W", "N"]
    assert result["contract"] == "4H"
    assert result["declarer"] == "E"
    assert result["termination"] == "CONTRACT"


def test_passout_and_double_redouble_are_legal_when_sides_match():
    assert validate_auction_prefix(["PASS"] * 4, dealer="N")["termination"] == "PASSOUT"
    result = validate_auction_prefix(["1S", "X", "XX", "PASS", "PASS", "PASS"], dealer="N")
    assert result["contract"] == "1SXX"
    assert result["declarer"] == "N"


@pytest.mark.parametrize("calls", [
    ["X"], ["1S", "XX"], ["1S", "PASS", "1H"],
    ["PASS", "PASS", "PASS", "PASS", "1C"],
])
def test_illegal_or_post_termination_calls_fail_closed(calls):
    with pytest.raises(VideoAuctionContractError):
        validate_auction_prefix(calls, dealer="N")
