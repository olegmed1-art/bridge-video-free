"""Strict, de-identified extraction of one official IBF board page.

The extractor retains source facts only.  It does not infer an auction, a play
record, or responsibility for a poor matchpoint score.  Pair names are not
retained; the official ``seat`` identifiers are sufficient to correlate the
target row and make the field comparison reproducible.
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from .contract import AutopilotContractError

_SUITS = ("S", "H", "D", "C")
_SUIT_SYMBOLS = {"♠": "S", "♥": "H", "♦": "D", "♣": "C"}
_STRAINS = ("NT", "S", "H", "D", "C")
_MAX_TABLES = 16
_MAX_ROWS_PER_TABLE = 256
_MAX_CELLS_PER_ROW = 32
_MAX_CELL_TEXT = 512
_DDS_HOST = "dds.bridgewebs.com"
_DDS_PATH = "/bsol2/ddummy.htm"


@dataclass(frozen=True)
class _Cell:
    text: str
    links: tuple[str, ...]


@dataclass(frozen=True)
class _Row:
    classes: tuple[str, ...]
    cells: tuple[_Cell, ...]


@dataclass(frozen=True)
class _Table:
    classes: tuple[str, ...]
    rows: tuple[_Row, ...]


@dataclass
class _TableBuilder:
    classes: tuple[str, ...]
    rows: list[_Row] = field(default_factory=list)


def _classes(attrs: list[tuple[str, str | None]]) -> tuple[str, ...]:
    raw = next((value for key, value in attrs if key.lower() == "class"), "") or ""
    return tuple(
        sorted(
            {token for token in raw.split() if re.fullmatch(r"[A-Za-z0-9_-]+", token)}
        )
    )


def _normalize(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


class _BoardParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[_Table] = []
        self.anchors: list[str] = []
        self._table: _TableBuilder | None = None
        self._row_classes: tuple[str, ...] = ()
        self._row: list[_Cell] | None = None
        self._cell_text: list[str] | None = None
        self._cell_links: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered == "table":
            if self._table is not None:
                raise AutopilotContractError("IBF_BOARD_NESTED_TABLE")
            if len(self.tables) >= _MAX_TABLES:
                raise AutopilotContractError("IBF_BOARD_TABLE_LIMIT")
            self._table = _TableBuilder(classes=_classes(attrs))
        elif lowered == "tr" and self._table is not None:
            if self._row is not None:
                # The official DD table omits the closing tag for its W row.
                # HTML permits the next <tr> to close it implicitly.
                self.handle_endtag("tr")
            self._row_classes = _classes(attrs)
            self._row = []
        elif lowered in {"td", "th"} and self._row is not None:
            if self._cell_text is not None:
                self.handle_endtag("td")
            self._cell_text = []
            self._cell_links = []
        elif lowered == "a":
            href = next((value for key, value in attrs if key.lower() == "href"), None)
            if href:
                self.anchors.append(href)
                if self._cell_links is not None:
                    self._cell_links.append(href)

    def handle_data(self, data: str) -> None:
        if self._cell_text is not None:
            self._cell_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if (
            lowered in {"td", "th"}
            and self._cell_text is not None
            and self._row is not None
        ):
            text = _normalize(" ".join(self._cell_text))
            if len(text) > _MAX_CELL_TEXT:
                raise AutopilotContractError("IBF_BOARD_CELL_TEXT_LIMIT")
            if len(self._row) >= _MAX_CELLS_PER_ROW:
                raise AutopilotContractError("IBF_BOARD_CELL_LIMIT")
            self._row.append(_Cell(text=text, links=tuple(self._cell_links or ())))
            self._cell_text = None
            self._cell_links = None
        elif lowered == "tr" and self._row is not None and self._table is not None:
            if len(self._table.rows) >= _MAX_ROWS_PER_TABLE:
                raise AutopilotContractError("IBF_BOARD_ROW_LIMIT")
            if self._row:
                self._table.rows.append(
                    _Row(classes=self._row_classes, cells=tuple(self._row))
                )
            self._row = None
            self._row_classes = ()
            self._cell_text = None
            self._cell_links = None
        elif lowered == "table" and self._table is not None:
            self.tables.append(
                _Table(classes=self._table.classes, rows=tuple(self._table.rows))
            )
            self._table = None


def _parse(html: str) -> tuple[tuple[_Table, ...], tuple[str, ...]]:
    parser = _BoardParser()
    try:
        parser.feed(html)
        parser.close()
    except AutopilotContractError:
        raise
    except Exception as exc:
        raise AutopilotContractError("IBF_BOARD_HTML_INVALID") from exc
    if (
        parser._table is not None
        or parser._row is not None
        or parser._cell_text is not None
    ):
        raise AutopilotContractError("IBF_BOARD_HTML_INCOMPLETE")
    return tuple(parser.tables), tuple(parser.anchors)


def _one_table(tables: tuple[_Table, ...], class_name: str) -> _Table:
    matches = [table for table in tables if class_name in table.classes]
    if len(matches) != 1:
        raise AutopilotContractError(f"IBF_BOARD_{class_name.upper()}_TABLE_INVALID")
    return matches[0]


def _parse_hand(text: str) -> dict[str, str]:
    normalized = text.upper().replace("10", "T")
    matches = re.findall(r"([♠♥♦♣])\s*([AKQJT98765432]*)", normalized)
    if len(matches) != 4 or [symbol for symbol, _cards in matches] != list(
        _SUIT_SYMBOLS
    ):
        raise AutopilotContractError("IBF_BOARD_HAND_INVALID")
    hand = {_SUIT_SYMBOLS[symbol]: cards for symbol, cards in matches}
    if sum(len(cards) for cards in hand.values()) != 13:
        raise AutopilotContractError("IBF_BOARD_HAND_CARD_COUNT_INVALID")
    return hand


def _extract_hands(table: _Table) -> dict[str, dict[str, str]]:
    if len(table.rows) != 3 or any(len(row.cells) != 3 for row in table.rows):
        raise AutopilotContractError("IBF_BOARD_DEAL_SHAPE_INVALID")
    hands = {
        "N": _parse_hand(table.rows[0].cells[1].text),
        "W": _parse_hand(table.rows[1].cells[0].text),
        "E": _parse_hand(table.rows[1].cells[2].text),
        "S": _parse_hand(table.rows[2].cells[1].text),
    }
    cards = [
        rank + suit
        for hand in hands.values()
        for suit, ranks in hand.items()
        for rank in ranks
    ]
    if len(cards) != 52 or len(set(cards)) != 52:
        raise AutopilotContractError("IBF_BOARD_DEAL_INTEGRITY_INVALID")
    return hands


def _dds_source(
    anchors: tuple[str, ...],
    expected_board_number: int,
    hands: dict[str, dict[str, str]],
) -> tuple[str, str, str]:
    matches: list[tuple[str, urllib.parse.SplitResult]] = []
    for href in anchors:
        parsed = urllib.parse.urlsplit(href)
        if (
            parsed.scheme == "https"
            and parsed.hostname == _DDS_HOST
            and parsed.path == _DDS_PATH
        ):
            matches.append((href, parsed))
    if len(matches) != 1:
        raise AutopilotContractError("IBF_BOARD_DDS_SOURCE_INVALID")
    href, parsed = matches[0]
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.fragment
    ):
        raise AutopilotContractError("IBF_BOARD_DDS_SOURCE_INVALID")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    required = {"club", "board", "dealer", "vul", "north", "east", "south", "west"}
    if set(query) != required or any(len(value) != 1 for value in query.values()):
        raise AutopilotContractError("IBF_BOARD_DDS_QUERY_INVALID")
    if query["club"] != ["il_iba"] or query["board"] != [str(expected_board_number)]:
        raise AutopilotContractError("IBF_BOARD_DDS_IDENTITY_INVALID")
    dealer = query["dealer"][0]
    vulnerability = query["vul"][0]
    if (
        dealer not in {"N", "E", "S", "W"}
        or re.fullmatch(r"[A-Za-z-]{1,16}", vulnerability) is None
    ):
        raise AutopilotContractError("IBF_BOARD_DDS_CONTEXT_INVALID")
    for seat, key in (("N", "north"), ("E", "east"), ("S", "south"), ("W", "west")):
        pbn = ".".join(hands[seat][suit] for suit in _SUITS)
        if query[key] != [pbn]:
            raise AutopilotContractError("IBF_BOARD_DDS_DEAL_MISMATCH")
    return href, dealer, vulnerability


def _extract_double_dummy(table: _Table) -> tuple[dict[str, dict[str, int]], int]:
    if len(table.rows) != 6:
        raise AutopilotContractError("IBF_BOARD_DD_SHAPE_INVALID")
    header = [
        cell.text.replace("♠", "S")
        .replace("♥", "H")
        .replace("♦", "D")
        .replace("♣", "C")
        for cell in table.rows[0].cells
    ]
    if header != ["", *_STRAINS]:
        raise AutopilotContractError("IBF_BOARD_DD_HEADER_INVALID")
    result: dict[str, dict[str, int]] = {}
    for expected_seat, row in zip(("N", "S", "E", "W"), table.rows[1:5], strict=True):
        values = [cell.text for cell in row.cells]
        if len(values) != 6 or values[0] != expected_seat:
            raise AutopilotContractError("IBF_BOARD_DD_ROW_INVALID")
        try:
            tricks = [int(value) for value in values[1:]]
        except ValueError as exc:
            raise AutopilotContractError("IBF_BOARD_DD_VALUE_INVALID") from exc
        if any(value < 0 or value > 13 for value in tricks):
            raise AutopilotContractError("IBF_BOARD_DD_VALUE_INVALID")
        result[expected_seat] = dict(zip(_STRAINS, tricks, strict=True))
    par_match = re.fullmatch(r"Par:\s*([-+]?[0-9]{1,5})", table.rows[5].cells[0].text)
    if par_match is None:
        raise AutopilotContractError("IBF_BOARD_PAR_INVALID")
    par_score = int(par_match.group(1))
    if abs(par_score) > 10000:
        raise AutopilotContractError("IBF_BOARD_PAR_INVALID")
    return result, par_score


def _seat_from_links(cell: _Cell) -> str | None:
    seats: list[str] = []
    for href in cell.links:
        parsed = urllib.parse.urlsplit(href)
        if parsed.path not in {
            "personal.php",
            "/viewer/personal.php",
            "/viewer//personal.php",
        }:
            continue
        values = urllib.parse.parse_qs(parsed.query, keep_blank_values=False).get(
            "seat", []
        )
        if len(values) == 1 and re.fullmatch(r"[A-Za-z0-9:-]{1,24}", values[0]):
            seats.append(values[0])
    unique = sorted(set(seats))
    if len(unique) > 1:
        raise AutopilotContractError("IBF_BOARD_FIELD_SEAT_INVALID")
    return unique[0] if unique else None


def _optional_percentage(text: str) -> float | None:
    if not text:
        return None
    if re.fullmatch(r"(?:100(?:\.0+)?|[0-9]{1,2}(?:\.[0-9]+)?)", text) is None:
        raise AutopilotContractError("IBF_BOARD_FIELD_PERCENTAGE_INVALID")
    value = float(text)
    if not 0 <= value <= 100:
        raise AutopilotContractError("IBF_BOARD_FIELD_PERCENTAGE_INVALID")
    return value


def _optional_score(text: str) -> int | None:
    if not text:
        return None
    if re.fullmatch(r"[-+]?[0-9]{1,5}", text) is None:
        raise AutopilotContractError("IBF_BOARD_FIELD_SCORE_INVALID")
    return int(text)


def _extract_field(table: _Table, target_seat: str) -> list[dict[str, Any]]:
    if len(table.rows) < 2:
        raise AutopilotContractError("IBF_BOARD_FIELD_MISSING")
    results: list[dict[str, Any]] = []
    target_count = 0
    for index, row in enumerate(table.rows[1:], start=1):
        adjusted = len(row.cells) == 8
        if len(row.cells) not in {8, 9}:
            raise AutopilotContractError("IBF_BOARD_FIELD_ROW_SHAPE_INVALID")
        ew_seat = _seat_from_links(row.cells[0])
        ns_seat = _seat_from_links(row.cells[7] if adjusted else row.cells[8])
        target_side: str | None = None
        if ew_seat == target_seat:
            target_side = "EW"
        if ns_seat == target_seat:
            if target_side is not None:
                raise AutopilotContractError("IBF_BOARD_FIELD_TARGET_DUPLICATE")
            target_side = "NS"
        if target_side is not None:
            target_count += 1
        ew_percentage = _optional_percentage(row.cells[1].text)
        ns_percentage = _optional_percentage(
            row.cells[6].text if adjusted else row.cells[7].text
        )
        if ew_percentage is None or ns_percentage is None:
            raise AutopilotContractError("IBF_BOARD_FIELD_PERCENTAGE_PAIR_INVALID")
        if adjusted:
            adjustment = row.cells[3].text
            if re.fullmatch(r"[A-Z][A-Z+-]{0,7}", adjustment) is None:
                raise AutopilotContractError("IBF_BOARD_FIELD_ADJUSTMENT_INVALID")
            ew_score_cell = None
            ns_score_cell = None
            opening_lead = row.cells[4].text or None
            contract = row.cells[5].text or None
        else:
            adjustment = None
            if abs(ew_percentage + ns_percentage - 100) > 0.02:
                raise AutopilotContractError("IBF_BOARD_FIELD_PERCENTAGE_PAIR_INVALID")
            ew_score_cell = _optional_score(row.cells[3].text)
            ns_score_cell = _optional_score(row.cells[4].text)
            if (ew_score_cell is None) == (ns_score_cell is None):
                raise AutopilotContractError("IBF_BOARD_FIELD_SCORE_SHAPE_INVALID")
            opening_lead = row.cells[5].text or None
            contract = row.cells[6].text
            if not contract or len(contract) > 32:
                raise AutopilotContractError("IBF_BOARD_FIELD_CONTRACT_INVALID")
        results.append(
            {
                "row": index,
                "ew_seat": ew_seat,
                "ns_seat": ns_seat,
                "ew_percentage": ew_percentage,
                "ns_percentage": ns_percentage,
                "ew_score_cell": ew_score_cell,
                "ns_score_cell": ns_score_cell,
                "adjustment": adjustment,
                "opening_lead": opening_lead,
                "contract": contract,
                "target_side": target_side,
            }
        )
    if target_count == 0:
        raise AutopilotContractError("IBF_BOARD_FIELD_TARGET_MISSING")
    if target_count != 1:
        raise AutopilotContractError("IBF_BOARD_FIELD_TARGET_DUPLICATE")
    return results


def extract_structured_board(
    html: str,
    *,
    expected_board_number: int,
    target_seat: str,
) -> dict[str, Any]:
    """Extract one complete, factual and de-identified board representation."""

    if not isinstance(html, str) or not html:
        raise AutopilotContractError("IBF_BOARD_HTML_INVALID")
    if isinstance(expected_board_number, bool) or not 1 <= expected_board_number <= 999:
        raise AutopilotContractError("IBF_BOARD_NUMBER_INVALID")
    if re.fullmatch(r"[A-Za-z0-9:-]{1,24}", target_seat) is None:
        raise AutopilotContractError("IBF_BOARD_TARGET_SEAT_INVALID")
    tables, anchors = _parse(html)
    deal_table = _one_table(tables, "deal")
    dd_table = _one_table(tables, "dd")
    results_table = _one_table(tables, "resultsTable")
    if not deal_table.rows or deal_table.rows[0].cells[0].text != str(
        expected_board_number
    ):
        raise AutopilotContractError("IBF_BOARD_NUMBER_MISMATCH")
    hands = _extract_hands(deal_table)
    dds_url, dealer, vulnerability = _dds_source(anchors, expected_board_number, hands)
    double_dummy, par_score = _extract_double_dummy(dd_table)
    field_results = _extract_field(results_table, target_seat)
    target = next(row for row in field_results if row["target_side"] is not None)
    target_percentage = target[
        "ew_percentage" if target["target_side"] == "EW" else "ns_percentage"
    ]
    return {
        "board_number": expected_board_number,
        "dealer": dealer,
        "vulnerability": vulnerability,
        "hands": hands,
        "double_dummy_tricks": double_dummy,
        "par_score": par_score,
        "field_results": field_results,
        "target_result": {
            "side": target["target_side"],
            "percentage": target_percentage,
            "opening_lead": target["opening_lead"],
            "contract": target["contract"],
            "player_error_demonstrated": False,
        },
        "observability": {
            "bidding": "UNOBSERVABLE_NO_AUCTION",
            "opening_lead": "SOURCE_OBSERVED_NOT_EVALUATED",
            "defense": "UNOBSERVABLE_NO_PLAY_RECORD",
            "declarer_play": "UNOBSERVABLE_NO_PLAY_RECORD",
            "competitive_decision": "UNOBSERVABLE_NO_AUCTION",
        },
        "dds_source_url_sha256": hashlib.sha256(dds_url.encode("utf-8")).hexdigest(),
        "board_page_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
    }


def build_structured_tournament_artifact(
    compact_snapshot: dict[str, Any], structured_boards: list[dict[str, Any]]
) -> dict[str, Any]:
    """Join compact retained evidence to complete board facts, failing on drift."""

    participation = compact_snapshot.get("latest_participation")
    compact_boards = compact_snapshot.get("boards")
    if not isinstance(participation, dict) or not isinstance(compact_boards, list):
        raise AutopilotContractError("IBF_STRUCTURED_SNAPSHOT_INVALID")
    if compact_snapshot.get("board_count") != len(compact_boards) or len(
        compact_boards
    ) != len(structured_boards):
        raise AutopilotContractError("IBF_STRUCTURED_BOARD_COUNT_MISMATCH")
    by_number = {board.get("board_number"): board for board in structured_boards}
    if len(by_number) != len(structured_boards) or any(
        not isinstance(number, int) or isinstance(number, bool) for number in by_number
    ):
        raise AutopilotContractError("IBF_STRUCTURED_BOARD_IDENTITY_INVALID")
    ordered: list[dict[str, Any]] = []
    review_order: list[dict[str, Any]] = []
    for compact in compact_boards:
        number = compact.get("board_number") if isinstance(compact, dict) else None
        structured = by_number.get(number)
        if (
            not isinstance(number, int)
            or isinstance(number, bool)
            or not isinstance(structured, dict)
        ):
            raise AutopilotContractError("IBF_STRUCTURED_BOARD_IDENTITY_INVALID")
        if structured.get("board_page_sha256") != compact.get("field_page_sha256"):
            raise AutopilotContractError("IBF_STRUCTURED_SOURCE_DRIFT")
        if len(structured.get("field_results", [])) != compact.get("field_row_count"):
            raise AutopilotContractError("IBF_STRUCTURED_FIELD_COUNT_MISMATCH")
        percentage_token = compact.get("percentage_token")
        target = structured.get("target_result")
        if not isinstance(target, dict) or percentage_token is None:
            raise AutopilotContractError("IBF_STRUCTURED_TARGET_RESULT_INVALID")
        try:
            compact_percentage = float(percentage_token)
            structured_percentage = float(target["percentage"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AutopilotContractError(
                "IBF_STRUCTURED_TARGET_RESULT_INVALID"
            ) from exc
        if abs(compact_percentage - structured_percentage) > 0.01:
            raise AutopilotContractError("IBF_STRUCTURED_TARGET_RESULT_MISMATCH")
        ordered.append(structured)
        review_order.append(
            {
                "board_number": number,
                "percentage": structured_percentage,
                "player_error_demonstrated": False,
            }
        )
    ordered.sort(key=lambda board: board["board_number"])
    review_order.sort(key=lambda item: (item["percentage"], item["board_number"]))
    return {
        "schema_version": "IBF_STRUCTURED_TOURNAMENT_V1",
        "source_authority": compact_snapshot.get("source_authority"),
        "ibf_player_id": compact_snapshot.get("ibf_player_id"),
        "latest_participation": participation,
        "board_count": len(ordered),
        "boards": ordered,
        "teaching_analysis": {
            "review_order": review_order,
            "causal_attribution": "NOT_DEMONSTRATED_BY_SCORE_OR_DOUBLE_DUMMY_ALONE",
            "missing_source_dimensions": ["AUCTION", "PLAY_RECORD"],
            "methodology_or_canon_applied": False,
        },
        "production_mutation": False,
        "model_calls": 0,
        "cost_actual_microusd": 0,
    }
