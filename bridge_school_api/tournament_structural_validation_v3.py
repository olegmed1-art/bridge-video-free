from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence


class TournamentStructureError(ValueError):
    pass


_RANKS = set("23456789TJQKA")
_SUITS = ("S", "H", "D", "C")
_SEATS = ("N", "E", "S", "W")
_REQUIRED_COLUMNS = {
    "board",
    "dealer",
    "vulnerability",
    "N",
    "E",
    "S",
    "W",
    "pair_direction",
    "status",
    "contract",
    "declarer",
    "result_delta",
    "opening_lead",
    "pair_score",
    "pair_percentage",
}

# Standard duplicate vulnerability cycle for boards 1..16, then repeats.
_VULNERABILITY_CYCLE = (
    "NONE",
    "NS",
    "EW",
    "BOTH",
    "NS",
    "EW",
    "BOTH",
    "NONE",
    "EW",
    "BOTH",
    "NONE",
    "NS",
    "BOTH",
    "NONE",
    "NS",
    "EW",
)


def _normalize_vulnerability(value: str) -> str:
    text = str(value).strip().upper().replace("–", "-").replace("—", "-").replace(" ", "")
    if text in {"NONE", "LOVE", "-", ""}:
        return "NONE"
    if text in {"BOTH", "ALL"}:
        return "BOTH"
    if text in {"NS", "N-S"}:
        return "NS"
    if text in {"EW", "E-W"}:
        return "EW"
    return f"INVALID:{text}"


def expected_dealer(board_number: int) -> str:
    if isinstance(board_number, bool) or int(board_number) <= 0:
        raise TournamentStructureError("board_number must be positive")
    return _SEATS[(int(board_number) - 1) % 4]


def expected_vulnerability(board_number: int) -> str:
    if isinstance(board_number, bool) or int(board_number) <= 0:
        raise TournamentStructureError("board_number must be positive")
    return _VULNERABILITY_CYCLE[(int(board_number) - 1) % 16]


def opening_leader(declarer: str) -> str:
    seat = str(declarer).strip().upper()
    if seat not in _SEATS:
        raise TournamentStructureError(f"invalid declarer seat: {declarer!r}")
    return _SEATS[(_SEATS.index(seat) + 1) % 4]


def _parse_pbn_hand(value: str) -> tuple[str, ...]:
    """Parse one S.H.D.C hand into rank+suit tokens used by the v3 core."""
    text = str(value).strip().upper().replace("10", "T")
    parts = text.split(".")
    if len(parts) != 4:
        raise TournamentStructureError(f"hand must have four S.H.D.C fields: {value!r}")
    cards: list[str] = []
    for suit, ranks_text in zip(_SUITS, parts, strict=True):
        ranks_text = "" if ranks_text == "-" else ranks_text
        for rank in ranks_text:
            if rank not in _RANKS:
                raise TournamentStructureError(f"invalid rank {rank!r} in hand {value!r}")
            cards.append(rank + suit)
    return tuple(cards)


def _normalize_opening_lead(value: str) -> str:
    """Convert source suit+rank (e.g. S2, DT) to rank+suit (2S, TD)."""
    text = str(value).strip().upper().replace("10", "T").replace(" ", "")
    if len(text) != 2 or text[0] not in _SUITS or text[1] not in _RANKS:
        raise TournamentStructureError(f"invalid opening lead: {value!r}")
    return text[1] + text[0]


def _rows_from_facts(source: Mapping[str, Any]) -> list[dict[str, str]]:
    if source.get("schema") != "bridge-tournament-facts-v1":
        raise TournamentStructureError("unsupported tournament facts schema")
    columns = source.get("columns")
    rows = source.get("rows")
    if not isinstance(columns, Sequence) or isinstance(columns, (str, bytes)):
        raise TournamentStructureError("facts columns are malformed")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise TournamentStructureError("facts rows are malformed")
    names = [str(x) for x in columns]
    if len(set(names)) != len(names):
        raise TournamentStructureError("duplicate column names")
    missing = sorted(_REQUIRED_COLUMNS - set(names))
    if missing:
        raise TournamentStructureError(f"missing required columns: {missing}")

    parsed: list[dict[str, str]] = []
    for raw in rows:
        if not isinstance(raw, str):
            raise TournamentStructureError("facts row must be pipe-delimited text")
        values = raw.split("|")
        if len(values) != len(names):
            raise TournamentStructureError(
                f"row has {len(values)} fields, expected {len(names)}: {raw!r}"
            )
        parsed.append(dict(zip(names, values, strict=True)))
    return parsed


def _nonempty(row: Mapping[str, str], key: str) -> bool:
    return bool(str(row.get(key, "")).strip())


def validate_tournament_structure(source: Mapping[str, Any]) -> dict[str, Any]:
    rows = _rows_from_facts(source)
    if not rows:
        raise TournamentStructureError("tournament facts contain no rows")

    board_numbers: list[int] = []
    for row in rows:
        try:
            board = int(row["board"])
        except (TypeError, ValueError) as exc:
            raise TournamentStructureError(f"invalid board number: {row.get('board')!r}") from exc
        if board <= 0:
            raise TournamentStructureError("board number must be positive")
        board_numbers.append(board)

    counts = Counter(board_numbers)
    duplicate_boards = sorted(board for board, count in counts.items() if count > 1)
    max_board = max(board_numbers)
    expected_boards = list(range(1, max_board + 1))
    present_set = set(board_numbers)
    missing_boards = [board for board in expected_boards if board not in present_set]

    checks: list[dict[str, Any]] = []
    dealer_cycle_pass = True
    vulnerability_cycle_pass = True
    hands_13x4_pass = True
    cards_52_unique_pass = True
    opening_leads_checked = 0
    opening_leads_legal = 0
    status_consistency_pass = True

    for row in rows:
        board = int(row["board"])
        errors: list[str] = []

        published_dealer = str(row["dealer"]).strip().upper()
        dealer_expected = expected_dealer(board)
        dealer_matches = published_dealer == dealer_expected
        if not dealer_matches:
            dealer_cycle_pass = False
            errors.append("DEALER_CYCLE_MISMATCH")

        published_vulnerability = _normalize_vulnerability(row["vulnerability"])
        vulnerability_expected = expected_vulnerability(board)
        vulnerability_matches = published_vulnerability == vulnerability_expected
        if not vulnerability_matches:
            vulnerability_cycle_pass = False
            errors.append("VULNERABILITY_CYCLE_MISMATCH")

        hand_cards: dict[str, tuple[str, ...]] = {}
        hand_sizes: dict[str, int | None] = {}
        hand_parse_error = False
        for seat in _SEATS:
            try:
                cards = _parse_pbn_hand(row[seat])
                hand_cards[seat] = cards
                hand_sizes[seat] = len(cards)
            except TournamentStructureError:
                hand_parse_error = True
                hand_sizes[seat] = None
        sizes_ok = not hand_parse_error and all(hand_sizes[seat] == 13 for seat in _SEATS)
        if not sizes_ok:
            hands_13x4_pass = False
            errors.append("HAND_SIZE_OR_FORMAT_INVALID")

        unique_52 = False
        if not hand_parse_error:
            all_cards = [card for seat in _SEATS for card in hand_cards[seat]]
            unique_52 = len(all_cards) == 52 and len(set(all_cards)) == 52
        if not unique_52:
            cards_52_unique_pass = False
            errors.append("DEAL_NOT_52_UNIQUE_CARDS")

        status = str(row["status"]).strip().lower()
        status_errors: list[str] = []
        lead_info: dict[str, Any] = {
            "applicable": False,
            "present": _nonempty(row, "opening_lead"),
            "leader": None,
            "source_token": str(row.get("opening_lead", "")),
            "card": None,
            "in_leader_hand": None,
            "legal": None,
        }

        if status == "played":
            required_played = (
                "pair_direction",
                "contract",
                "declarer",
                "result_delta",
                "opening_lead",
                "pair_score",
                "pair_percentage",
            )
            missing_played = [key for key in required_played if not _nonempty(row, key)]
            if missing_played:
                status_errors.append("PLAYED_REQUIRED_FIELDS_MISSING:" + ",".join(missing_played))

            if _nonempty(row, "declarer") and _nonempty(row, "opening_lead"):
                opening_leads_checked += 1
                lead_info["applicable"] = True
                try:
                    leader = opening_leader(row["declarer"])
                    lead_card = _normalize_opening_lead(row["opening_lead"])
                    in_hand = bool(not hand_parse_error and lead_card in hand_cards.get(leader, ()))
                    lead_info.update(
                        {
                            "leader": leader,
                            "card": lead_card,
                            "in_leader_hand": in_hand,
                            "legal": in_hand,
                        }
                    )
                    if in_hand:
                        opening_leads_legal += 1
                    else:
                        errors.append("OPENING_LEAD_NOT_IN_LEADER_HAND")
                except TournamentStructureError:
                    lead_info["legal"] = False
                    errors.append("OPENING_LEAD_INVALID")
            elif _nonempty(row, "opening_lead"):
                errors.append("OPENING_LEAD_WITHOUT_VALID_DECLARER")
        elif status in {"average", "unplayed"}:
            forbidden_contract_fields = (
                "contract",
                "declarer",
                "result_delta",
                "opening_lead",
                "pair_score",
            )
            present_forbidden = [key for key in forbidden_contract_fields if _nonempty(row, key)]
            if present_forbidden:
                status_errors.append(
                    "NONPLAYED_CONTRACT_FIELDS_PRESENT:" + ",".join(present_forbidden)
                )
        else:
            status_errors.append(f"UNSUPPORTED_STATUS:{status}")

        if status_errors:
            status_consistency_pass = False
            errors.extend(status_errors)

        checks.append(
            {
                "board_number": board,
                "status": status,
                "hand_sizes": hand_sizes,
                "hands_13_each": sizes_ok,
                "cards_52_unique": unique_52,
                "dealer": {
                    "published": published_dealer,
                    "expected": dealer_expected,
                    "matches": dealer_matches,
                },
                "vulnerability": {
                    "published": published_vulnerability,
                    "expected": vulnerability_expected,
                    "matches": vulnerability_matches,
                },
                "opening_lead": lead_info,
                "status_consistent": not status_errors,
                "errors": errors,
                "passes": not errors,
            }
        )

    status_counts = Counter(str(row["status"]).strip().lower() for row in rows)
    coverage_complete = not duplicate_boards and not missing_boards and len(board_numbers) == max_board
    all_pass = bool(
        coverage_complete
        and dealer_cycle_pass
        and vulnerability_cycle_pass
        and hands_13x4_pass
        and cards_52_unique_pass
        and status_consistency_pass
        and opening_leads_checked == opening_leads_legal
        and all(item["passes"] for item in checks)
    )

    return {
        "schema": "tournament-structural-validation-v1",
        "board_count": len(rows),
        "coverage": {
            "expected_boards": expected_boards,
            "present_boards": sorted(board_numbers),
            "missing_boards": missing_boards,
            "duplicate_boards": duplicate_boards,
            "complete": coverage_complete,
        },
        "status_counts": dict(sorted(status_counts.items())),
        "dealer_cycle_pass": dealer_cycle_pass,
        "vulnerability_cycle_pass": vulnerability_cycle_pass,
        "hands_13x4_pass": hands_13x4_pass,
        "cards_52_unique_pass": cards_52_unique_pass,
        "opening_leads_checked": opening_leads_checked,
        "opening_leads_legal": opening_leads_legal,
        "status_consistency_pass": status_consistency_pass,
        "source_conflict_gate_pass": all_pass,
        "all_structural_checks_pass": all_pass,
        "checks": checks,
    }
