"""Bounded EBU appeals extractor with redundant per-row pixel OCR.

The geometry is fixed by the visible N/W/E/S compass. Every holding is read directly
from its bounded row with multiple local Tesseract segmentation modes and image
preprocessings. No card is completed from the deck, and Board/dealer/vulnerability are
read only from the explicit appeals header. A recognized ambiguous/incomplete row fails
closed before the strict 52-unique-card gate.
"""
from __future__ import annotations

import hashlib

from .screenshot import ObservedField, ScreenshotDealObservation
from .vision_appeals_cross import (
    AppealsCrossVisionError,
    _extract_appeals_metadata,
    _ocr_appeals_compass,
)
from .vision_publication import _clean_rank_text, _decode, _deps


def _read_row(image, *, x0: float, cy: float, span: float, pytesseract, cv2):
    height, width = image.shape[:2]
    radius = max(6, int(span * 0.18))
    left = max(0, int(x0))
    right = min(width, int(left + max(90.0, span * 2.55)))
    top = max(0, int(cy - radius))
    bottom = min(height, int(cy + radius + 1))
    crop = image[top:bottom, left:right]
    if not crop.size:
        raise AppealsCrossVisionError("EMPTY_APPEALS_HOLDING_CROP")

    grouped: list[list[str]] = []
    for scale in (4, 5, 6):
        scaled = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(scaled, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        adaptive = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 9
        )
        group: list[str] = []
        for source in (gray, binary, adaptive):
            for psm in (6, 7, 8, 11, 13):
                raw = pytesseract.image_to_string(
                    source,
                    config=f"--psm {psm} -c tessedit_char_whitelist=AKQJT9876543210",
                )
                group.append(_clean_rank_text(raw))
        grouped.append(group)

    supported: dict[str, set[int]] = {}
    flat: list[str] = []
    for group_index, group in enumerate(grouped):
        for value in group:
            flat.append(value)
            if value:
                supported.setdefault(value, set()).add(group_index)
    cross_scale = {value: groups for value, groups in supported.items() if len(groups) >= 2}
    if not cross_scale:
        raise AppealsCrossVisionError(f"APPEALS_ROW_NO_CROSS_SCALE_CONSENSUS:{flat}")

    counts = {value: flat.count(value) for value in cross_scale}
    best_count = max(counts.values())
    best = [value for value, count in counts.items() if count == best_count]
    if len(best) != 1:
        raise AppealsCrossVisionError(f"AMBIGUOUS_APPEALS_CARD_OCR:{counts}")
    winner = best[0]
    # A conflicting cross-scale reading remains terminal unless the winning exact text
    # has a clear >3:2 OCR-support margin. This is an OCR-consensus threshold only: it
    # does not inspect other hands, deck inventory, Board number, or bridge semantics.
    alternatives = [
        value for value in cross_scale
        if value != winner and counts[value] * 3 >= best_count * 2
    ]
    if alternatives:
        raise AppealsCrossVisionError(f"AMBIGUOUS_APPEALS_CARD_OCR:{counts}")
    confidence = min(0.92, 0.62 + 0.03 * best_count)
    return winner, confidence


def _extract_hands(image, compass, pytesseract, cv2):
    n, w, e, s = (compass[seat] for seat in "NWES")
    span = s[1] - n[1]
    if span <= 12:
        raise AppealsCrossVisionError("APPEALS_COMPASS_SPAN_INVALID")
    axis_x = (n[0] + s[0]) / 2
    center_y = (n[1] + s[1]) / 2
    starts = {
        "N": axis_x - 0.80 * span,
        "S": axis_x - 0.80 * span,
        "W": axis_x - 3.10 * span,
        "E": axis_x + 1.50 * span,
    }
    rows = {
        "N": [n[1] - factor * span for factor in (1.53, 1.16, 0.80, 0.43)],
        "S": [s[1] + factor * span for factor in (0.43, 0.80, 1.16, 1.53)],
        "W": [center_y + factor * span for factor in (-0.55, -0.18, 0.18, 0.55)],
        "E": [center_y + factor * span for factor in (-0.55, -0.18, 0.18, 0.55)],
    }
    hands = {}
    confidence = {}
    cards: list[str] = []
    for seat in "NESW":
        holdings: list[str] = []
        confs: list[float] = []
        for cy in rows[seat]:
            value, conf = _read_row(
                image,
                x0=starts[seat],
                cy=cy,
                span=span,
                pytesseract=pytesseract,
                cv2=cv2,
            )
            holdings.append(value)
            confs.append(conf)
        if sum(len(value) for value in holdings) != 13:
            raise AppealsCrossVisionError(
                f"INCOMPLETE_APPEALS_HAND:{seat}:{'.'.join(holdings)}"
            )
        hands[seat] = dict(zip("SHDC", holdings, strict=True))
        confidence[seat] = dict(zip("SHDC", confs, strict=True))
        for suit, ranks in zip("SHDC", holdings, strict=True):
            cards.extend(suit + rank for rank in ranks)

    expected = {suit + rank for suit in "SHDC" for rank in "AKQJT98765432"}
    if len(cards) != 52 or len(set(cards)) != 52:
        raise AppealsCrossVisionError(
            f"APPEALS_DECK_VALIDATION_FAILED:{len(cards)}/{len(set(cards))}"
        )
    if set(cards) != expected:
        raise AppealsCrossVisionError("APPEALS_DECK_VALIDATION_FAILED:NOT_STANDARD_DECK")
    return hands, confidence


def extract_appeals_cross_observation(
    image_bytes: bytes, *, media_type: str, filename: str | None = None
) -> ScreenshotDealObservation:
    cv2, np, pytesseract = _deps()
    image = _decode(image_bytes, cv2, np)
    board, dealer, vulnerability, metadata_confidence = _extract_appeals_metadata(
        image, pytesseract, cv2
    )
    compass = _ocr_appeals_compass(image, pytesseract, cv2)
    hands, hand_confidence = _extract_hands(image, compass, pytesseract, cv2)
    source = "local_tesseract_appeals_cross_v4"
    image_sha256 = hashlib.sha256(image_bytes).hexdigest()
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
            "image_sha256": ObservedField(
                image_sha256, confidence=1.0, source="runtime"
            ),
            "filename": ObservedField(filename, confidence=1.0, source="runtime"),
            "media_type": ObservedField(media_type, confidence=1.0, source="runtime"),
        },
    )
