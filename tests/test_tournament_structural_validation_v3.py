from __future__ import annotations

import copy
import json
from pathlib import Path

from bridge_school_api.tournament_structural_validation_v3 import (
    expected_dealer,
    expected_vulnerability,
    opening_leader,
    validate_tournament_structure,
)


FACTS = Path("data/tournaments/tournament_30041_round2_diana_facts_v1.json")


def _source():
    return json.loads(FACTS.read_text(encoding="utf-8"))


def _mutate_row(source, board_number: int, **updates):
    columns = list(source["columns"])
    for index, raw in enumerate(source["rows"]):
        values = raw.split("|")
        row = dict(zip(columns, values, strict=True))
        if int(row["board"]) == board_number:
            row.update({key: str(value) for key, value in updates.items()})
            source["rows"][index] = "|".join(row[column] for column in columns)
            return
    raise AssertionError(f"board {board_number} not found")


def test_standard_duplicate_cycles_and_opening_leader():
    assert [expected_dealer(x) for x in range(1, 9)] == ["N", "E", "S", "W", "N", "E", "S", "W"]
    assert [expected_vulnerability(x) for x in range(1, 17)] == [
        "NONE", "NS", "EW", "BOTH", "NS", "EW", "BOTH", "NONE",
        "EW", "BOTH", "NONE", "NS", "BOTH", "NONE", "NS", "EW",
    ]
    assert expected_vulnerability(17) == "NONE"
    assert opening_leader("N") == "E"
    assert opening_leader("E") == "S"
    assert opening_leader("S") == "W"
    assert opening_leader("W") == "N"


def test_real_30041_passes_full_structural_gate():
    report = validate_tournament_structure(_source())
    assert report["schema"] == "tournament-structural-validation-v1"
    assert report["board_count"] == 24
    assert report["coverage"]["complete"] is True
    assert report["status_counts"] == {"average": 1, "played": 21, "unplayed": 2}
    assert report["dealer_cycle_pass"] is True
    assert report["vulnerability_cycle_pass"] is True
    assert report["hands_13x4_pass"] is True
    assert report["cards_52_unique_pass"] is True
    assert report["opening_leads_checked"] == 21
    assert report["opening_leads_legal"] == 21
    assert report["status_consistency_pass"] is True
    assert report["all_structural_checks_pass"] is True
    assert all(item["passes"] for item in report["checks"])


def test_wrong_dealer_fails_closed():
    source = _source()
    _mutate_row(source, 1, dealer="E")
    report = validate_tournament_structure(source)
    assert report["dealer_cycle_pass"] is False
    board = next(x for x in report["checks"] if x["board_number"] == 1)
    assert "DEALER_CYCLE_MISMATCH" in board["errors"]
    assert report["all_structural_checks_pass"] is False


def test_wrong_vulnerability_fails_closed():
    source = _source()
    _mutate_row(source, 2, vulnerability="None")
    report = validate_tournament_structure(source)
    assert report["vulnerability_cycle_pass"] is False
    board = next(x for x in report["checks"] if x["board_number"] == 2)
    assert "VULNERABILITY_CYCLE_MISMATCH" in board["errors"]
    assert report["all_structural_checks_pass"] is False


def test_opening_lead_must_belong_to_left_hand_opponent():
    source = _source()
    # Board 2: declarer S, so W must lead. AS is not in West's hand.
    _mutate_row(source, 2, opening_lead="SA")
    report = validate_tournament_structure(source)
    board = next(x for x in report["checks"] if x["board_number"] == 2)
    assert board["opening_lead"]["leader"] == "W"
    assert board["opening_lead"]["legal"] is False
    assert "OPENING_LEAD_NOT_IN_LEADER_HAND" in board["errors"]
    assert report["opening_leads_legal"] == 20
    assert report["all_structural_checks_pass"] is False


def test_nonplayed_row_cannot_carry_contract_result_fields():
    source = _source()
    _mutate_row(source, 1, contract="1NT", declarer="N", result_delta="0", opening_lead="S2", pair_score="90")
    report = validate_tournament_structure(source)
    board = next(x for x in report["checks"] if x["board_number"] == 1)
    assert board["status_consistent"] is False
    assert any(x.startswith("NONPLAYED_CONTRACT_FIELDS_PRESENT:") for x in board["errors"])
    assert report["status_consistency_pass"] is False


def test_52_card_integrity_is_not_repaired():
    source = _source()
    columns = list(source["columns"])
    rows = []
    for raw in source["rows"]:
        values = raw.split("|")
        row = dict(zip(columns, values, strict=True))
        if int(row["board"]) == 2:
            row["N"] = row["E"]
        rows.append("|".join(row[column] for column in columns))
    source["rows"] = rows
    report = validate_tournament_structure(source)
    board = next(x for x in report["checks"] if x["board_number"] == 2)
    assert board["hands_13_each"] is True
    assert board["cards_52_unique"] is False
    assert "DEAL_NOT_52_UNIQUE_CARDS" in board["errors"]
    assert report["cards_52_unique_pass"] is False


def test_coverage_manifest_detects_missing_board():
    source = copy.deepcopy(_source())
    columns = list(source["columns"])
    board_index = columns.index("board")
    source["rows"] = [raw for raw in source["rows"] if int(raw.split("|")[board_index]) != 7]
    report = validate_tournament_structure(source)
    assert report["coverage"]["complete"] is False
    assert report["coverage"]["missing_boards"] == [7]
    assert report["all_structural_checks_pass"] is False
