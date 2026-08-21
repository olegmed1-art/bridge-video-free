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
    _cluster_rows,
    _deps,
    _extract_metadata,
    _glyph_column,
    _ocr_holding_row,
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
    # Keep the native high-resolution publication crop whenever practical. The older
    # cross-family normalizer intentionally targets 700px, but this print font loses
    # small terminal ranks at that scale. This is a pixel-resolution choice only; no
    # card value is inferred from layout or deck inventory.
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
            try:
                value, conf = _ocr_holding_row(
                    image, row, glyph_x[hand], pytesseract, cv2
                )
            except PublicationVisionError as exc:
                raise PublicationGridVisionError(str(exc)) from exc
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
