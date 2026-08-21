"""Second bounded EBU appeals cross extractor using proven cross-row geometry.

The appeals metadata/compass detection remains specific to the EBU appeals form. Hand
rows are read with the already field-proven publication cross row/glyph detector, which
masks the repeated suit-glyph column before OCR. This is pixel geometry only: no card is
filled from deck complement, no metadata is derived from board number, and all output
still passes the strict 52-unique standard-deck gate.
"""
from __future__ import annotations

import hashlib

from .screenshot import ObservedField, ScreenshotDealObservation
from .vision_appeals_cross import (
    AppealsCrossVisionError,
    _extract_appeals_metadata,
    _ocr_appeals_compass,
)
from .vision_publication import PublicationVisionError, _decode, _deps, _extract_hands


def extract_appeals_cross_observation(
    image_bytes: bytes, *, media_type: str, filename: str | None = None
) -> ScreenshotDealObservation:
    cv2, np, pytesseract = _deps()
    image = _decode(image_bytes, cv2, np)
    board, dealer, vulnerability, metadata_confidence = _extract_appeals_metadata(
        image, pytesseract, cv2
    )
    compass = _ocr_appeals_compass(image, pytesseract, cv2)
    try:
        hands, hand_confidence = _extract_hands(image, compass, pytesseract, cv2)
    except PublicationVisionError as exc:
        # The appeals header is already recognized at this point. Any hand/deck
        # ambiguity is terminal for this family and must not fall through to another
        # extractor.
        raise AppealsCrossVisionError(f"APPEALS_HAND_GATE:{exc}") from exc

    source = "local_tesseract_appeals_cross_v2"
    image_sha256 = hashlib.sha256(image_bytes).hexdigest()
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
            "vision_extractor": ObservedField(source, confidence=1.0, source="runtime"),
            "image_sha256": ObservedField(
                image_sha256, confidence=1.0, source="runtime"
            ),
            "filename": ObservedField(filename, confidence=1.0, source="runtime"),
            "media_type": ObservedField(media_type, confidence=1.0, source="runtime"),
        },
    )
