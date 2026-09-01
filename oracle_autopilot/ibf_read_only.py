"""Bounded read-only retrieval for the first real Autopilot IBF task.

This module deliberately performs no bridge inference. It discovers the latest
actual participation from official IBF pages, retrieves the personal board list
and verifies each linked field page. The retained result is compact evidence for
later analysis; missing source data fails closed instead of being invented.
"""

from __future__ import annotations

import hashlib
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from typing import Any

from .contract import AutopilotContractError, AutopilotRetryableError


IBF_INDEX_URL = "https://main.bridge.co.il/results/"
IBF_MEMBER_URL = "https://bridge.co.il/viewer/membermplist.php?id={player_id}"
IBF_VIEWER_ORIGIN = "https://bridge.co.il"
IBF_ALLOWED_HOSTS = frozenset(
    {"bridge.co.il", "www.bridge.co.il", "main.bridge.co.il", "www.main.bridge.co.il"}
)
IBF_VIEWER_PATHS = frozenset(
    {"/viewer/membermplist.php", "/viewer/session.php", "/viewer/personal.php", "/viewer/board.php"}
)
IBF_RESPONSE_LIMIT_BYTES = 1_048_576
IBF_MAX_SESSION_CANDIDATES = 60
IBF_MAX_BOARD_PAGES = 32
IBF_MAX_REQUESTS = 96
IBF_ROW_EXCERPT_LIMIT = 160
IBF_SOURCE_AUTHORITY = "ISRAEL_BRIDGE_FEDERATION_OFFICIAL_RESULTS"


class _RejectIBFRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise AutopilotContractError("IBF_REDIRECT_REJECTED")


@dataclass
class _ReadBudget:
    used: int = 0

    def consume(self) -> None:
        if self.used >= IBF_MAX_REQUESTS:
            raise AutopilotContractError("IBF_REQUEST_BUDGET_EXHAUSTED")
        self.used += 1


@dataclass(frozen=True)
class _ParsedDocument:
    text: str
    rows: tuple[tuple[tuple[str, tuple[str, ...]], ...], ...]
    anchors: tuple[str, ...]


class _DocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._all_text: list[str] = []
        self._anchors: list[str] = []
        self._row: list[tuple[str, tuple[str, ...]]] | None = None
        self._cell_text: list[str] | None = None
        self._cell_links: list[str] | None = None
        self.rows: list[tuple[tuple[str, tuple[str, ...]], ...]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered == "tr":
            self._row = []
        elif lowered in {"td", "th"} and self._row is not None:
            self._cell_text = []
            self._cell_links = []
        elif lowered == "a":
            href = next((value for key, value in attrs if key.lower() == "href"), None)
            if href:
                self._anchors.append(href)
                if self._cell_links is not None:
                    self._cell_links.append(href)

    def handle_data(self, data: str) -> None:
        if data:
            self._all_text.append(data)
            if self._cell_text is not None:
                self._cell_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"td", "th"} and self._cell_text is not None and self._row is not None:
            text = _normalize_text(" ".join(self._cell_text))
            self._row.append((text, tuple(self._cell_links or ())))
            self._cell_text = None
            self._cell_links = None
        elif lowered == "tr" and self._row is not None:
            if self._row:
                self.rows.append(tuple(self._row))
            self._row = None
            self._cell_text = None
            self._cell_links = None

    def parsed(self) -> _ParsedDocument:
        return _ParsedDocument(
            text=_normalize_text(" ".join(self._all_text)),
            rows=tuple(self.rows),
            anchors=tuple(self._anchors),
        )


def _normalize_text(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def _parse_document(html: str) -> _ParsedDocument:
    parser = _DocumentParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:
        raise AutopilotContractError("IBF_HTML_INVALID") from exc
    return parser.parsed()


def _validate_official_url(url: str) -> urllib.parse.SplitResult:
    if not isinstance(url, str) or len(url) > 2048:
        raise AutopilotContractError("IBF_URL_INVALID")
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or host not in IBF_ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.fragment
        or len(parsed.query) > 1024
    ):
        raise AutopilotContractError("IBF_ORIGIN_INVALID")
    if host in {"main.bridge.co.il", "www.main.bridge.co.il"}:
        if parsed.path != "/results/" or parsed.query:
            raise AutopilotContractError("IBF_INDEX_URL_INVALID")
    elif parsed.path not in IBF_VIEWER_PATHS:
        raise AutopilotContractError("IBF_VIEWER_PATH_INVALID")
    return parsed


def _decode_html(raw: bytes, charset: str | None) -> str:
    candidates: list[str] = []
    if charset:
        candidates.append(charset.lower())
    candidates.extend(["utf-8", "windows-1255", "iso-8859-8"])
    for candidate in candidates:
        if candidate not in {"utf-8", "utf8", "windows-1255", "cp1255", "iso-8859-8"}:
            continue
        try:
            return raw.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    raise AutopilotContractError("IBF_HTML_ENCODING_INVALID")


def _ibf_get_html(url: str, budget: _ReadBudget) -> str:
    """Credential-free, no-redirect, size-bounded GET to an official IBF origin."""

    _validate_official_url(url)
    budget.consume()
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "bridge-school-autopilot-shadow/1.6", "Accept": "text/html,*/*;q=0.1"},
    )
    try:
        opener = urllib.request.build_opener(_RejectIBFRedirects())
        with opener.open(request, timeout=15) as response:
            final_url = response.geturl()
            _validate_official_url(final_url)
            if final_url != url or response.status != 200:
                raise AutopilotContractError("IBF_RESPONSE_INVALID")
            raw = response.read(IBF_RESPONSE_LIMIT_BYTES + 1)
            charset = response.headers.get_content_charset()
    except urllib.error.HTTPError as exc:
        if exc.code in {408, 425, 429} or 500 <= exc.code <= 599:
            raise AutopilotRetryableError("IBF_TRANSIENT_HTTP_ERROR") from exc
        raise AutopilotContractError("IBF_HTTP_ERROR") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise AutopilotRetryableError("IBF_TRANSIENT_HTTP_ERROR") from exc
    if len(raw) > IBF_RESPONSE_LIMIT_BYTES:
        raise AutopilotContractError("IBF_RESPONSE_TOO_LARGE")
    return _decode_html(raw, charset)


def _normalize_legacy_viewer_url(url: str) -> str:
    """Canonicalize only known legacy href forms before strict validation."""

    parsed = urllib.parse.urlsplit(url)
    normalized = parsed
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme == "http"
        and host in IBF_ALLOWED_HOSTS
        and parsed.netloc.lower() == host
        and not parsed.fragment
    ):
        normalized = normalized._replace(scheme="https")
    if normalized.path.startswith("/viewer//"):
        normalized_path = f"/viewer/{normalized.path.removeprefix('/viewer//')}"
        if normalized_path in IBF_VIEWER_PATHS:
            normalized = normalized._replace(path=normalized_path)
    return urllib.parse.urlunsplit(normalized)


def _canonical_session_url(href: str, base_url: str) -> tuple[str, int, int] | None:
    absolute = _normalize_legacy_viewer_url(urllib.parse.urljoin(base_url, href))
    try:
        parsed = _validate_official_url(absolute)
    except AutopilotContractError:
        return None
    if parsed.path != "/viewer/session.php":
        return None
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)
    if set(query) - {"event", "round", "ibf", "hc"}:
        return None
    event_values = query.get("event")
    round_values = query.get("round")
    if not event_values or not round_values or len(event_values) != 1 or len(round_values) != 1:
        return None
    if re.fullmatch(r"[1-9][0-9]{0,9}", event_values[0]) is None or re.fullmatch(
        r"[1-9][0-9]{0,3}", round_values[0]
    ) is None:
        return None
    event_id = int(event_values[0])
    round_id = int(round_values[0])
    url = f"{IBF_VIEWER_ORIGIN}/viewer/session.php?event={event_id}&round={round_id}"
    return url, event_id, round_id


def _canonical_personal_url(href: str, base_url: str, event_id: int, round_id: int) -> tuple[str, str] | None:
    absolute = _normalize_legacy_viewer_url(urllib.parse.urljoin(base_url, href))
    try:
        parsed = _validate_official_url(absolute)
    except AutopilotContractError:
        return None
    if parsed.path != "/viewer/personal.php":
        return None
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)
    event = query.get("event", [])
    round_values = query.get("round", [])
    seats = query.get("seat", [])
    if event != [str(event_id)] or round_values != [str(round_id)] or len(seats) != 1:
        return None
    seat = seats[0]
    if re.fullmatch(r"[A-Za-z0-9:-]{1,24}", seat) is None:
        return None
    return (
        f"{IBF_VIEWER_ORIGIN}/viewer/personal.php?event={event_id}&round={round_id}&seat={urllib.parse.quote(seat, safe=':-')}",
        seat,
    )


def _validated_board_url(href: str, base_url: str, event_id: int, round_id: int) -> str | None:
    absolute = _normalize_legacy_viewer_url(urllib.parse.urljoin(base_url, href))
    try:
        parsed = _validate_official_url(absolute)
    except AutopilotContractError:
        return None
    if parsed.path != "/viewer/board.php":
        return None
    query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
    if not 2 <= len(query_pairs) <= 12:
        return None
    values: dict[str, list[str]] = {}
    for key, value in query_pairs:
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", key) is None:
            return None
        if re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", value) is None:
            return None
        values.setdefault(key, []).append(value)
    if values.get("event") != [str(event_id)] or values.get("round") != [str(round_id)]:
        return None
    normalized_query = urllib.parse.urlencode(query_pairs)
    return f"{IBF_VIEWER_ORIGIN}/viewer/board.php?{normalized_query}"


def _session_candidates(documents: list[tuple[str, _ParsedDocument]]) -> list[tuple[str, int, int]]:
    found: dict[tuple[int, int], str] = {}
    for base_url, document in documents:
        for href in document.anchors:
            candidate = _canonical_session_url(href, base_url)
            if candidate is None:
                continue
            url, event_id, round_id = candidate
            found[(event_id, round_id)] = url
    ordered = sorted(found.items(), key=lambda item: item[0], reverse=True)
    return [(url, event_id, round_id) for (event_id, round_id), url in ordered[:IBF_MAX_SESSION_CANDIDATES]]


def _row_text(row: tuple[tuple[str, tuple[str, ...]], ...]) -> str:
    return _normalize_text(" | ".join(cell[0] for cell in row if cell[0]))


def _row_contains_player(row: tuple[tuple[str, tuple[str, ...]], ...], player_id: str) -> bool:
    return re.search(rf"(?<![0-9]){re.escape(player_id)}(?![0-9])", _row_text(row)) is not None


def _extract_session_date(text: str) -> date:
    match = re.search(r"(?<![0-9])(\d{1,2})/(\d{1,2})/(\d{2,4})(?![0-9])", text)
    if not match:
        raise AutopilotContractError("IBF_SESSION_DATE_MISSING")
    day, month, year = (int(part) for part in match.groups())
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise AutopilotContractError("IBF_SESSION_DATE_INVALID") from exc


def _find_personal_link(
    document: _ParsedDocument, base_url: str, player_id: str, event_id: int, round_id: int
) -> tuple[str, str] | None:
    for row in document.rows:
        if not _row_contains_player(row, player_id):
            continue
        for _text, links in row:
            for href in links:
                personal = _canonical_personal_url(href, base_url, event_id, round_id)
                if personal is not None:
                    return personal
    return None


def _extract_personal_boards(
    document: _ParsedDocument, personal_url: str, event_id: int, round_id: int
) -> list[dict[str, Any]]:
    boards: list[dict[str, Any]] = []
    seen_numbers: set[int] = set()
    for row in document.rows:
        if not row:
            continue
        first = row[0][0].strip()
        if re.fullmatch(r"[1-9][0-9]{0,2}", first) is None:
            continue
        board_number = int(first)
        if board_number in seen_numbers:
            raise AutopilotContractError("IBF_PERSONAL_DUPLICATE_BOARD")
        board_url: str | None = None
        for _text, links in row:
            for href in links:
                candidate = _validated_board_url(href, personal_url, event_id, round_id)
                if candidate is not None:
                    board_url = candidate
                    break
            if board_url:
                break
        if board_url is None:
            continue
        text = _row_text(row)
        percent_match = re.search(r"(?<![0-9])(100(?:\.0+)?|[0-9]{1,2}(?:\.[0-9]+)?)(?![0-9])", text)
        score_match = re.search(r"(?<![0-9])[-+]?[1-9][0-9]{1,4}(?![0-9])", text)
        boards.append(
            {
                "board_number": board_number,
                "board_url": board_url,
                "personal_row_excerpt": text[:IBF_ROW_EXCERPT_LIMIT],
                "percentage_token": percent_match.group(1) if percent_match else None,
                "score_token": score_match.group(0) if score_match else None,
            }
        )
        seen_numbers.add(board_number)
    boards.sort(key=lambda item: item["board_number"])
    if not boards:
        raise AutopilotContractError("IBF_PERSONAL_BOARD_LINKS_MISSING")
    if len(boards) > IBF_MAX_BOARD_PAGES:
        raise AutopilotContractError("IBF_BOARD_LIMIT_EXCEEDED")
    return boards


def _count_field_rows(document: _ParsedDocument) -> int:
    count = 0
    for row in document.rows:
        text = _row_text(row)
        if len(row) >= 4 and re.search(r"(?<![0-9])[-+]?[0-9]{2,5}(?![0-9])", text):
            count += 1
    return count


def fetch_ibf_read_only_snapshot(goal_json: dict[str, Any]) -> dict[str, Any]:
    """Discover latest actual participation and retain compact official evidence."""

    player_id = goal_json.get("ibf_player_id")
    if not isinstance(player_id, str) or re.fullmatch(r"[1-9][0-9]{0,9}", player_id) is None:
        raise AutopilotContractError("IBF_PLAYER_ID_INVALID")
    if goal_json.get("source_authority") != IBF_SOURCE_AUTHORITY:
        raise AutopilotContractError("IBF_SOURCE_AUTHORITY_INVALID")
    budget = _ReadBudget()

    member_url = IBF_MEMBER_URL.format(player_id=player_id)
    member_html = _ibf_get_html(member_url, budget)
    member_doc = _parse_document(member_html)
    if re.search(rf"(?<![0-9]){re.escape(player_id)}(?![0-9])", member_doc.text) is None:
        raise AutopilotContractError("IBF_MEMBER_IDENTITY_MISMATCH")

    index_html = _ibf_get_html(IBF_INDEX_URL, budget)
    index_doc = _parse_document(index_html)
    candidates = _session_candidates([(member_url, member_doc), (IBF_INDEX_URL, index_doc)])
    if not candidates:
        raise AutopilotContractError("IBF_DISCOVERY_NO_SESSION_LINKS")

    participations: list[dict[str, Any]] = []
    for session_url, event_id, round_id in candidates:
        session_html = _ibf_get_html(session_url, budget)
        session_doc = _parse_document(session_html)
        personal = _find_personal_link(session_doc, session_url, player_id, event_id, round_id)
        if personal is None:
            continue
        session_date = _extract_session_date(session_doc.text)
        personal_url, seat = personal
        participations.append(
            {
                "session_url": session_url,
                "session_html": session_html,
                "event_id": event_id,
                "round_id": round_id,
                "date": session_date,
                "personal_url": personal_url,
                "seat": seat,
            }
        )

    if not participations:
        raise AutopilotContractError("IBF_PLAYER_NOT_FOUND_IN_RECENT_SESSIONS")
    latest = max(
        participations,
        key=lambda item: (item["date"], item["event_id"], item["round_id"]),
    )

    personal_html = _ibf_get_html(latest["personal_url"], budget)
    personal_doc = _parse_document(personal_html)
    if re.search(rf"(?<![0-9]){re.escape(player_id)}(?![0-9])", personal_doc.text) is None:
        raise AutopilotContractError("IBF_PERSONAL_IDENTITY_MISMATCH")
    boards = _extract_personal_boards(
        personal_doc, latest["personal_url"], latest["event_id"], latest["round_id"]
    )

    compact_boards: list[dict[str, Any]] = []
    for board in boards:
        board_html = _ibf_get_html(board["board_url"], budget)
        board_doc = _parse_document(board_html)
        field_row_count = _count_field_rows(board_doc)
        if field_row_count < 1:
            raise AutopilotContractError("IBF_FIELD_COMPARISON_MISSING")
        compact_boards.append(
            {
                "board_number": board["board_number"],
                "personal_row_excerpt": board["personal_row_excerpt"],
                "percentage_token": board["percentage_token"],
                "score_token": board["score_token"],
                "field_row_count": field_row_count,
                "field_page_sha256": hashlib.sha256(board_html.encode("utf-8")).hexdigest(),
            }
        )

    return {
        "source_authority": IBF_SOURCE_AUTHORITY,
        "ibf_player_id": player_id,
        "latest_participation": {
            "date": latest["date"].isoformat(),
            "event_id": latest["event_id"],
            "round_id": latest["round_id"],
            "seat": latest["seat"],
            "session_url": latest["session_url"],
            "personal_url": latest["personal_url"],
        },
        "board_count": len(compact_boards),
        "boards": compact_boards,
        "member_page_sha256": hashlib.sha256(member_html.encode("utf-8")).hexdigest(),
        "results_index_sha256": hashlib.sha256(index_html.encode("utf-8")).hexdigest(),
        "session_page_sha256": hashlib.sha256(latest["session_html"].encode("utf-8")).hexdigest(),
        "personal_page_sha256": hashlib.sha256(personal_html.encode("utf-8")).hexdigest(),
        "request_count": budget.used,
        "http_method": "GET",
        "production_mutation": False,
        "model_calls": 0,
        "cost_actual_microusd": 0,
        "analysis_scope": "SOURCE_RETRIEVAL_AND_FIELD_EVIDENCE_ONLY",
    }
