"""Evidence-preserving deal-review pages for the stable 3.1 FREE PDF.

The renderer is deliberately presentation-only. It accepts cards already held
in the master-analysis deal contract, preserves observed and human-verified
provenance, and never reconstructs a hidden hand or promotes review data to
school canon.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from bridge_contracts.video_deal import SEATS, canonicalize_video_deal

SCHEMA = "bridge-3.1-free-deal-review-pdf/v2"
SUITS = ("S", "H", "D", "C")
SUIT_SYMBOLS = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
RANK_ORDER = "AKQJT98765432"
CALL_RE = re.compile(r"^(?:PASS|X|XX|[1-7](?:C|D|H|S|NT))$", re.I)
MAX_DISPLAYED_AUCTION_CALLS = 24


class DealReviewPdfError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _register_fonts() -> None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    regular = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    bold = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    if not regular.is_file() or not bold.is_file():
        raise DealReviewPdfError("DejaVu Sans fonts are unavailable")
    if "DealReviewSans" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("DealReviewSans", str(regular)))
    if "DealReviewSans-Bold" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("DealReviewSans-Bold", str(bold)))


def _hand_cards(value: Any, seat: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        value = value.get("cards") or []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise DealReviewPdfError(f"deal hand {seat} must be an array, object, or null")
    return list(value)


def _normalise_hands(deal: Mapping[str, Any]) -> dict[str, list[str]]:
    raw = deal.get("hands") or {}
    if not isinstance(raw, Mapping):
        raise DealReviewPdfError("deal hands must be an object")
    return {seat: _hand_cards(raw.get(seat), seat) for seat in SEATS}


def _verified_seats(deal: Mapping[str, Any], shot: Mapping[str, Any]) -> set[str]:
    verification = deal.get("verification") or {}
    if not isinstance(verification, Mapping):
        raise DealReviewPdfError("deal verification must be an object")
    if str(verification.get("status") or "").upper() != "HUMAN_VERIFIED":
        return set()
    seats = verification.get("verified_seats") or []
    if not isinstance(seats, Sequence) or isinstance(seats, (str, bytes)):
        raise DealReviewPdfError("verified_seats must be an array")
    result = {str(seat).upper() for seat in seats}
    if not result.issubset(set(SEATS)):
        raise DealReviewPdfError("verified_seats contains an unsupported seat")
    method = str(verification.get("method") or "").strip()
    reviewer = str(verification.get("reviewer") or "").strip()
    verified_at = str(verification.get("verified_at") or "").strip()
    reference_sha = str(verification.get("reference_frame_sha256") or "").strip().lower()
    if not method or not reviewer:
        raise DealReviewPdfError("human verification lacks method or reviewer")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", verified_at):
        raise DealReviewPdfError("human verification lacks an exact UTC timestamp")
    if not re.fullmatch(r"[0-9a-f]{64}", reference_sha):
        raise DealReviewPdfError("human verification lacks a valid reference frame SHA-256")
    if not isinstance(shot.get("path"), Path) or shot.get("sha256") != reference_sha:
        raise DealReviewPdfError("human verification does not match the hash-bound screenshot")
    return result


def _normalise_auction(deal: Mapping[str, Any]) -> dict[str, Any]:
    raw = deal.get("auction")
    if raw is None:
        return {"status": "UNAVAILABLE", "dealer": deal.get("dealer"), "calls": []}
    if isinstance(raw, Mapping):
        calls = raw.get("calls") or []
        dealer = raw.get("dealer") or deal.get("dealer")
        status = str(raw.get("status") or "REVIEW")
    else:
        calls = raw
        dealer = deal.get("dealer")
        status = "REVIEW"
    if not isinstance(calls, Sequence) or isinstance(calls, (str, bytes)):
        raise DealReviewPdfError("auction calls must be an array")
    normalised = [str(call).strip().upper() for call in calls]
    if any(not CALL_RE.fullmatch(call) for call in normalised):
        raise DealReviewPdfError("auction contains an invalid call")
    dealer_text = str(dealer or "").upper()
    if dealer_text and dealer_text not in SEATS:
        raise DealReviewPdfError("auction dealer must be N, E, S, or W")
    return {"status": status[:48], "dealer": dealer_text, "calls": normalised}


def _evidence_ids(deal: Mapping[str, Any]) -> list[str]:
    evidence = deal.get("evidence") or []
    if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)):
        raise DealReviewPdfError("deal evidence must be an array")
    result = []
    for item in evidence:
        if isinstance(item, Mapping):
            value = item.get("evidence_id") or item.get("id")
        else:
            value = item
        if value:
            result.append(str(value))
    return result


def _safe_shot(shot: Mapping[str, Any] | None) -> dict[str, Any]:
    if not shot:
        return {"path": None, "sha256": "", "time": None, "evidence_id": ""}
    path_text = str(shot.get("path") or "").strip()
    expected = str(shot.get("sha256") or "").strip().lower()
    path = Path(path_text) if path_text else None
    if (
        path is None
        or not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
        or len(expected) != 64
        or _sha256(path) != expected
    ):
        path = None
    return {
        "path": path,
        "sha256": expected,
        "time": shot.get("time"),
        "evidence_id": str(shot.get("evidence_id") or ""),
    }


def build_deal_review_views(master: Mapping[str, Any], shots: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Build strict display views without adding evidence or card identities."""
    deals = master.get("deals") or []
    if not isinstance(deals, Sequence) or isinstance(deals, (str, bytes)):
        raise DealReviewPdfError("master deals must be an array")
    by_id = {
        str(shot.get("evidence_id")): shot
        for shot in shots
        if isinstance(shot, Mapping) and shot.get("evidence_id")
    }
    views = []
    for ordinal, deal in enumerate(deals, start=1):
        if not isinstance(deal, Mapping):
            raise DealReviewPdfError("master deal must be an object")
        hands = _normalise_hands(deal)
        try:
            observed = canonicalize_video_deal({"hands": hands}).to_dict()
        except Exception as exc:
            raise DealReviewPdfError("deal cards violate the canonical 52-card contract") from exc
        observed_count = sum(len(observed["hands"][seat]["cards"]) for seat in SEATS)
        evidence_status = "OBSERVED_COMPLETE" if observed_count == 52 else "PARTIAL_OBSERVATION"
        chosen = next((by_id[item] for item in _evidence_ids(deal) if item in by_id), None)
        safe_shot = _safe_shot(chosen)
        views.append({
            "board_number": deal.get("board_number") or ordinal,
            "deal_id": str(deal.get("deal_id") or f"deal-{ordinal}"),
            "status": str(deal.get("status") or "REVIEW"),
            "observed": observed,
            "observed_count": observed_count,
            "evidence_deal": observed,
            "evidence_status": evidence_status,
            "verified_seats": _verified_seats(deal, safe_shot),
            "auction": _normalise_auction(deal),
            "shot": safe_shot,
        })
    return views


def _panel(canvas: Any, x: float, y: float, width: float, height: float, title: str) -> None:
    from reportlab.lib.colors import HexColor

    canvas.setFillColor(HexColor("#F7F8FA"))
    canvas.setStrokeColor(HexColor("#D9DEE7"))
    canvas.roundRect(x, y, width, height, 7, fill=1, stroke=1)
    canvas.setFillColor(HexColor("#172033"))
    canvas.setFont("DealReviewSans-Bold", 8.2)
    canvas.drawString(x + 8, y + height - 14, title[:94])


def _hand_lines(cards: Sequence[str], unknown_count: int) -> list[tuple[str, str]]:
    by_suit = {suit: [] for suit in SUITS}
    for card in cards:
        if isinstance(card, str) and len(card) == 2 and card[1] in by_suit:
            by_suit[card[1]].append(card[0])
    result = []
    for suit in SUITS:
        ranks = "".join(rank for rank in RANK_ORDER if rank in by_suit[suit]) or "-"
        result.append((suit, f"{SUIT_SYMBOLS[suit]} {ranks}"))
    if unknown_count:
        result.append(("?", f"? x{unknown_count}"))
    return result


def _draw_hand(
    canvas: Any,
    *,
    x: float,
    y: float,
    seat: str,
    hand: Mapping[str, Any],
    evidence_class: str,
    compact: bool,
) -> None:
    from reportlab.lib.colors import HexColor

    colour = "#667085" if evidence_class == "UNKNOWN" else "#0B6B4B"
    canvas.setFillColor(HexColor(colour))
    canvas.setFont("DealReviewSans-Bold", 6.8 if compact else 7.4)
    canvas.drawString(x, y, f"{seat} - {evidence_class}")
    step = 7.0 if compact else 8.0
    baseline = y - step
    for suit, line in _hand_lines(
        list(hand.get("cards") or []), int(hand.get("unknown_count") or 0)
    ):
        if suit in {"H", "D"}:
            text_colour = "#C52A35"
        else:
            text_colour = "#202838"
        canvas.setFillColor(HexColor(text_colour))
        canvas.setFont("DealReviewSans", 6.5 if compact else 7.0)
        canvas.drawString(x, baseline, line[:28])
        baseline -= step


def _draw_deal(
    canvas: Any,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    deal: Mapping[str, Any],
    title: str,
    verified_seats: set[str],
    note: str,
) -> None:
    from reportlab.lib.colors import HexColor

    _panel(canvas, x, y, width, height, title)
    hands = deal.get("hands") or {}
    compact = height < 230
    hand_width = width * (0.42 if compact else 0.46)

    def evidence_class(seat: str) -> str:
        if not (hands.get(seat) or {}).get("cards"):
            return "UNKNOWN"
        return "HUMAN_VERIFIED" if seat in verified_seats else "OBSERVED"

    placements = {
        "N": (x + (width - hand_width) / 2, y + height - 30),
        "W": (x + 9, y + height * 0.58),
        "E": (x + width - hand_width + 1, y + height * 0.58),
        "S": (x + (width - hand_width) / 2, y + 62),
    }
    for seat in SEATS:
        hand_x, hand_y = placements[seat]
        _draw_hand(
            canvas,
            x=hand_x,
            y=hand_y,
            seat=seat,
            hand=hands.get(seat) or {},
            evidence_class=evidence_class(seat),
            compact=compact,
        )
    canvas.setFillColor(HexColor("#667085"))
    canvas.setFont("DealReviewSans", 5.8)
    canvas.drawString(x + 8, y + 7, note[:108])


def _draw_screenshot(canvas: Any, *, x: float, y: float, width: float, height: float, shot: Mapping[str, Any]) -> bool:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.utils import ImageReader

    _panel(canvas, x, y, width, height, "Исходный кадр - SHA-256-bound evidence")
    image_x, image_y = x + 7, y + 7
    image_width, image_height = width - 14, height - 27
    frame = shot.get("path")
    if not isinstance(frame, Path):
        canvas.setFillColor(HexColor("#E8EBF0"))
        canvas.rect(image_x, image_y, image_width, image_height, fill=1, stroke=0)
        canvas.setFillColor(HexColor("#667085"))
        canvas.setFont("DealReviewSans", 8)
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
        return True
    except Exception:
        canvas.setFillColor(HexColor("#E8EBF0"))
        canvas.rect(image_x, image_y, image_width, image_height, fill=1, stroke=0)
        canvas.setFillColor(HexColor("#667085"))
        canvas.setFont("DealReviewSans", 8)
        canvas.drawCentredString(x + width / 2, y + height / 2, "Файл подтвержден, но изображение не декодируется")
        return False


def _draw_auction(canvas: Any, *, x: float, y: float, width: float, height: float, auction: Mapping[str, Any]) -> None:
    from reportlab.lib.colors import HexColor

    _panel(canvas, x, y, width, height, "Торговля - подтвержденные наблюдения")
    status = str(auction.get("status") or "UNAVAILABLE")
    calls = list(auction.get("calls") or [])
    dealer = str(auction.get("dealer") or "")
    canvas.setFillColor(HexColor("#475467"))
    canvas.setFont("DealReviewSans", 6.5)
    canvas.drawString(x + 8, y + height - 27, f"Статус: {status}   Сдающий: {dealer or '?'}")
    if not calls or dealer not in SEATS:
        canvas.setFillColor(HexColor("#667085"))
        canvas.setFont("DealReviewSans", 8)
        canvas.drawString(x + 8, y + height - 44, "Нет подтвержденной последовательности заявок")
        return
    displayed = calls[:MAX_DISPLAYED_AUCTION_CALLS]
    dealer_index = SEATS.index(dealer)
    grid_x = x + 8
    grid_y_top = y + height - 39
    cell_width = (width - 16) / 4
    row_height = 12
    canvas.setFont("DealReviewSans-Bold", 6.5)
    for column, seat in enumerate(SEATS):
        canvas.setFillColor(HexColor("#344054"))
        canvas.drawCentredString(grid_x + cell_width * (column + 0.5), grid_y_top, seat)
    rows: list[list[str]] = []
    for index, call in enumerate(displayed):
        absolute = dealer_index + index
        row, column = divmod(absolute, 4)
        while len(rows) <= row:
            rows.append([""] * 4)
        rows[row][column] = "Pass" if call == "PASS" else call
    for row_index, row in enumerate(rows):
        baseline = grid_y_top - row_height * (row_index + 1)
        if baseline < y + 17:
            break
        for column, call in enumerate(row):
            canvas.setFillColor(HexColor("#101828"))
            canvas.setFont("DealReviewSans", 6.5)
            canvas.drawCentredString(grid_x + cell_width * (column + 0.5), baseline, call)


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


def _render_appendix(path: Path, views: Sequence[Mapping[str, Any]], source: str) -> int:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.pdfgen.canvas import Canvas

    _register_fonts()
    page_width, page_height = landscape(A4)
    screenshots = 0
    canvas = Canvas(str(path), pagesize=(page_width, page_height), pageCompression=1)
    canvas.setTitle("Bridge Video 3.1 FREE deal review")
    canvas.setSubject("Evidence review; no automatic canon promotion")
    for page_number, view in enumerate(views, start=1):
        canvas.setFillColor(HexColor("#101828"))
        canvas.rect(0, page_height - 48, page_width, 48, fill=1, stroke=0)
        canvas.setFillColor(HexColor("#FFFFFF"))
        canvas.setFont("DealReviewSans-Bold", 14)
        canvas.drawString(
            28,
            page_height - 29,
            f"Сдача {view.get('board_number') or '?'}  |  {_timestamp((view.get('shot') or {}).get('time'))}  |  3.1 FREE - DEAL REVIEW",
        )
        canvas.setFont("DealReviewSans", 6.8)
        canvas.drawRightString(page_width - 28, page_height - 27, source[:62])
        if _draw_screenshot(canvas, x=28, y=257, width=500, height=277, shot=view.get("shot") or {}):
            screenshots += 1
        _draw_deal(
            canvas,
            x=536,
            y=257,
            width=278,
            height=277,
            deal=view.get("observed") or {},
            title=f"Распознано (OBSERVED / HUMAN_VERIFIED) - {int(view.get('observed_count') or 0)}/52",
            verified_seats=set(view.get("verified_seats") or set()),
            note="Показываются только сохраненные наблюдения; подтверждение помечено отдельно",
        )
        _draw_deal(
            canvas,
            x=28,
            y=31,
            width=389,
            height=218,
            deal=view.get("evidence_deal") or {},
            title=f"Проверяемая полнота - {view.get('evidence_status')}",
            verified_seats=set(view.get("verified_seats") or set()),
            note="Скрытые и недостающие карты остаются UNKNOWN; 39-to-13 запрещено",
        )
        _draw_auction(
            canvas,
            x=425,
            y=31,
            width=389,
            height=218,
            auction=view.get("auction") or {},
        )
        canvas.setFillColor(HexColor("#667085"))
        canvas.setFont("DealReviewSans", 6)
        canvas.drawString(28, 15, "EVIDENCE REVIEW - HUMAN_VERIFIED/OBSERVED is not automatic SCHOOL CANON; hidden cards stay UNKNOWN")
        canvas.drawRightString(page_width - 28, 15, f"{page_number}/{len(views)}")
        canvas.showPage()
    canvas.save()
    return screenshots


def _validate_tail(pdf: Path, expected_pages: int) -> None:
    import fitz

    document = fitz.open(pdf)
    try:
        if document.page_count < expected_pages:
            raise DealReviewPdfError("deal-review PDF page count mismatch")
        for page in list(document)[-expected_pages:]:
            if page.rect.width <= page.rect.height:
                raise DealReviewPdfError("deal-review page is not landscape")
            text = page.get_text()
            for marker in (
                "3.1 FREE - DEAL REVIEW",
                "Распознано",
                "Проверяемая полнота",
                "Торговля",
                "EVIDENCE REVIEW",
            ):
                if marker not in text:
                    raise DealReviewPdfError(f"deal-review page is missing marker: {marker}")
    finally:
        document.close()


def append_deal_review_pages(
    pdf_path: Path,
    *,
    master: Mapping[str, Any],
    shots: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Append one landscape evidence-review page per master deal atomically."""
    import fitz

    views = build_deal_review_views(master, shots)
    if not views:
        return {
            "schema": SCHEMA,
            "pages": 0,
            "deals": 0,
            "screenshots_embedded": 0,
            "canon_promotion_performed": False,
            "hidden_hand_reconstruction_performed": False,
        }
    pdf_path = Path(pdf_path)
    source = str((master.get("source") or {}).get("name") or "Bridge Video")
    with tempfile.TemporaryDirectory(prefix="bridge-deal-review-", dir=pdf_path.parent) as temporary_dir:
        appendix_path = Path(temporary_dir) / "appendix.pdf"
        merged_path = Path(temporary_dir) / "merged.pdf"
        screenshots = _render_appendix(appendix_path, views, source)
        document = fitz.open(pdf_path)
        appendix = fitz.open(appendix_path)
        try:
            document.insert_pdf(appendix)
            document.save(merged_path, garbage=4, deflate=True)
        finally:
            appendix.close()
            document.close()
        _validate_tail(merged_path, len(views))
        os.replace(merged_path, pdf_path)
    return {
        "schema": SCHEMA,
        "pages": len(views),
        "deals": len(views),
        "screenshots_embedded": screenshots,
        "canon_promotion_performed": False,
        "reconstruction_rule": "PROHIBITED_HIDDEN_CARDS_REMAIN_UNKNOWN",
        "hidden_hand_reconstruction_performed": False,
    }


__all__ = [
    "SCHEMA",
    "DealReviewPdfError",
    "append_deal_review_pages",
    "build_deal_review_views",
]
