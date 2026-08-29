"""One-page-per-deal PDF review of profiled SHADOW bridge observations.

Every page keeps the source screenshot, accepted observations, optional exact
39-to-13 reconstruction, and auction together.  Observed and derived cards are
rendered as separate evidence classes; the PDF is never a canonical artifact.
"""
from __future__ import annotations

import hashlib
import math
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from bridge_contracts.video_deal import SEATS
from bridge_vision.shadow_pbn import build_shadow_deal_views

SCHEMA = "bridge-profiled-shadow-pdf/v1"
REPORT_NAME = "bridge_positions_profiled_shadow_report.pdf"
SUITS = ("S", "H", "D", "C")
SUIT_SYMBOLS = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
RANK_ORDER = "AKQJT98765432"
MAX_DISPLAYED_AUCTION_CALLS = 24


class ShadowPdfError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_frame(frames_root: Path, raw_name: Any, expected_sha256: Any) -> Path | None:
    name = str(raw_name or "").strip()
    expected = str(expected_sha256 or "").strip().lower()
    if not name or Path(name).name != name:
        return None
    path = (frames_root / name).resolve()
    try:
        path.relative_to(frames_root.resolve())
    except ValueError:
        return None
    if path.is_symlink() or not path.is_file():
        return None
    if len(expected) != 64 or _sha256(path) != expected:
        return None
    return path


def _font_paths() -> tuple[str, str]:
    regular = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    bold = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    if not regular.is_file() or not bold.is_file():
        raise ShadowPdfError("DejaVu Sans fonts are unavailable")
    return str(regular), str(bold)


def _register_fonts() -> None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    regular, bold = _font_paths()
    if "ShadowSans" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("ShadowSans", regular))
    if "ShadowSans-Bold" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("ShadowSans-Bold", bold))


def _panel(canvas: Any, x: float, y: float, width: float, height: float, title: str) -> None:
    from reportlab.lib.colors import HexColor

    canvas.setFillColor(HexColor("#F7F8FA"))
    canvas.setStrokeColor(HexColor("#D9DEE7"))
    canvas.roundRect(x, y, width, height, 7, fill=1, stroke=1)
    canvas.setFillColor(HexColor("#172033"))
    canvas.setFont("ShadowSans-Bold", 8.2)
    canvas.drawString(x + 8, y + height - 14, title)


def _hand_lines(cards: Sequence[str], unknown_count: int) -> list[tuple[str, str]]:
    by_suit = {suit: [] for suit in SUITS}
    for card in cards:
        if isinstance(card, str) and len(card) == 2 and card[1] in by_suit:
            by_suit[card[1]].append(card[0])
    lines = []
    for suit in SUITS:
        ranks = "".join(rank for rank in RANK_ORDER if rank in by_suit[suit]) or "-"
        lines.append((suit, f"{SUIT_SYMBOLS[suit]} {ranks}"))
    if unknown_count:
        lines.append(("?", f"? x{unknown_count}"))
    return lines


def _draw_hand(
    canvas: Any,
    *,
    x: float,
    y: float,
    width: float,
    seat: str,
    hand: Mapping[str, Any],
    derived: bool,
    compact: bool,
) -> None:
    from reportlab.lib.colors import HexColor

    cards = list(hand.get("cards") or [])
    unknown = int(hand.get("unknown_count") or 0)
    canvas.setFillColor(HexColor("#A65D00") if derived else HexColor("#0B6B4B"))
    canvas.setFont("ShadowSans-Bold", 6.8 if compact else 7.4)
    label = seat + (" - DERIVED" if derived else " - OBSERVED")
    canvas.drawString(x, y, label)
    step = 7.0 if compact else 8.0
    font_size = 6.5 if compact else 7.0
    line_y = y - step
    for suit, line in _hand_lines(cards, unknown):
        if suit in {"H", "D"}:
            canvas.setFillColor(HexColor("#C52A35") if not derived else HexColor("#A65D00"))
        else:
            canvas.setFillColor(HexColor("#202838") if not derived else HexColor("#A65D00"))
        canvas.setFont("ShadowSans", font_size)
        canvas.drawString(x, line_y, line[:28])
        line_y -= step


def _draw_deal(
    canvas: Any,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    deal: Mapping[str, Any],
    title: str,
    derived_seats: set[str],
    note: str,
) -> None:
    from reportlab.lib.colors import HexColor

    _panel(canvas, x, y, width, height, title)
    hands = deal.get("hands") or {}
    compact = height < 210
    hand_width = width * (0.42 if compact else 0.46)
    top_y = y + height - 30
    bottom_y = y + 62
    middle_y = y + height * 0.58
    _draw_hand(
        canvas,
        x=x + (width - hand_width) / 2,
        y=top_y,
        width=hand_width,
        seat="N",
        hand=hands.get("N") or {},
        derived="N" in derived_seats,
        compact=compact,
    )
    _draw_hand(
        canvas,
        x=x + 9,
        y=middle_y,
        width=hand_width,
        seat="W",
        hand=hands.get("W") or {},
        derived="W" in derived_seats,
        compact=compact,
    )
    _draw_hand(
        canvas,
        x=x + width - hand_width + 1,
        y=middle_y,
        width=hand_width,
        seat="E",
        hand=hands.get("E") or {},
        derived="E" in derived_seats,
        compact=compact,
    )
    _draw_hand(
        canvas,
        x=x + (width - hand_width) / 2,
        y=bottom_y,
        width=hand_width,
        seat="S",
        hand=hands.get("S") or {},
        derived="S" in derived_seats,
        compact=compact,
    )
    canvas.setFillColor(HexColor("#667085"))
    canvas.setFont("ShadowSans", 5.8)
    canvas.drawString(x + 8, y + 7, note[:96])


def _draw_screenshot(canvas: Any, *, x: float, y: float, width: float, height: float, frame: Path | None) -> bool:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.utils import ImageReader

    _panel(canvas, x, y, width, height, "Исходный кадр - hash-bound evidence")
    image_x, image_y = x + 7, y + 7
    image_width, image_height = width - 14, height - 27
    if frame is None:
        canvas.setFillColor(HexColor("#E8EBF0"))
        canvas.rect(image_x, image_y, image_width, image_height, fill=1, stroke=0)
        canvas.setFillColor(HexColor("#667085"))
        canvas.setFont("ShadowSans", 8)
        canvas.drawCentredString(x + width / 2, y + height / 2, "Скрин недоступен или не прошел SHA-256 проверку")
        return False
    try:
        image = ImageReader(str(frame))
        source_width, source_height = image.getSize()
        scale = min(image_width / source_width, image_height / source_height)
        draw_width, draw_height = source_width * scale, source_height * scale
        canvas.drawImage(
            image,
            image_x + (image_width - draw_width) / 2,
            image_y + (image_height - draw_height) / 2,
            width=draw_width,
            height=draw_height,
            preserveAspectRatio=True,
            mask="auto",
        )
    except Exception:
        canvas.setFillColor(HexColor("#E8EBF0"))
        canvas.rect(image_x, image_y, image_width, image_height, fill=1, stroke=0)
        canvas.setFillColor(HexColor("#667085"))
        canvas.setFont("ShadowSans", 8)
        canvas.drawCentredString(x + width / 2, y + height / 2, "Файл кадра подтвержден, но изображение не декодируется")
        return False
    return True


def _pbn_call(value: Any) -> str:
    return "Pass" if str(value).upper() == "PASS" else str(value)


def _draw_auction(
    canvas: Any,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    auction: Mapping[str, Any],
) -> None:
    from reportlab.lib.colors import HexColor

    _panel(canvas, x, y, width, height, "Торговля - VISUAL / temporally confirmed")
    status = str(auction.get("status") or "UNAVAILABLE")
    calls = list(auction.get("calls") or [])
    dealer = str(auction.get("dealer") or "")
    canvas.setFillColor(HexColor("#475467"))
    canvas.setFont("ShadowSans", 6.5)
    canvas.drawString(x + 8, y + height - 27, f"Статус: {status}   Сдающий: {dealer or '?'}")
    if not calls or dealer not in SEATS:
        canvas.setFillColor(HexColor("#667085"))
        canvas.setFont("ShadowSans", 8)
        canvas.drawString(x + 8, y + height - 44, "Нет подтвержденной последовательности заявок")
        return

    displayed = calls[:MAX_DISPLAYED_AUCTION_CALLS]
    columns = list(SEATS)
    dealer_index = columns.index(dealer)
    grid_x = x + 8
    grid_y_top = y + height - 39
    cell_width = (width - 16) / 4
    row_height = 12
    canvas.setFont("ShadowSans-Bold", 6.5)
    for column, seat in enumerate(columns):
        canvas.setFillColor(HexColor("#344054"))
        canvas.drawCentredString(grid_x + cell_width * (column + 0.5), grid_y_top, seat)
    rows: list[list[str]] = []
    for index, call in enumerate(displayed):
        absolute = dealer_index + index
        row = absolute // 4
        column = absolute % 4
        while len(rows) <= row:
            rows.append([""] * 4)
        rows[row][column] = _pbn_call(call)
    for row_index, row in enumerate(rows):
        baseline = grid_y_top - row_height * (row_index + 1)
        if baseline < y + 17:
            break
        for column, call in enumerate(row):
            canvas.setFillColor(HexColor("#101828"))
            canvas.setFont("ShadowSans", 6.5)
            canvas.drawCentredString(grid_x + cell_width * (column + 0.5), baseline, call)
    if len(calls) > len(displayed):
        canvas.setFillColor(HexColor("#A65D00"))
        canvas.setFont("ShadowSans", 5.8)
        canvas.drawRightString(x + width - 8, y + 7, f"На странице первые {len(displayed)} из {len(calls)}; полный список в PBN")


def _timestamp(value: Any) -> str:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return "time unknown"
    if not math.isfinite(seconds):
        return "time unknown"
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _draw_header(canvas: Any, *, page_width: float, page_height: float, view: Mapping[str, Any], source: str) -> None:
    from reportlab.lib.colors import HexColor

    board = view.get("board_number") or "?"
    timestamp = _timestamp((view.get("representative_frame") or {}).get("time"))
    canvas.setFillColor(HexColor("#101828"))
    canvas.rect(0, page_height - 48, page_width, 48, fill=1, stroke=0)
    canvas.setFillColor(HexColor("#FFFFFF"))
    canvas.setFont("ShadowSans-Bold", 14)
    canvas.drawString(28, page_height - 29, f"Сдача {board}  |  {timestamp}  |  SHADOW ONLY")
    canvas.setFont("ShadowSans", 6.8)
    canvas.drawRightString(page_width - 28, page_height - 27, (source or "Universal Video")[:72])


def _draw_footer(canvas: Any, *, page_width: float, page_number: int, page_count: int) -> None:
    from reportlab.lib.colors import HexColor

    canvas.setFillColor(HexColor("#667085"))
    canvas.setFont("ShadowSans", 6)
    canvas.drawString(28, 15, "NOT CANONICAL - NOT PRODUCTION - orange cards are DERIVED, never observed")
    canvas.drawRightString(page_width - 28, 15, f"{page_number}/{page_count}")


def _empty_view(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    representative = {"frame_file": "", "frame_sha256": "", "time": float("inf")}
    for record in records:
        if isinstance(record, Mapping) and record.get("frame_file"):
            representative = {
                "frame_file": str(record.get("frame_file") or ""),
                "frame_sha256": str(record.get("frame_sha256") or ""),
                "time": record.get("time"),
            }
            break
    empty_hands = {seat: {"cards": [], "unknown_count": 13} for seat in SEATS}
    return {
        "board_number": "?",
        "observed": {"hands": empty_hands},
        "observed_count": 0,
        "reconstructed": {"hands": empty_hands, "derivations": []},
        "reconstruction_status": "NOT_DERIVED_INSUFFICIENT_OBSERVATIONS",
        "auction": {"status": "UNAVAILABLE", "calls": []},
        "representative_frame": representative,
        "result_scope": "SHADOW_ONLY",
        "canonical_promotion_allowed": False,
    }


def _validate_pdf(path: Path, *, expected_pages: int) -> None:
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise ShadowPdfError("generated shadow PDF cannot be reopened") from exc
    if len(reader.pages) != expected_pages:
        raise ShadowPdfError("generated shadow PDF page count mismatch")
    metadata = reader.metadata or {}
    if metadata.get("/Subject") != "SHADOW_ONLY; CanonicalPromotionAllowed=false":
        raise ShadowPdfError("generated shadow PDF metadata boundary mismatch")
    for page in reader.pages:
        box = page.mediabox
        if float(box.width) <= float(box.height):
            raise ShadowPdfError("generated shadow PDF page is not landscape")
        text = page.extract_text() or ""
        for marker in ("SHADOW ONLY", "Распознано", "Торговля", "NOT CANONICAL"):
            if marker not in text:
                raise ShadowPdfError(f"generated shadow PDF is missing visible marker: {marker}")


def render_shadow_pdf(
    records: Sequence[Mapping[str, Any]],
    *,
    frames_root: Path,
    output_path: Path,
    source: str = "",
) -> dict[str, Any]:
    """Render and reopen one landscape page per deal, then atomically publish it."""
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.pdfgen.canvas import Canvas

    _register_fonts()
    views = build_shadow_deal_views(records)
    pages = views or [_empty_view(records)]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    page_width, page_height = landscape(A4)
    screenshots_embedded = 0
    fd, temporary_name = tempfile.mkstemp(
        prefix=output_path.name + ".",
        suffix=".tmp",
        dir=output_path.parent,
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        canvas = Canvas(str(temporary), pagesize=(page_width, page_height), pageCompression=1)
        canvas.setTitle("Bridge positions profiled shadow report")
        canvas.setAuthor("Universal Video 3.1-test")
        canvas.setSubject("SHADOW_ONLY; CanonicalPromotionAllowed=false")
        canvas.setKeywords("SHADOW_ONLY, NOT_CANONICAL, NOT_PRODUCTION")
        for page_number, view in enumerate(pages, start=1):
            _draw_header(canvas, page_width=page_width, page_height=page_height, view=view, source=source)
            representative = view.get("representative_frame") or {}
            frame = _safe_frame(
                frames_root,
                representative.get("frame_file"),
                representative.get("frame_sha256"),
            )
            if _draw_screenshot(canvas, x=28, y=257, width=500, height=277, frame=frame):
                screenshots_embedded += 1
            observed = view.get("observed") or {}
            _draw_deal(
                canvas,
                x=536,
                y=257,
                width=278,
                height=277,
                deal=observed,
                title=f"Распознано (OBSERVED) - {int(view.get('observed_count') or 0)}/52",
                derived_seats=set(),
                note="Только карты, прошедшие независимые визуальные и временные пороги",
            )
            reconstructed = view.get("reconstructed") or {}
            derived_seats = {
                str(item.get("seat"))
                for item in reconstructed.get("derivations") or []
                if isinstance(item, Mapping)
            }
            status = str(view.get("reconstruction_status") or "NOT_DERIVED")
            _draw_deal(
                canvas,
                x=28,
                y=31,
                width=389,
                height=218,
                deal=reconstructed,
                title=f"Достроенный расклад - {status}",
                derived_seats=derived_seats,
                note=(
                    "DERIVED допускается только как точное 39-to-13 вычитание колоды"
                    if derived_seats
                    else "Недостающие карты не угадываются"
                ),
            )
            _draw_auction(
                canvas,
                x=425,
                y=31,
                width=389,
                height=218,
                auction=view.get("auction") or {},
            )
            _draw_footer(canvas, page_width=page_width, page_number=page_number, page_count=len(pages))
            canvas.showPage()
        canvas.save()
        _validate_pdf(temporary, expected_pages=len(pages))
        temporary.replace(output_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {
        "schema": SCHEMA,
        "output": output_path.name,
        "pages": len(pages),
        "deals": len(views),
        "empty_observation_page": not bool(views),
        "screenshots_embedded": screenshots_embedded,
        "sha256": _sha256(output_path),
        "size_bytes": output_path.stat().st_size,
        "result_scope": "SHADOW_ONLY",
        "canonical_promotion_allowed": False,
    }


__all__ = ["REPORT_NAME", "SCHEMA", "ShadowPdfError", "render_shadow_pdf"]
