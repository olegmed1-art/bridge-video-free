from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from oracle_autopilot.contract import AutopilotContractError, ClaimedTask, validate_task_contract
from oracle_autopilot.ibf_read_only import (
    IBF_INDEX_URL,
    IBF_MAX_BOARD_PAGES,
    IBF_MEMBER_URL,
    IBF_ROW_EXCERPT_LIMIT,
    IBF_SOURCE_AUTHORITY,
    _canonical_session_url,
    _canonical_total_url,
    _validate_official_url,
    fetch_ibf_read_only_snapshot,
)


def _task(**overrides):
    values = {
        "task_id": "00000000-0000-0000-0000-000000001013",
        "goal_type": "IBF_READ_ONLY_ANALYSIS",
        "goal_json": {
            "approval_ref": "github:issue:1013#director-go",
            "ibf_player_id": "15031",
            "source_authority": IBF_SOURCE_AUTHORITY,
        },
        "current_step_key": "ibf.read_only_analysis",
        "step_cursor": 0,
        "lease_epoch": 1,
        "attempts": 1,
        "max_attempts": 3,
        "cost_cap_microusd": 0,
        "cost_reserved_microusd": 0,
    }
    values.update(overrides)
    return ClaimedTask(**values)


def test_ibf_contract_is_exact_and_zero_cost():
    validate_task_contract(_task())

    for bad_goal in (
        {**_task().goal_json, "url": "https://example.com"},
        {**_task().goal_json, "ibf_player_id": "15A31"},
        {**_task().goal_json, "source_authority": "SEARCH_ENGINE"},
        {**_task().goal_json, "approval_ref": "https://evil.example/?x=1&y=2"},
    ):
        with pytest.raises(AutopilotContractError):
            validate_task_contract(_task(goal_json=bad_goal))

    with pytest.raises(AutopilotContractError, match="AUTOPILOT_IBF_COST_INVALID"):
        validate_task_contract(_task(cost_cap_microusd=1))
    with pytest.raises(AutopilotContractError, match="AUTOPILOT_IBF_STATE_INVALID"):
        validate_task_contract(_task(current_step_key="shadow.wait"))


def test_ibf_url_boundary_rejects_non_official_and_credentials():
    assert IBF_INDEX_URL == "https://main.bridge.co.il/results/"
    assert IBF_MEMBER_URL == "https://bridge.co.il/viewer/membermplist.php?id={player_id}"
    _validate_official_url(IBF_INDEX_URL)
    _validate_official_url("https://bridge.co.il/viewer/session.php?event=30041&round=3")
    _validate_official_url("https://bridge.co.il/viewer/total.php?event=29688&ibf=15031")
    assert _canonical_session_url(
        "http://www.bridge.co.il/viewer//session.php?event=30041&ibf=15031&round=3",
        IBF_MEMBER_URL.format(player_id="15031"),
    ) == ("https://bridge.co.il/viewer/session.php?event=30041&round=3", 30041, 3)
    assert _canonical_total_url(
        "http://www.bridge.co.il/viewer//total.php?event=29688&ibf=15031",
        IBF_MEMBER_URL.format(player_id="15031"),
        "15031",
    ) == ("https://bridge.co.il/viewer/total.php?event=29688&ibf=15031", 29688)
    assert (
        _canonical_total_url(
            "https://bridge.co.il/viewer/total.php?event=29688&ibf=15032",
            IBF_MEMBER_URL.format(player_id="15031"),
            "15031",
        )
        is None
    )

    for url in (
        "https://example.com/viewer/session.php?event=30041&round=3",
        "https://user:pass@www.bridge.co.il/viewer/session.php?event=30041&round=3",
        "http://www.bridge.co.il/viewer/session.php?event=30041&round=3",
        "https://bridge.co.il/../../etc/passwd",
    ):
        with pytest.raises(AutopilotContractError):
            _validate_official_url(url)


def test_snapshot_selects_latest_actual_participation_and_verifies_field_pages():
    member_url = IBF_MEMBER_URL.format(player_id="15031")
    total = "https://bridge.co.il/viewer/total.php?event=29688&ibf=15031"
    session_latest = "https://bridge.co.il/viewer/session.php?event=29688&round=4"
    personal_latest = "https://bridge.co.il/viewer/personal.php?event=29688&round=4&seat=106"
    board_latest = "https://bridge.co.il/viewer/board.php?board=1&event=29688&ibf=15031&round=4&seat=106"
    session4 = "https://bridge.co.il/viewer/session.php?event=30041&round=4"
    session3 = "https://bridge.co.il/viewer/session.php?event=30041&round=3"
    personal3 = "https://bridge.co.il/viewer/personal.php?event=30041&round=3&seat=7"
    board1 = "https://bridge.co.il/viewer/board.php?board=1&event=30041&ibf=15031&round=3&seat=7"
    board2 = "https://bridge.co.il/viewer/board.php?board=2&event=30041&ibf=15031&round=3&seat=7"

    pages = {
        member_url: """
            <html><body>member 15031
            <a href='http://www.bridge.co.il/viewer//total.php?event=29688&ibf=15031'>30 Aug total</a>
            <a href='/viewer//session.php?event=29912&ibf=15031&round=4'>older</a>
            </body></html>
        """,
        total: """
            <html><body>event total 30/08/26
            <a href='http://www.bridge.co.il/viewer//session.php?event=29688&ibf=15031&round=4'>played</a>
            </body></html>
        """,
        IBF_INDEX_URL: """
            <html><body>
            <a href='http://www.bridge.co.il/viewer//session.php?event=30041&ibf=15031&round=4'>29 Aug</a>
            <a href='http://www.bridge.co.il/viewer//session.php?event=30041&ibf=15031&round=3'>22 Aug</a>
            </body></html>
        """,
        session4: """
            <html><body>Session 4 date 29/08/26 Director 15031
            <table><tr><td>Other Pair</td><td>1215 2222</td></tr></table>
            </body></html>
        """,
        session_latest: """
            <html><body>Session 4 date 30/08/26
            <table><tr>
              <td><a href='http://www.bridge.co.il/viewer//personal.php?event=29688&ibf=15031&round=4&seat=106'>Oleg - Partner</a></td>
              <td>15031 49999</td><td>59.03</td>
            </tr></table></body></html>
        """,
        personal_latest: """
            <html><body>personal 15031
            <table>
              <tr><th>Board</th><th>Dir</th><th>NS</th><th>EW</th><th>%</th><th>Lead</th><th>Contract</th><th>Opponents</th></tr>
              <tr><td><a href='http://www.bridge.co.il/viewer//board.php?board=1&event=29688&ibf=15031&round=4&seat=106'>1</a></td><td>NS</td><td>-450</td><td></td><td>33.33</td><td>DT</td><td>4S+1 E</td><td>Pair 1</td></tr>
            </table></body></html>
        """,
        board_latest: """
            <html><body><table>
              <tr><th>Pair</th><th>Contract</th><th>Score</th><th>Result</th></tr>
              <tr><td>1</td><td>4S E</td><td>-420</td><td>60.00</td></tr>
              <tr><td>2</td><td>4S+1 E</td><td>-450</td><td>33.33</td></tr>
            </table></body></html>
        """,
        session3: """
            <html><body>Session 3 date 22/08/26
            <table><tr>
              <td><a href='http://www.bridge.co.il/viewer//personal.php?event=30041&ibf=15031&round=3&seat=7'>Oleg - Partner</a></td>
              <td>15031 42137</td><td>60.45</td>
            </tr></table></body></html>
        """,
        personal3: """
            <html><body>personal 15031
            <table>
              <tr><th>Board</th><th>Dir</th><th>Score</th><th>%</th><th>Lead</th><th>Contract</th></tr>
              <tr><td><a href='http://www.bridge.co.il/viewer//board.php?board=1&event=30041&ibf=15031&round=3&seat=7'>1</a></td><td>NS</td><td>-450</td><td>33.33</td><td>DT</td><td>4S+1 E</td></tr>
              <tr><td><a href='http://www.bridge.co.il/viewer//board.php?board=2&event=30041&ibf=15031&round=3&seat=7'>2</a></td><td>EW</td><td>420</td><td>75.00</td><td>C2</td><td>4H S</td></tr>
            </table></body></html>
        """,
        board1: """
            <html><body><table>
              <tr><th>Pair</th><th>Contract</th><th>Score</th><th>Result</th></tr>
              <tr><td>1</td><td>4S E</td><td>-420</td><td>60.00</td></tr>
              <tr><td>2</td><td>4S+1 E</td><td>-450</td><td>33.33</td></tr>
            </table></body></html>
        """,
        board2: """
            <html><body><table>
              <tr><th>Pair</th><th>Contract</th><th>Score</th><th>Result</th></tr>
              <tr><td>1</td><td>4H S</td><td>420</td><td>75.00</td></tr>
              <tr><td>2</td><td>3NT S</td><td>400</td><td>50.00</td></tr>
            </table></body></html>
        """,
        "https://bridge.co.il/viewer/session.php?event=29912&round=4": """
            <html><body>date 05/08/26<table><tr><td>old</td><td>99999</td></tr></table></body></html>
        """,
    }

    def fake_get(url, budget):
        budget.consume()
        return pages[url]

    with patch("oracle_autopilot.ibf_read_only._ibf_get_html", side_effect=fake_get):
        result = fetch_ibf_read_only_snapshot(_task().goal_json)

    assert result["latest_participation"] == {
        "date": "2026-08-30",
        "event_id": 29688,
        "round_id": 4,
        "seat": "106",
        "session_url": session_latest,
        "personal_url": personal_latest,
    }
    assert result["board_count"] == 1
    assert result["boards"][0]["personal_row_excerpt"] == "1 | NS | -450 | 33.33 | DT | 4S+1 E"
    assert result["boards"][0]["score_token"] == "-450"
    assert result["boards"][0]["percentage_token"] == "33.33"
    assert result["boards"][0]["field_row_count"] == 2
    assert len(result["boards"][0]["field_page_sha256"]) == 64
    assert result["production_mutation"] is False
    assert result["model_calls"] == 0
    assert result["analysis_scope"] == "SOURCE_RETRIEVAL_AND_FIELD_EVIDENCE_ONLY"


def test_missing_board_links_fail_closed_instead_of_inventing_data():
    member_url = IBF_MEMBER_URL.format(player_id="15031")
    session = "https://bridge.co.il/viewer/session.php?event=30041&round=3"
    personal = "https://bridge.co.il/viewer/personal.php?event=30041&round=3&seat=7"
    pages = {
        member_url: "member 15031 <a href='/viewer/session.php?event=30041&round=3'>s</a>",
        IBF_INDEX_URL: "<a href='https://bridge.co.il/viewer/session.php?event=30041&round=3'>s</a>",
        session: "22/08/26 <table><tr><td><a href='/viewer/personal.php?event=30041&round=3&seat=7'>P</a></td><td>15031</td></tr></table>",
        personal: "personal 15031 <table><tr><td>1</td><td>NS</td><td>-450</td></tr></table>",
    }

    def fake_get(url, budget):
        budget.consume()
        return pages[url]

    with patch("oracle_autopilot.ibf_read_only._ibf_get_html", side_effect=fake_get):
        with pytest.raises(AutopilotContractError, match="IBF_PERSONAL_BOARD_LINKS_MISSING"):
            fetch_ibf_read_only_snapshot(_task().goal_json)


def test_maximum_pilot_snapshot_stays_below_database_evidence_limit():
    boards = [
        {
            "board_number": number,
            "personal_row_excerpt": "x" * IBF_ROW_EXCERPT_LIMIT,
            "percentage_token": "100.00",
            "score_token": "-12345",
            "field_row_count": 200,
            "field_page_sha256": "f" * 64,
        }
        for number in range(1, IBF_MAX_BOARD_PAGES + 1)
    ]
    snapshot = {
        "source_authority": IBF_SOURCE_AUTHORITY,
        "ibf_player_id": "15031",
        "latest_participation": {
            "date": "2026-08-30",
            "event_id": 29688,
            "round_id": 4,
            "seat": "106",
            "session_url": "https://bridge.co.il/viewer/session.php?event=29688&round=4",
            "personal_url": "https://bridge.co.il/viewer/personal.php?event=29688&round=4&seat=106",
        },
        "board_count": len(boards),
        "boards": boards,
        "member_page_sha256": "a" * 64,
        "results_index_sha256": "b" * 64,
        "session_page_sha256": "c" * 64,
        "personal_page_sha256": "d" * 64,
        "request_count": 96,
        "http_method": "GET",
        "production_mutation": False,
        "model_calls": 0,
        "cost_actual_microusd": 0,
        "analysis_scope": "SOURCE_RETRIEVAL_AND_FIELD_EVIDENCE_ONLY",
    }
    encoded = json.dumps(snapshot, ensure_ascii=False, separators=(", ", ": ")).encode("utf-8")
    assert len(encoded) < 7_600
