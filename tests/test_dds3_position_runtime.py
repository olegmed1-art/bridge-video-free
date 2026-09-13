from bridge_school_api.dds3.position_runtime import solve_position_all_moves, solve_position_trajectory


class FakeWorker:
    def __init__(self, responses):
        self.responses = iter(responses)

    def call(self, position):
        return next(self.responses)


def raw_response(best_rows, *, tricks_remaining=13, request_seq=1, nodes=100, tt_before=False, same_tt=False):
    return {
        "ok": True,
        "engine": "DDS3",
        "fallback_used": False,
        "operation": "position_all_moves",
        "request_seq": request_seq,
        "tricks_remaining": tricks_remaining,
        "nodes": nodes,
        "tt_present_before": tt_before,
        "tt_present_after": True,
        "same_tt_instance": same_tt,
        "moves": [
            {
                "card": card,
                "tricks_for_side_to_play": tricks,
                "equivalent": equivalent,
                "representative": representative or card,
            }
            for card, tricks, equivalent, representative in best_rows
        ],
    }


def test_all_moves_preserves_equal_optima_and_regret():
    worker = FakeWorker(
        [
            raw_response(
                [
                    ("SA", 8, False, None),
                    ("SK", 8, True, "SA"),
                    ("H2", 7, False, None),
                    ("D2", 5, False, None),
                ]
            )
        ]
    )
    result = solve_position_all_moves({"pbn": "unused", "trump": "NT", "first": "N"}, worker=worker)
    assert result["engine"] == "DDS3"
    assert result["fallback_used"] is False
    assert result["best_tricks"] == 8
    assert result["optimal_cards"] == ["SA", "SK"]
    by_card = {item["card"]: item for item in result["moves"]}
    assert by_card["SA"]["regret_class"] == "0"
    assert by_card["SK"]["optimal"] is True
    assert by_card["SK"]["equivalent"] is True
    assert by_card["SK"]["representative"] == "SA"
    assert by_card["H2"]["regret"] == 1
    assert by_card["D2"]["regret_class"] == "2+"


def test_trajectory_uses_fixed_partnership_perspective_and_reports_damage():
    worker = FakeWorker(
        [
            raw_response([("SA", 7, False, None)], request_seq=1),
            raw_response([("H2", 7, False, None)], request_seq=2, tt_before=True, same_tt=True),
            raw_response([("D2", 6, False, None)], tricks_remaining=12, request_seq=3, tt_before=True, same_tt=True),
        ]
    )
    positions = [
        {"pbn": "unused", "trump": "NT", "first": "N", "perspective_tricks_won": 0},
        {"pbn": "unused", "trump": "NT", "first": "E", "perspective_tricks_won": 0},
        {"pbn": "unused", "trump": "NT", "first": "E", "perspective_tricks_won": 0},
    ]
    result = solve_position_trajectory(positions, perspective="NS", worker=worker)
    # V0 = 7 for NS. At V1 the side to play is EW and can make 7/13, so NS can make 6.
    # At V2 EW can make 6/12, so NS can still make 6.
    assert result["values"] == [7, 6, 6]
    assert result["first_swing"] == {"after_play": 1, "from": 7, "to": 6, "delta": -1}
    assert result["first_loss"] == result["first_swing"]
    assert result["unrecovered_damage"] == 1
    assert result["final_delta"] == -1
    assert result["engine"] == "DDS3"
    assert result["fallback_used"] is False
