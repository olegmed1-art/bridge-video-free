from __future__ import annotations

import pytest

from oracle_autopilot.contract import AutopilotContractError
from oracle_autopilot.ibf_board_structured import (
    build_structured_tournament_artifact,
    extract_structured_board,
)

BOARD_HTML = """
<html><body>
<table class='deal' dir='LTR'><tr>
  <td class='boardnum'>1</td>
  <td>&spades; 9854<br>&hearts; Q952<br>&diams; 7<br>&clubs; J964</td><td>&nbsp;</td></tr>
<tr><td>&spades; <br>&hearts; A6<br>&diams; AK852<br>&clubs; AKQ532</td>
  <td class='boardnum'><img src='deal01.png'></td>
  <td>&spades; AKJT632<br>&hearts; JT74<br>&diams; 93<br>&clubs; </td></tr>
<tr><td>&nbsp;</td><td>&spades; Q7<br>&hearts; K83<br>&diams; QJT64<br>&clubs; T87</td>
  <td>&nbsp;</td></tr></table>
<a href='https://dds.bridgewebs.com/bsol2/ddummy.htm?club=il_iba&amp;board=1&amp;dealer=N&amp;vul=None&amp;north=9854.Q952.7.J964&amp;east=AKJT632.JT74.93.&amp;south=Q7.K83.QJT64.T87&amp;west=.A6.AK852.AKQ532'>
<table class='dd'>
<tr><td>&nbsp;</td><td>NT</td><td>&spades;</td><td>&hearts;</td><td>&diams;</td><td>&clubs;</td></tr>
<tr><td>N</td><td>0</td><td>0</td><td>3</td><td>2</td><td>3</td></tr>
<tr><td>S</td><td>4</td><td>0</td><td>3</td><td>2</td><td>4</td></tr>
<tr><td>E</td><td>9</td><td>13</td><td>10</td><td>11</td><td>9</td></tr>
<tr><td>W</td><td>9</td><td>13</td><td>10</td><td>11</td><td>9</td>
<tr><td colspan='6'>Par: -1510</td></tr></table></a>
<table class='resultsTable'>
<tr><th>EW</th><th>EW%</th><th colspan='3'>result</th><th>lead</th><th>contract</th><th>NS%</th><th>NS</th></tr>
<tr class='bold'>
<td><a href='personal.php?event=29692&amp;round=9&amp;seat=4&amp;ibf=15031'>target</a></td>
<td>0.00</td><td>&nbsp;</td><td>&nbsp;</td><td>50</td><td>&diams;7</td><td>6&clubs;-1 [W]</td><td>100.00</td>
<td><a href='personal.php?event=29692&amp;round=9&amp;seat=5'>opponent</a></td></tr>
<tr><td><a href='personal.php?event=29692&amp;round=9&amp;seat=2'>pair</a></td>
<td>33.33</td><td>&nbsp;</td><td>-450</td><td>&nbsp;</td><td>&diams;Q</td><td>4&spades;+1 [E]</td><td>66.67</td>
<td><a href='personal.php?event=29692&amp;round=9&amp;seat=6'>pair</a></td></tr>
</table>
</body></html>
"""


def test_extracts_complete_deidentified_source_facts_without_error_attribution():
    result = extract_structured_board(
        BOARD_HTML, expected_board_number=1, target_seat="4"
    )

    assert result["hands"]["N"] == {"S": "9854", "H": "Q952", "D": "7", "C": "J964"}
    assert (
        sum(len(cards) for hand in result["hands"].values() for cards in hand.values())
        == 52
    )
    assert result["dealer"] == "N"
    assert result["vulnerability"] == "None"
    assert result["double_dummy_tricks"]["W"]["S"] == 13
    assert result["par_score"] == -1510
    assert len(result["field_results"]) == 2
    assert result["field_results"][0]["ew_seat"] == "4"
    assert result["field_results"][0]["ns_seat"] == "5"
    assert result["target_result"] == {
        "side": "EW",
        "percentage": 0.0,
        "opening_lead": "♦7",
        "contract": "6♣-1 [W]",
        "player_error_demonstrated": False,
    }
    assert result["observability"]["bidding"] == "UNOBSERVABLE_NO_AUCTION"
    assert result["observability"]["defense"] == "UNOBSERVABLE_NO_PLAY_RECORD"
    assert len(result["board_page_sha256"]) == 64
    assert len(result["dds_source_url_sha256"]) == 64

    def string_values(value):
        if isinstance(value, dict):
            for nested in value.values():
                yield from string_values(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from string_values(nested)
        elif isinstance(value, str):
            yield value

    retained_strings = set(string_values(result))
    assert "target" not in retained_strings
    assert "opponent" not in retained_strings


@pytest.mark.parametrize(
    ("old", "new", "code"),
    [
        (
            "<td class='boardnum'>1</td>",
            "<td class='boardnum'>2</td>",
            "IBF_BOARD_NUMBER_MISMATCH",
        ),
        ("&clubs; J964", "&clubs; J965", "IBF_BOARD_DEAL_INTEGRITY_INVALID"),
        (
            "<td>100.00</td>",
            "<td>99.00</td>",
            "IBF_BOARD_FIELD_PERCENTAGE_PAIR_INVALID",
        ),
        (
            "seat=4&amp;ibf=15031",
            "seat=44&amp;ibf=15031",
            "IBF_BOARD_FIELD_TARGET_MISSING",
        ),
        (
            "<td>13</td><td>10</td><td>11</td><td>9</td>",
            "<td>14</td><td>10</td><td>11</td><td>9</td>",
            "IBF_BOARD_DD_VALUE_INVALID",
        ),
    ],
)
def test_structural_corruption_fails_closed(old, new, code):
    changed = BOARD_HTML.replace(old, new, 1)
    with pytest.raises(AutopilotContractError, match=code):
        extract_structured_board(changed, expected_board_number=1, target_seat="4")


def test_missing_target_or_duplicate_target_fails_closed():
    missing = BOARD_HTML.replace("seat=4&amp;ibf=15031", "seat=40&amp;ibf=15031", 1)
    with pytest.raises(AutopilotContractError, match="IBF_BOARD_FIELD_TARGET_MISSING"):
        extract_structured_board(missing, expected_board_number=1, target_seat="4")

    duplicate = BOARD_HTML.replace("seat=2'>pair", "seat=4'>pair", 1)
    with pytest.raises(
        AutopilotContractError, match="IBF_BOARD_FIELD_TARGET_DUPLICATE"
    ):
        extract_structured_board(duplicate, expected_board_number=1, target_seat="4")


def test_adjusted_field_row_is_retained_without_inventing_a_contract_or_score():
    adjusted_row = """
    <tr><td><a href='personal.php?event=29692&amp;round=9&amp;seat=7'>pair</a></td>
    <td>60.00</td><td>&nbsp;</td><td colspan='2'>AP</td><td>&nbsp;</td><td>&nbsp;</td><td>60.00</td>
    <td><a href='personal.php?event=29692&amp;round=9&amp;seat=8'>pair</a></td></tr>
    """
    changed = BOARD_HTML.replace(
        "</table>\n</body>", adjusted_row + "</table>\n</body>"
    )
    result = extract_structured_board(changed, expected_board_number=1, target_seat="4")

    adjusted = result["field_results"][-1]
    assert adjusted["adjustment"] == "AP"
    assert adjusted["ew_percentage"] == adjusted["ns_percentage"] == 60.0
    assert adjusted["ew_score_cell"] is None
    assert adjusted["ns_score_cell"] is None
    assert adjusted["contract"] is None


def test_structured_tournament_join_proves_source_identity_and_ranks_without_attribution():
    board = extract_structured_board(
        BOARD_HTML, expected_board_number=1, target_seat="4"
    )
    snapshot = {
        "source_authority": "ISRAEL_BRIDGE_FEDERATION_OFFICIAL_RESULTS",
        "ibf_player_id": "15031",
        "latest_participation": {
            "date": "2026-08-27",
            "event_id": 29692,
            "round_id": 9,
            "seat": "4",
        },
        "board_count": 1,
        "boards": [
            {
                "board_number": 1,
                "percentage_token": "0.00",
                "field_row_count": 2,
                "field_page_sha256": board["board_page_sha256"],
            }
        ],
    }

    artifact = build_structured_tournament_artifact(snapshot, [board])

    assert artifact["schema_version"] == "IBF_STRUCTURED_TOURNAMENT_V1"
    assert artifact["board_count"] == 1
    assert artifact["teaching_analysis"]["review_order"] == [
        {"board_number": 1, "percentage": 0.0, "player_error_demonstrated": False}
    ]
    assert artifact["teaching_analysis"]["methodology_or_canon_applied"] is False
    assert artifact["teaching_analysis"]["missing_source_dimensions"] == [
        "AUCTION",
        "PLAY_RECORD",
    ]


def test_structured_tournament_join_rejects_source_drift():
    board = extract_structured_board(
        BOARD_HTML, expected_board_number=1, target_seat="4"
    )
    snapshot = {
        "latest_participation": {},
        "board_count": 1,
        "boards": [
            {
                "board_number": 1,
                "percentage_token": "0.00",
                "field_row_count": 2,
                "field_page_sha256": "0" * 64,
            }
        ],
    }
    with pytest.raises(AutopilotContractError, match="IBF_STRUCTURED_SOURCE_DRIFT"):
        build_structured_tournament_artifact(snapshot, [board])
