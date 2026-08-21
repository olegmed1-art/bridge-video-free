"""Local/free fail-closed extractor for named-seat publication diagrams.

This bounded family has explicit ``North``, ``West``, ``East`` and ``South`` headings
above four suit rows for each hand plus an explicit Board/Dealer/Vulnerable header.
The printed seat labels may be arranged either as a classic cross or as a strict vertical
N→W→E→S publication stack; seat identity always comes from those detected labels.
Cards are read from pixels and must form the exact standard 52-card deck; no
deck-complement or bridge inference repair is permitted.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from .screenshot import ObservedField, ScreenshotDealObservation
from .vision_publication import (
    PublicationVisionError,
    _decode,
    _extract_metadata,
    _glyph_column,
    _ocr_holding_row,
    _rank_tokens,
    _cluster_rows,
    _row_center,
    _deps,
)


class NamedQuadrantVisionError(ValueError):
    pass


def _heading_candidates(image: Any, pytesseract: Any) -> dict[str, list[tuple[float, float, float]]]:
    data = pytesseract.image_to_data(
        image, config="--psm 11", output_type=pytesseract.Output.DICT
    )
    labels: dict[str, list[tuple[float, float, float]]] = {
        seat: [] for seat in ("N", "W", "E", "S")
    }
    mapping = {"NORTH": "N", "WEST": "W", "EAST": "E", "SOUTH": "S"}
    for index, raw in enumerate(data["text"]):
        cleaned = re.sub(r"[^A-Za-z]", "", raw).upper()
        seat = mapping.get(cleaned)
        if seat is None:
            continue
        try:
            conf = max(0.0, min(1.0, float(data["conf"][index]) / 100.0))
        except (TypeError, ValueError):
            conf = 0.0
        if conf < 0.15:
            continue
        x = int(data["left"][index]); y = int(data["top"][index])
        width = int(data["width"][index]); height = int(data["height"][index])
        labels[seat].append((x + width / 2, y + height / 2, conf))
    return labels


def _seat_headings(image: Any, pytesseract: Any) -> dict[str, tuple[float, float, float]]:
    labels = _heading_candidates(image, pytesseract)
    if any(not labels[seat] for seat in "NWES"):
        raise NamedQuadrantVisionError("UNSUPPORTED_LAYOUT_NAMED_QUADRANT_NO_HEADINGS")

    height, width = image.shape[:2]
    best: tuple[float, dict[str, tuple[float, float, float]]] | None = None
    for north in labels["N"]:
        for west in labels["W"]:
            for east in labels["E"]:
                for south in labels["S"]:
                    candidate = {"N": north, "W": west, "E": east, "S": south}
                    min_conf = min(north[2], west[2], east[2], south[2])

                    # Geometry A: conventional cross, N above W/E and S below them.
                    if (
                        north[1] < west[1]
                        and north[1] < east[1]
                        and south[1] > west[1]
                        and south[1] > east[1]
                        and west[0] < east[0]
                    ):
                        center_x = (west[0] + east[0]) / 2
                        if (
                            abs(north[0] - center_x) <= width * 0.25
                            and abs(south[0] - center_x) <= width * 0.25
                            and abs(west[1] - east[1]) <= height * 0.16
                            and east[0] - west[0] >= width * 0.20
                        ):
                            score = (
                                abs(north[0] - south[0])
                                + abs(west[1] - east[1])
                                + 0.25 * abs((north[1] + south[1]) / 2 - (west[1] + east[1]) / 2)
                                - 20.0 * min_conf
                            )
                            if best is None or score < best[0]:
                                best = (score, candidate)

                    # Geometry B: legacy VuBridge HTML/PDF stack. The four complete
                    # hands are printed in strict N→W→E→S order down one narrow column.
                    # This is still fail-closed: all four labels must be explicit, nearly
                    # column-aligned, separated vertically, and the later deck gate must
                    # independently prove all 52 standard cards.
                    ys = [north[1], west[1], east[1], south[1]]
                    xs = [north[0], west[0], east[0], south[0]]
                    gaps = [ys[i + 1] - ys[i] for i in range(3)]
                    if (
                        ys == sorted(ys)
                        and min(gaps) >= height * 0.08
                        and max(xs) - min(xs) <= width * 0.20
                    ):
                        mean_x = sum(xs) / 4
                        alignment = sum(abs(x - mean_x) for x in xs)
                        gap_spread = max(gaps) - min(gaps)
                        score = 1000.0 + alignment + 0.20 * gap_spread - 20.0 * min_conf
                        if best is None or score < best[0]:
                            best = (score, candidate)

    if best is None:
        raise NamedQuadrantVisionError("UNSUPPORTED_LAYOUT_NAMED_QUADRANT_GEOMETRY")
    return best[1]


def _rows_for_heading(
    tokens: list[dict[str, Any]],
    heading: tuple[float, float, float],
    *,
    image_width: int,
    image_height: int,
) -> list[list[dict[str, Any]]]:
    hx, hy, _ = heading
    candidates = [
        token
        for token in tokens
        if abs(token["cx"] - hx) <= image_width * 0.22
        and hy - image_height * 0.025 <= token["cy"] <= hy + image_height * 0.25
    ]
    rows = _cluster_rows(candidates, tolerance=max(12.0, image_width * 0.018))
    rows = [row for row in rows if _row_center(row) >= hy - image_height * 0.025]
    rows.sort(key=lambda row: (_row_center(row), min(item["x"] for item in row)))
    if len(rows) < 4:
        raise NamedQuadrantVisionError("INCOMPLETE_NAMED_HAND_ROWS")
    # The four suit rows are the four closest row clusters below the seat heading.
    rows = sorted(rows, key=lambda row: abs(_row_center(row) - hy))[:4]
    rows.sort(key=_row_center)
    if len(rows) != 4:
        raise NamedQuadrantVisionError("INCOMPLETE_NAMED_HAND_ROWS")
    return rows


def _extract_named_hands(
    image: Any,
    headings: dict[str, tuple[float, float, float]],
    pytesseract: Any,
    cv2: Any,
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, float]]]:
    tokens = _rank_tokens(image, pytesseract)
    height, width = image.shape[:2]
    raw_rows = {
        seat: _rows_for_heading(
            tokens,
            headings[seat],
            image_width=width,
            image_height=height,
        )
        for seat in "NESW"
    }
    glyph_x = {seat: _glyph_column(rows) for seat, rows in raw_rows.items()}

    hands: dict[str, dict[str, str]] = {}
    confidence: dict[str, dict[str, float]] = {}
    cards: list[str] = []
    for seat in "NESW":
        holdings: list[str] = []
        row_confidence: list[float] = []
        for row in raw_rows[seat]:
            value, conf = _ocr_holding_row(
                image, row, glyph_x[seat], pytesseract, cv2
            )
            holdings.append(value)
            row_confidence.append(conf)
        if sum(len(value) for value in holdings) != 13:
            raise NamedQuadrantVisionError(
                f"INCOMPLETE_HAND:{seat}:{'.'.join(holdings)}"
            )
        hands[seat] = dict(zip("SHDC", holdings, strict=True))
        confidence[seat] = dict(zip("SHDC", row_confidence, strict=True))
        for suit, ranks in zip("SHDC", holdings, strict=True):
            cards.extend(suit + rank for rank in ranks)

    expected = {suit + rank for suit in "SHDC" for rank in "AKQJT98765432"}
    if len(cards) != 52 or len(set(cards)) != 52:
        raise NamedQuadrantVisionError(
            f"DECK_VALIDATION_FAILED:{len(cards)}/{len(set(cards))}"
        )
    if set(cards) != expected:
        raise NamedQuadrantVisionError("DECK_VALIDATION_FAILED:NOT_STANDARD_DECK")
    return hands, confidence


def extract_named_quadrant_observation(
    image_bytes: bytes, *, media_type: str, filename: str | None = None
) -> ScreenshotDealObservation:
    cv2, np, pytesseract = _deps()
    image = _decode(image_bytes, cv2, np)
    headings = _seat_headings(image, pytesseract)
    hands, hand_confidence = _extract_named_hands(
        image, headings, pytesseract, cv2
    )
    try:
        board, dealer, vulnerability, metadata_confidence = _extract_metadata(
            image, pytesseract
        )
    except PublicationVisionError as exc:
        message = str(exc)
        if message.startswith("METADATA_OCR_FAILED"):
            raise NamedQuadrantVisionError(
                "UNSUPPORTED_LAYOUT_NAMED_QUADRANT_NO_METADATA_HEADER"
            ) from exc
        raise NamedQuadrantVisionError(message) from exc

    image_sha256 = hashlib.sha256(image_bytes).hexdigest()
    source = "local_tesseract_named_quadrant_v1"
    return ScreenshotDealObservation(
        hands=hands,
        board_number=ObservedField(
            board, confidence=metadata_confidence, source=source
        ),
        dealer=ObservedField(
            dealer, confidence=metadata_confidence, source=source
        ),
        vulnerability=ObservedField(
            vulnerability, confidence=metadata_confidence, source=source
        ),
        hand_confidence=hand_confidence,
        extra_metadata={
            "vision_extractor": ObservedField(
                source, confidence=1.0, source="runtime"
            ),
            "layout_family": ObservedField(
                "named_quadrant", confidence=1.0, source="runtime"
            ),
            "image_sha256": ObservedField(
                image_sha256, confidence=1.0, source="runtime"
            ),
            "filename": ObservedField(
                filename, confidence=1.0, source="runtime"
            ),
            "media_type": ObservedField(
                media_type, confidence=1.0, source="runtime"
            ),
        },
    )
