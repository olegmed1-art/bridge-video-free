"""Local/free fail-closed extractor for VuBridge named four-column diagrams.

The supported family has explicit ``West``, ``North``, ``East`` and ``South`` printed
headings on one horizontal band, four suit rows below each heading, and an explicit
Board/Dealer/Vulnerable header. Seat identity is taken only from the printed word; the
horizontal order is not used to rename seats. Suit-row order S/H/D/C is a visible layout
contract of this publication family. Cards must form the exact standard 52-card deck;
no deck-complement, board-derived metadata, or bridge inference repair is permitted.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from .screenshot import ObservedField, ScreenshotDealObservation
from .vision_publication import (
    PublicationVisionError,
    _clean_rank_text,
    _decode,
    _deps,
    _extract_metadata,
)


class NamedQuadrantVisionError(ValueError):
    pass


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
    labels = _heading_candidates(image, pytesseract)
    if any(not labels[seat] for seat in "NWES"):
        raise NamedQuadrantVisionError("UNSUPPORTED_LAYOUT_NAMED_QUADRANT_NO_HEADINGS")
    height, width = image.shape[:2]
    # The hand-heading occurrence is the set of one label per seat lying on the same
    # horizontal publication band. Incidental metadata labels occur elsewhere.
    best: tuple[float, dict[str, tuple[float, float, float]]] | None = None
    for n in labels["N"]:
        for w in labels["W"]:
            for e in labels["E"]:
                for s in labels["S"]:
                    group = {"N": n, "W": w, "E": e, "S": s}
                    ys = [v[1] for v in group.values()]
                    xs = sorted(v[0] for v in group.values())
                    if max(ys) - min(ys) > height * 0.045:
                        continue
                    gaps = [xs[i + 1] - xs[i] for i in range(3)]
                    if min(gaps) < width * 0.12:
                        continue
                    min_conf = min(v[2] for v in group.values())
                    score = (max(ys) - min(ys)) + 0.15 * (max(gaps) - min(gaps)) - 25 * min_conf
                    if best is None or score < best[0]:
                        best = (score, group)
    if best is None:
        raise NamedQuadrantVisionError("UNSUPPORTED_LAYOUT_NAMED_QUADRANT_GEOMETRY")
    return best[1]


def _column_bounds(headings: dict[str, tuple[float, float, float]], width: int) -> dict[str, tuple[int, int]]:
    ordered = sorted(((value[0], seat) for seat, value in headings.items()))
    out: dict[str, tuple[int, int]] = {}
    for index, (x, seat) in enumerate(ordered):
        left = 0 if index == 0 else int((ordered[index - 1][0] + x) / 2)
        right = width if index == len(ordered) - 1 else int((x + ordered[index + 1][0]) / 2)
        # Keep a small inner gutter so letters from adjacent columns cannot leak in.
        gutter = max(2, int(width * 0.006))
        out[seat] = (max(0, left + gutter), min(width, right - gutter))
    return out


def _rank_lines_from_crop(crop: Any, pytesseract: Any, cv2: Any) -> list[str]:
    crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    candidates: list[tuple[str, ...]] = []
    for source in (gray, binary):
        for psm in (6, 11):
            raw = pytesseract.image_to_string(
                source,
                config=f"--psm {psm} -c tessedit_char_whitelist=AKQJT9876543210",
            )
            lines = []
            for line in raw.splitlines():
                value = _clean_rank_text(line)
                if value:
                    lines.append(value)
            # This bounded family has exactly four visible S/H/D/C holding rows.
            if len(lines) >= 4:
                candidate = tuple(lines[:4])
                if sum(len(value) for value in candidate) == 13:
                    candidates.append(candidate)
    unique = sorted(set(candidates))
    if not unique:
        raise NamedQuadrantVisionError("INCOMPLETE_NAMED_HAND_ROWS")
    if len(unique) != 1:
        raise NamedQuadrantVisionError(f"AMBIGUOUS_CARD_OCR:{unique}")
    return list(unique[0])


def _extract_four_column_hands(
    image: Any,
    headings: dict[str, tuple[float, float, float]],
    pytesseract: Any,
    cv2: Any,
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, float]]]:
    height, width = image.shape[:2]
    bounds = _column_bounds(headings, width)
    hands: dict[str, dict[str, str]] = {}
    confidence: dict[str, dict[str, float]] = {}
    cards: list[str] = []
    for seat in "NESW":
        hx, hy, _ = headings[seat]
        x0, x1 = bounds[seat]
        y0 = max(0, int(hy + height * 0.025))
        y1 = min(height, int(hy + height * 0.46))
        crop = image[y0:y1, x0:x1]
        if crop.size == 0:
            raise NamedQuadrantVisionError(f"EMPTY_HAND_CROP:{seat}")
        holdings = _rank_lines_from_crop(crop, pytesseract, cv2)
        hands[seat] = dict(zip("SHDC", holdings, strict=True))
        confidence[seat] = {suit: 0.66 for suit in "SHDC"}
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
    image = _decode(image_bytes, cv2, np)
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
            "layout_family": ObservedField("named_four_column", confidence=1.0, source="runtime"),
            "image_sha256": ObservedField(image_sha256, confidence=1.0, source="runtime"),
            "filename": ObservedField(filename, confidence=1.0, source="runtime"),
            "media_type": ObservedField(media_type, confidence=1.0, source="runtime"),
        },
    )
