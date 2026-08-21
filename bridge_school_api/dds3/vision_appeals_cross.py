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


def _dedicated_dealer_read(image: Any, pytesseract: Any, cv2: Any) -> str | None:
    """Read the word immediately to the right of the explicit Dealer label.

    This is OCR redundancy, not semantic repair. A full dealer word must appear exactly
    as a contiguous substring in at least two bounded crop reads; fuzzy spell correction
    such as ``Bast -> East`` and board-derived metadata are forbidden.
    """
    height, width = image.shape[:2]
    candidates: list[str] = []
    diagnostics: list[str] = []
    full_dealers = {"NORTH": "N", "EAST": "E", "SOUTH": "S", "WEST": "W"}
    for detector_psm in (6, 11):
        data = pytesseract.image_to_data(
            image,
            config=f"--psm {detector_psm}",
            output_type=pytesseract.Output.DICT,
        )
        for index, raw in enumerate(data["text"]):
            if re.sub(r"[^A-Za-z]", "", raw).upper() != "DEALER":
                continue
            try:
                conf = float(data["conf"][index])
            except (TypeError, ValueError):
                conf = -1
            if conf < 5:
                continue
            left = int(data["left"][index]); top = int(data["top"][index])
            w = int(data["width"][index]); h = int(data["height"][index])
            x0 = max(0, left + w + 1)
            x1 = min(width, x0 + max(150, int(width * 0.25)))
            y0 = max(0, top - max(10, h))
            y1 = min(height, top + h + max(10, h))
            crop = image[y0:y1, x0:x1]
            if not crop.size:
                continue
            crop = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            for source in (gray, binary):
                for psm in (7, 8, 11, 13):
                    text = pytesseract.image_to_string(
                        source,
                        config=f"--psm {psm} -c tessedit_char_whitelist=NorthEastSouthWestNESW",
                    )
                    token = re.sub(r"[^A-Za-z]", "", text).upper()
                    if token:
                        diagnostics.append(token)
                    if token in DEALER_MAP:
                        candidates.append(DEALER_MAP[token])
                    for word, seat in full_dealers.items():
                        if word in token:
                            candidates.append(seat)
    if not candidates:
        if diagnostics:
            raise AppealsCrossVisionError(
                f"APPEALS_DEALER_EXACT_OCR_FAILED:{diagnostics[:16]}"
            )
        return None
    counts = {value: candidates.count(value) for value in set(candidates)}
    if len(counts) != 1:
        raise AppealsCrossVisionError(f"APPEALS_DEALER_OCR_AMBIGUOUS:{candidates}")
    best = next(iter(counts))
    if counts[best] < 2:
        raise AppealsCrossVisionError(f"APPEALS_DEALER_OCR_INSUFFICIENT:{candidates}")
    return best


def _extract_appeals_metadata(
    image: Any, pytesseract: Any, cv2: Any
) -> tuple[int, str, str, float]:
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
    if not board_match or not vul_match:
        raise AppealsCrossVisionError(f"APPEALS_METADATA_OCR_FAILED:{text[:240]!r}")

    dealer = DEALER_MAP.get(dealer_match.group(1).upper()) if dealer_match else None
    if dealer is None:
        dealer = _dedicated_dealer_read(image, pytesseract, cv2)
    vul_key = re.sub(r"\s+", "", vul_match.group(1).upper())
    vulnerability = VUL_MAP.get(vul_key)
    if dealer is None or vulnerability is None:
        raise AppealsCrossVisionError(f"APPEALS_METADATA_OCR_FAILED:{text[:240]!r}")
    return int(board_match.group(1)), dealer, vulnerability, 0.78


def extract_appeals_cross_observation(
    image_bytes: bytes, *, media_type: str, filename: str | None = None
) -> ScreenshotDealObservation:
    cv2, np, pytesseract = _deps()
    image = _decode(image_bytes, cv2, np)

    # Header recognition happens before card extraction so unrelated publication layouts
    # can continue to their own bounded extractor. Once this family is recognized,
    # card/metadata ambiguity fails closed rather than falling through.
    board, dealer, vulnerability, metadata_confidence = _extract_appeals_metadata(
        image, pytesseract, cv2
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
