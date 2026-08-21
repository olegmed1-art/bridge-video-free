"""Local/free fail-closed extractor for appeals-form cross diagrams.

This bounded family uses the classic N/W/E/S cross hand geometry together with an
explicit appeals-form metadata header such as ``Board no 2 / Dealer East / N/S
vulnerable``. Cards are read only from pixels; no deck complement, board-derived
metadata, bridge inference repair, paid/cloud vision, or alternate solver is used.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from .screenshot import ObservedField, ScreenshotDealObservation
from .vision_publication import (
    DEALER_MAP,
    VUL_MAP,
    _decode,
    _deps,
    _extract_hands,
    _ocr_compass,
)


class AppealsCrossVisionError(ValueError):
    pass


def _extract_appeals_metadata(image: Any, pytesseract: Any) -> tuple[int, str, str, float]:
    text = pytesseract.image_to_string(image, config="--psm 6").replace("\n", " ")
    header = re.search(r"\bBoard\s*(?:no|number)\b", text, re.IGNORECASE)
    if header is None:
        raise AppealsCrossVisionError("UNSUPPORTED_LAYOUT_APPEALS_HEADER")

    board_match = re.search(
        r"\bBoard\s*(?:no|number)\s*[:#.]?\s*(\d{1,3})\b", text, re.IGNORECASE
    )
    dealer_match = re.search(
        r"\bDealer\s*[:.]?\s*(North|East|South|West|[NESW])\b", text, re.IGNORECASE
    )
    vul_match = re.search(
        r"\b(None|Love|N\s*[-/]?\s*S|E\s*[-/]?\s*W|Both|All)\s+vulnerable\b",
        text,
        re.IGNORECASE,
    )
    if not board_match or not dealer_match or not vul_match:
        raise AppealsCrossVisionError(f"APPEALS_METADATA_OCR_FAILED:{text[:240]!r}")

    dealer = DEALER_MAP.get(dealer_match.group(1).upper())
    vul_key = re.sub(r"\s+", "", vul_match.group(1).upper())
    vulnerability = VUL_MAP.get(vul_key)
    if dealer is None or vulnerability is None:
        raise AppealsCrossVisionError("APPEALS_METADATA_OCR_INVALID")
    return int(board_match.group(1)), dealer, vulnerability, 0.80


def extract_appeals_cross_observation(
    image_bytes: bytes, *, media_type: str, filename: str | None = None
) -> ScreenshotDealObservation:
    cv2, np, pytesseract = _deps()
    image = _decode(image_bytes, cv2, np)

    # Header recognition happens before card extraction so unrelated publication layouts
    # can continue to their own bounded extractor. Once this family is recognized,
    # card/metadata ambiguity fails closed rather than falling through.
    board, dealer, vulnerability, metadata_confidence = _extract_appeals_metadata(
        image, pytesseract
    )
    try:
        compass = _ocr_compass(image, pytesseract)
        hands, hand_confidence = _extract_hands(image, compass, pytesseract, cv2)
    except Exception as exc:
        raise AppealsCrossVisionError(str(exc)) from exc

    image_sha256 = hashlib.sha256(image_bytes).hexdigest()
    source = "local_tesseract_appeals_cross_v1"
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
            "media_type": ObservedField(
                media_type, confidence=1.0, source="runtime"
            ),
        },
    )
