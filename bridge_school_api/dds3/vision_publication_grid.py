"""Local/free fail-closed extractor for publication grid diagrams without compass labels.

This bounded family is the classic printed layout with an explicit
`Board / Dealer / Vul` header, four centered North rows, four paired West/East rows,
and four centered South rows. Seat assignment is part of this detected page layout, not
bridge inference. Cards are read only from pixels and still must pass the full 52-unique
validation; missing/ambiguous cards are never repaired from the deck complement.
"""
from __future__ import annotations

import hashlib
from typing import Any

from .screenshot import ObservedField, ScreenshotDealObservation
from .vision_publication import (
    PublicationVisionError,
    _clean_rank_text,
    _cluster_rows,
    _deps,
    _extract_metadata,
    _glyph_column,
    _rank_tokens,
    _row_center,
)


class PublicationGridVisionError(ValueError):
    pass


def _decode_grid(image_bytes: bytes, cv2: Any, np: Any) -> Any:
    if not image_bytes:
        raise PublicationGridVisionError("EMPTY_IMAGE")
    image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise PublicationGridVisionError("IMAGE_DECODE_FAILED")
    height, width = image.shape[:2]
    if width < 250 or height < 180:
        raise PublicationGridVisionError("IMAGE_TOO_SMALL")
    if width < 600:
        scale = 700.0 / width
        image = cv2.resize(
            image,
            (700, max(1, round(height * scale))),
            interpolation=cv2.INTER_CUBIC,
        )
    elif width > 1200:
        scale = 1000.0 / width
        image = cv2.resize(
            image,
            (1000, max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    return image


def _grid_rows(image: Any, pytesseract: Any) -> dict[str, list[list[dict[str, Any]]]]:
    tokens = _rank_tokens(image, pytesseract)
    height, width = image.shape[:2]

    center_tokens = [
        token for token in tokens
        if width * 0.30 <= token["cx"] <= width * 0.62 and token["cy"] > height * 0.07
    ]
    center_rows = _cluster_rows(center_tokens, tolerance=max(14.0, width * 0.02))
    center_rows = [row for row in center_rows if _row_center(row) > height * 0.08]
    if len(center_rows) != 8:
        raise PublicationGridVisionError(f"UNSUPPORTED_LAYOUT_GRID_CENTER_ROWS:{len(center_rows)}")

    north_rows = center_rows[:4]
    south_rows = center_rows[4:]
    north_bottom = _row_center(north_rows[-1])
    south_top = _row_center(south_rows[0])
    if not (north_bottom < south_top and south_top - north_bottom > height * 0.16):
        raise PublicationGridVisionError("UNSUPPORTED_LAYOUT_GRID_VERTICAL_GEOMETRY")

    lateral_tokens = [
        token for token in tokens
        if north_bottom + width * 0.01 < token["cy"] < south_top - width * 0.01
        and (token["cx"] < width * 0.36 or token["cx"] > width * 0.66)
    ]
    lateral_rows = _cluster_rows(lateral_tokens, tolerance=max(14.0, width * 0.02))
    if len(lateral_rows) != 4:
        raise PublicationGridVisionError(f"UNSUPPORTED_LAYOUT_GRID_LATERAL_ROWS:{len(lateral_rows)}")

    west_rows = [[token for token in row if token["cx"] < width * 0.36] for row in lateral_rows]
    east_rows = [[token for token in row if token["cx"] > width * 0.66] for row in lateral_rows]
    if any(not row for row in west_rows + east_rows):
        raise PublicationGridVisionError("UNSUPPORTED_LAYOUT_GRID_EMPTY_LATERAL_ROW")

    return {"N": north_rows, "E": east_rows, "S": south_rows, "W": west_rows}


def _component_holding_candidate(
    gray: Any, cv2: Any, pytesseract: Any
) -> tuple[str, float] | None:
    """Read separated printed rank glyphs independently from pixels.

    Whole-row OCR can make a systematic single-glyph substitution even when the glyphs
    are visually separated. This path segments connected ink components and requires
    strong repeated OCR agreement for every rank before using the component sequence.
    It does not inspect deck inventory, other hands, or canonical truth. Rows containing
    typography that cannot be represented by one connected rank glyph (for example a
    printed two-character ``10``) simply fall back to the independent whole-row pass.
    """
    _, inverted = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(inverted, 8)
    components: list[tuple[int, int, int, int, int]] = []
    min_height = max(8, int(gray.shape[0] * 0.25))
    for index in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[index])
        if area < 20 or height < min_height or width < 2:
            continue
        components.append((x, y, width, height, area))
    components.sort(key=lambda item: item[0])
    if not components or len(components) > 13:
        return None

    chars: list[str] = []
    strengths: list[int] = []
    for x, y, width, height, _area in components:
        pad = 5
        part = gray[
            max(0, y - pad):min(gray.shape[0], y + height + pad),
            max(0, x - pad):min(gray.shape[1], x + width + pad),
        ]
        votes: list[str] = []
        for scale in (3, 5):
            enlarged = cv2.resize(
                part, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC
            )
            for psm in (10, 13):
                raw = pytesseract.image_to_string(
                    enlarged,
                    config=(
                        f"--psm {psm} "
                        "-c tessedit_char_whitelist=AKQJT9876543210"
                    ),
                )
                cleaned = _clean_rank_text(raw)
                if len(cleaned) == 1:
                    votes.append(cleaned)
        if not votes:
            return None
        frequencies = {value: votes.count(value) for value in set(votes)}
        ordered = sorted(frequencies.items(), key=lambda item: (-item[1], item[0]))
        best, best_votes = ordered[0]
        if best_votes < 3:
            return None
        if len(ordered) > 1 and ordered[1][1] == best_votes:
            raise PublicationGridVisionError(f"AMBIGUOUS_COMPONENT_OCR:{ordered}")
        chars.append(best)
        strengths.append(best_votes)

    if not chars:
        return None
    confidence = min(0.96, 0.78 + 0.04 * min(strengths))
    return "".join(chars), confidence


def _ocr_grid_holding_row(
    image: Any,
    row: list[dict[str, Any]],
    glyph_x: float,
    pytesseract: Any,
    cv2: Any,
) -> tuple[str, float]:
    """Read only the printed holding, excluding the suit-glyph column by geometry.

    The print family has a stable ~5.5% image-width gap from the left edge of the suit
    glyph to the first rank. Starting after that gap avoids the OCR failure where the
    suit symbol and first rank are fused into one pseudo-character. Candidate readings
    come from independent pixel paths. No deck state is consulted.
    """
    if not row:
        return "", 0.0
    height, width = image.shape[:2]
    cy = _row_center(row)
    y0 = max(0, int(cy - max(20.0, width * 0.028)))
    y1 = min(height, int(cy + max(20.0, width * 0.028)))
    x0 = max(0, int(glyph_x + width * 0.055))
    x1 = min(width, int(x0 + width * 0.30))
    crop = image[y0:y1, x0:x1]
    if not crop.size:
        raise PublicationGridVisionError("EMPTY_HOLDING_CROP")

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    component = _component_holding_candidate(gray, cv2, pytesseract)
    if component is not None:
        return component

    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    candidates: list[str] = []
    for source in (gray, binary):
        for scale in (2, 3):
            enlarged = cv2.resize(
                source, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC
            )
            for psm in (7, 8, 13):
                raw = pytesseract.image_to_string(
                    enlarged,
                    config=(
                        f"--psm {psm} "
                        "-c tessedit_char_whitelist=AKQJT9876543210"
                    ),
                )
                cleaned = _clean_rank_text(raw)
                if cleaned:
                    candidates.append(cleaned)

    if not candidates:
        return "", 0.55
    counts = {value: candidates.count(value) for value in set(candidates)}
    ordered = sorted(counts.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))
    best, votes = ordered[0]
    if votes < 2:
        raise PublicationGridVisionError(f"AMBIGUOUS_CARD_OCR:{ordered}")
    if len(ordered) > 1 and ordered[1][1] == votes and ordered[1][0] != best:
        raise PublicationGridVisionError(f"AMBIGUOUS_CARD_OCR:{ordered}")
    confidence = min(0.92, 0.58 + 0.04 * votes)
    return best, confidence


def _extract_grid_hands(
    image: Any, pytesseract: Any, cv2: Any
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, float]]]:
    rows = _grid_rows(image, pytesseract)
    glyph_x = {hand: _glyph_column(hand_rows) for hand, hand_rows in rows.items()}
    hands: dict[str, dict[str, str]] = {}
    confidence: dict[str, dict[str, float]] = {}
    cards: list[str] = []

    for hand in "NESW":
        holdings: list[str] = []
        row_confidence: list[float] = []
        for row in rows[hand]:
            value, conf = _ocr_grid_holding_row(
                image, row, glyph_x[hand], pytesseract, cv2
            )
            holdings.append(value)
            row_confidence.append(conf)
        if sum(len(value) for value in holdings) != 13:
            raise PublicationGridVisionError(
                f"INCOMPLETE_HAND:{hand}:{'.'.join(holdings)}"
            )
        hands[hand] = dict(zip("SHDC", holdings, strict=True))
        confidence[hand] = dict(zip("SHDC", row_confidence, strict=True))
        for suit, ranks in zip("SHDC", holdings, strict=True):
            cards.extend(suit + rank for rank in ranks)

    expected = {suit + rank for suit in "SHDC" for rank in "AKQJT98765432"}
    if len(cards) != 52 or len(set(cards)) != 52:
        raise PublicationGridVisionError(
            f"DECK_VALIDATION_FAILED:{len(cards)}/{len(set(cards))}"
        )
    if set(cards) != expected:
        raise PublicationGridVisionError("DECK_VALIDATION_FAILED:NOT_STANDARD_DECK")
    return hands, confidence


def extract_publication_grid_observation(
    image_bytes: bytes, *, media_type: str, filename: str | None = None
) -> ScreenshotDealObservation:
    cv2, np, pytesseract = _deps()
    image = _decode_grid(image_bytes, cv2, np)
    try:
        board, dealer, vulnerability, metadata_confidence = _extract_metadata(
            image, pytesseract
        )
    except PublicationVisionError as exc:
        message = str(exc)
        if message.startswith("METADATA_OCR_FAILED"):
            raise PublicationGridVisionError("UNSUPPORTED_LAYOUT_GRID_NO_METADATA_HEADER") from exc
        raise PublicationGridVisionError(message) from exc

    hands, hand_confidence = _extract_grid_hands(image, pytesseract, cv2)
    image_sha256 = hashlib.sha256(image_bytes).hexdigest()
    source = "local_tesseract_publication_grid_v1"
    return ScreenshotDealObservation(
        hands=hands,
        board_number=ObservedField(board, confidence=metadata_confidence, source=source),
        dealer=ObservedField(dealer, confidence=metadata_confidence, source=source),
        vulnerability=ObservedField(
            vulnerability, confidence=metadata_confidence, source=source
        ),
        hand_confidence=hand_confidence,
        extra_metadata={
            "vision_extractor": ObservedField(source, confidence=1.0, source="runtime"),
            "layout_family": ObservedField(
                "publication_grid", confidence=1.0, source="runtime"
            ),
            "image_sha256": ObservedField(
                image_sha256, confidence=1.0, source="runtime"
            ),
            "filename": ObservedField(filename, confidence=1.0, source="runtime"),
            "media_type": ObservedField(media_type, confidence=1.0, source="runtime"),
        },
    )
