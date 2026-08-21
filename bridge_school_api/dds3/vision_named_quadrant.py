"""Local/free fail-closed extractor for VuBridge named-quadrant diagrams.

The supported family prints the full seat names around the deal: North above, West/East
on the middle band, and South below. Each seat has four visible S/H/D/C holding rows and
the page has an explicit Board/Dealer/Vulnerable header. Seat identity is taken only
from the printed word. Cards must form the exact standard 52-card deck; no deck
complement, board-derived metadata, or bridge inference repair is permitted.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any

from .screenshot import ObservedField, ScreenshotDealObservation
from .vision_publication import (
    PublicationVisionError,
    _clean_rank_text,
    _cluster_rows,
    _deps,
    _extract_metadata,
    _rank_tokens,
    _row_center,
)


class NamedQuadrantVisionError(ValueError):
    pass


def _decode_named(image_bytes: bytes, cv2: Any, np: Any) -> Any:
    if not image_bytes:
        raise NamedQuadrantVisionError("EMPTY_IMAGE")
    image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise NamedQuadrantVisionError("IMAGE_DECODE_FAILED")
    height, width = image.shape[:2]
    if width < 250 or height < 180:
        raise NamedQuadrantVisionError("IMAGE_TOO_SMALL")
    target_width = 1400
    if width != target_width:
        scale = target_width / float(width)
        image = cv2.resize(
            image,
            (target_width, max(1, round(height * scale))),
            interpolation=cv2.INTER_CUBIC if scale >= 1 else cv2.INTER_AREA,
        )
    return image


def _heading_candidates(image: Any, pytesseract: Any) -> dict[str, list[tuple[float, float, float]]]:
    data = pytesseract.image_to_data(image, config="--psm 11", output_type=pytesseract.Output.DICT)
    labels: dict[str, list[tuple[float, float, float]]] = {seat: [] for seat in "NWES"}
    mapping = {"NORTH": "N", "WEST": "W", "EAST": "E", "SOUTH": "S"}
    for index, raw in enumerate(data["text"]):
        seat = mapping.get(re.sub(r"[^A-Za-z]", "", raw).upper())
        if seat is None:
            continue
        try:
            conf = max(0.0, min(1.0, float(data["conf"][index]) / 100.0))
        except (TypeError, ValueError):
            conf = 0.0
        if conf < 0.15:
            continue
        x = int(data["left"][index]); y = int(data["top"][index])
        w = int(data["width"][index]); h = int(data["height"][index])
        labels[seat].append((x + w / 2, y + h / 2, conf))
    return labels


def _four_column_headings(image: Any, pytesseract: Any) -> dict[str, tuple[float, float, float]]:
    """Resolve the four printed seat headings by named-quadrant geometry.

    The historical function name is kept for compatibility with the field diagnostic,
    but this family is a cross/quadrant layout, not four headings on one horizontal row.
    Metadata text such as ``Dealer: North`` or ``By: East`` is deliberately rejected by
    requiring the complete N-above / W-E-middle / S-below geometry.
    """
    labels = _heading_candidates(image, pytesseract)
    if any(not labels[seat] for seat in "NWES"):
        raise NamedQuadrantVisionError("UNSUPPORTED_LAYOUT_NAMED_QUADRANT_NO_HEADINGS")
    height, width = image.shape[:2]
    best: tuple[float, dict[str, tuple[float, float, float]]] | None = None
    for n in labels["N"]:
        for w in labels["W"]:
            for e in labels["E"]:
                for s in labels["S"]:
                    middle_y = (w[1] + e[1]) / 2.0
                    center_x = (n[0] + s[0]) / 2.0
                    if abs(w[1] - e[1]) > height * 0.075:
                        continue
                    if abs(n[0] - s[0]) > width * 0.075:
                        continue
                    if not (n[1] < middle_y - height * 0.10):
                        continue
                    if not (s[1] > middle_y + height * 0.10):
                        continue
                    if not (w[0] < center_x - width * 0.12):
                        continue
                    if not (e[0] > center_x + width * 0.12):
                        continue
                    if not (w[0] < n[0] < e[0] and w[0] < s[0] < e[0]):
                        continue
                    min_conf = min(n[2], w[2], e[2], s[2])
                    horizontal_balance = abs((center_x - w[0]) - (e[0] - center_x))
                    vertical_balance = abs((middle_y - n[1]) - (s[1] - middle_y))
                    score = (
                        abs(w[1] - e[1])
                        + abs(n[0] - s[0])
                        + 0.20 * horizontal_balance
                        + 0.10 * vertical_balance
                        - 25 * min_conf
                    )
                    group = {"N": n, "W": w, "E": e, "S": s}
                    if best is None or score < best[0]:
                        best = (score, group)
    if best is None:
        raise NamedQuadrantVisionError("UNSUPPORTED_LAYOUT_NAMED_QUADRANT_GEOMETRY")
    return best[1]


def _column_bounds(headings: dict[str, tuple[float, float, float]], width: int) -> dict[str, tuple[int, int]]:
    """Return conservative seat-local horizontal windows.

    North and South intentionally share the center column, so ordinary four-column
    midpoint partitioning is invalid. Width is instead bounded by the distance from the
    seat to the nearest lateral/central neighbour and never spans another hand.
    """
    out: dict[str, tuple[int, int]] = {}
    for seat in "NESW":
        x = headings[seat][0]
        if seat in "NS":
            neighbour_distance = min(abs(x - headings["W"][0]), abs(headings["E"][0] - x))
        else:
            neighbour_distance = min(abs(x - headings["N"][0]), abs(x - headings["S"][0]))
        half = max(width * 0.075, min(width * 0.18, neighbour_distance * 0.44))
        out[seat] = (max(0, int(x - half)), min(width, int(x + half)))
    return out


def _seat_rank_rows(
    image: Any,
    *,
    seat: str,
    headings: dict[str, tuple[float, float, float]],
    bounds: dict[str, tuple[int, int]],
    pytesseract: Any,
) -> list[float]:
    """Locate four holding rows directly below one printed seat heading."""
    height, width = image.shape[:2]
    x0, x1 = bounds[seat]
    heading_y = headings[seat][1]
    tokens = [
        token
        for token in _rank_tokens(image, pytesseract)
        if x0 <= token["cx"] <= x1
        and heading_y + height * 0.015 <= token["cy"] <= heading_y + height * 0.20
    ]
    rows = _cluster_rows(tokens, tolerance=max(14.0, width * 0.014))
    centers = [_row_center(row) for row in rows if row]
    selected: list[float] = []
    for center in sorted(centers):
        if selected and center - selected[-1] < height * 0.025:
            continue
        selected.append(center)
        if len(selected) == 4:
            break
    if len(selected) != 4:
        raise NamedQuadrantVisionError(f"INCOMPLETE_NAMED_HAND_ROWS:{seat}:{len(selected)}")
    return selected


def _global_rank_rows(
    image: Any,
    headings: dict[str, tuple[float, float, float]],
    pytesseract: Any,
) -> list[float]:
    """Compatibility diagnostic: return North's four detected holding rows."""
    bounds = _column_bounds(headings, image.shape[1])
    return _seat_rank_rows(
        image,
        seat="N",
        headings=headings,
        bounds=bounds,
        pytesseract=pytesseract,
    )


def _ocr_rank_row(
    image: Any,
    *,
    x0: int,
    x1: int,
    center_y: float,
    pytesseract: Any,
    cv2: Any,
) -> tuple[str, float]:
    """OCR one isolated holding row after the printed suit-glyph column.

    In this bounded VuBridge family the seat-local window deliberately starts well left
    of the suit glyph. Pixel diagnostics show the holding itself begins around 30% into
    that window. Starting the OCR to the right of that stable glyph zone prevents a suit
    symbol from being hallucinated as a rank; no missing rank is supplied from deck
    inventory and the final 13-card/52-card gates remain authoritative.
    """
    height, _ = image.shape[:2]
    half = max(24, int(height * 0.024))
    y0 = max(0, int(center_y - half)); y1 = min(height, int(center_y + half))
    column_width = x1 - x0
    readings: list[str] = []
    for left_fraction in (0.28, 0.30, 0.32, 0.34):
        rx0 = min(x1 - 1, x0 + int(column_width * left_fraction))
        crop = image[y0:y1, rx0:x1]
        if crop.size == 0:
            continue
        crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        for source in (gray, binary):
            for psm in (7, 13):
                value = _clean_rank_text(
                    pytesseract.image_to_string(
                        source,
                        config=f"--psm {psm} -c tessedit_char_whitelist=AKQJT9876543210",
                    )
                )
                if value:
                    readings.append(value)
    if not readings:
        raise NamedQuadrantVisionError("INCOMPLETE_NAMED_HAND_ROW")
    counts = Counter(readings)
    best, support = counts.most_common(1)[0]
    if len(counts) > 1 and support < 2:
        raise NamedQuadrantVisionError(f"AMBIGUOUS_CARD_OCR:{sorted(counts)}")
    confidence = min(0.92, 0.58 + 0.04 * support)
    return best, confidence


def _extract_four_column_hands(
    image: Any,
    headings: dict[str, tuple[float, float, float]],
    pytesseract: Any,
    cv2: Any,
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, float]]]:
    _, width = image.shape[:2]
    bounds = _column_bounds(headings, width)
    hands: dict[str, dict[str, str]] = {}
    confidence: dict[str, dict[str, float]] = {}
    cards: list[str] = []
    for seat in "NESW":
        x0, x1 = bounds[seat]
        row_centers = _seat_rank_rows(
            image,
            seat=seat,
            headings=headings,
            bounds=bounds,
            pytesseract=pytesseract,
        )
        holdings: list[str] = []
        row_confidence: list[float] = []
        for center_y in row_centers:
            holding, conf = _ocr_rank_row(
                image,
                x0=x0,
                x1=x1,
                center_y=center_y,
                pytesseract=pytesseract,
                cv2=cv2,
            )
            holdings.append(holding)
            row_confidence.append(conf)
        if sum(len(value) for value in holdings) != 13:
            raise NamedQuadrantVisionError(f"INCOMPLETE_HAND:{seat}:{'.'.join(holdings)}")
        hands[seat] = dict(zip("SHDC", holdings, strict=True))
        confidence[seat] = dict(zip("SHDC", row_confidence, strict=True))
        for suit, ranks in zip("SHDC", holdings, strict=True):
            cards.extend(suit + rank for rank in ranks)

    expected = {suit + rank for suit in "SHDC" for rank in "AKQJT98765432"}
    if len(cards) != 52 or len(set(cards)) != 52:
        raise NamedQuadrantVisionError(f"DECK_VALIDATION_FAILED:{len(cards)}/{len(set(cards))}")
    if set(cards) != expected:
        raise NamedQuadrantVisionError("DECK_VALIDATION_FAILED:NOT_STANDARD_DECK")
    return hands, confidence


def extract_named_quadrant_observation(
    image_bytes: bytes, *, media_type: str, filename: str | None = None
) -> ScreenshotDealObservation:
    cv2, np, pytesseract = _deps()
    image = _decode_named(image_bytes, cv2, np)
    headings = _four_column_headings(image, pytesseract)
    hands, hand_confidence = _extract_four_column_hands(image, headings, pytesseract, cv2)
    try:
        board, dealer, vulnerability, metadata_confidence = _extract_metadata(image, pytesseract)
    except PublicationVisionError as exc:
        message = str(exc)
        if message.startswith("METADATA_OCR_FAILED"):
            raise NamedQuadrantVisionError("UNSUPPORTED_LAYOUT_NAMED_QUADRANT_NO_METADATA_HEADER") from exc
        raise NamedQuadrantVisionError(message) from exc

    image_sha256 = hashlib.sha256(image_bytes).hexdigest()
    source = "local_tesseract_named_quadrant_v1"
    return ScreenshotDealObservation(
        hands=hands,
        board_number=ObservedField(board, confidence=metadata_confidence, source=source),
        dealer=ObservedField(dealer, confidence=metadata_confidence, source=source),
        vulnerability=ObservedField(vulnerability, confidence=metadata_confidence, source=source),
        hand_confidence=hand_confidence,
        extra_metadata={
            "vision_extractor": ObservedField(source, confidence=1.0, source="runtime"),
            "layout_family": ObservedField("named_quadrant", confidence=1.0, source="runtime"),
            "image_sha256": ObservedField(image_sha256, confidence=1.0, source="runtime"),
            "filename": ObservedField(filename, confidence=1.0, source="runtime"),
            "media_type": ObservedField(media_type, confidence=1.0, source="runtime"),
        },
    )
