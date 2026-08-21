"""Local/free fail-closed pixel extractor for federation-style bridge diagrams.

This module recognizes the yellow Israel Bridge Federation board-panel layout from
actual JPEG/PNG/WebP bytes. It is intentionally narrower than a general-purpose
vision model: unsupported layouts and ambiguous OCR are rejected. It never fills a
missing card by deck complement and never derives dealer/vulnerability from the
board number.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from .screenshot import ObservedField, ScreenshotDealObservation

RANKS = set("AKQJT98765432")
VUL_MAP = {
    "NONE": "None",
    "NS": "NS",
    "N-S": "NS",
    "EW": "EW",
    "E-W": "EW",
    "BOTH": "Both",
    "ALL": "Both",
}


class LocalVisionError(ValueError):
    pass


def _deps():
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        import pytesseract  # type: ignore
    except Exception as exc:  # pragma: no cover - environment-specific
        raise LocalVisionError("LOCAL_VISION_RUNTIME_UNAVAILABLE") from exc
    return cv2, np, pytesseract


def _clean_ranks(text: str) -> str:
    return "".join(ch for ch in text.upper() if ch in RANKS)


def _normalize(image: Any, cv2: Any) -> Any:
    height, width = image.shape[:2]
    if width < 250 or height < 250:
        raise LocalVisionError("IMAGE_TOO_SMALL")
    if width != 384:
        scale = 384.0 / width
        image = cv2.resize(
            image,
            (384, max(1, round(height * scale))),
            interpolation=cv2.INTER_CUBIC,
        )
    return image


def _yellow_start(image: Any, cv2: Any, np: Any) -> int:
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    yellow = (
        (rgb[:, :, 0] > 225)
        & (rgb[:, :, 1] > 210)
        & (rgb[:, :, 2] < 130)
    )
    rows = np.where(yellow.mean(axis=1) > 0.45)[0]
    if not len(rows):
        raise LocalVisionError("UNSUPPORTED_LAYOUT_NO_YELLOW_PANEL")
    return int(rows[0])


def _compass(image: Any, yellow_start: int, cv2: Any) -> tuple[int, int, int, int]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask = (gray < 70).astype("uint8")
    mask[: yellow_start + 30] = 0
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    height, width = image.shape[:2]
    candidates: list[tuple[int, int, int, int, int]] = []
    for component_id in range(1, count):
        x, y, box_width, box_height, area = map(int, stats[component_id])
        ratio = box_width / max(1, box_height)
        if (
            1800 < area < 15000
            and 0.65 < ratio < 1.5
            and abs((x + box_width / 2) - width / 2) < width * 0.18
        ):
            candidates.append((area, x, y, box_width, box_height))
    if not candidates:
        raise LocalVisionError("UNSUPPORTED_LAYOUT_NO_COMPASS")
    _, x, y, box_width, box_height = max(candidates)
    return x, y, box_width, box_height


def _row_centers(image: Any, yellow_start: int) -> dict[str, list[float]]:
    height = image.shape[0]
    # These are pixel-geometry anchors of the federation yellow-panel family after
    # normalization to 384 px width. They describe visible rows, not bridge rules.
    return {
        "N": [yellow_start + 14 + 27.5 * index for index in range(4)],
        "W": [yellow_start + 125 + 27.5 * index for index in range(4)],
        "E": [yellow_start + 125 + 27.5 * index for index in range(4)],
        "S": [height - 99 + 27.5 * index for index in range(4)],
    }


def _mask_suit_glyphs(
    image: Any,
    yellow_start: int,
    compass_x: int,
    compass_width: int,
    centers: dict[str, list[float]],
    cv2: Any,
) -> Any:
    masked = image.copy()
    height, width = image.shape[:2]
    x_ranges = {
        "N": (compass_x - 1, compass_x + 22),
        "S": (compass_x - 1, compass_x + 22),
        "W": (0, 28),
        "E": (compass_x + compass_width + 34, compass_x + compass_width + 58),
    }
    for hand, row_values in centers.items():
        x0, x1 = x_ranges[hand]
        for suit_index, center_y in enumerate(row_values):
            # Club holdings may wrap below the glyph; keep the club mask narrower.
            delta_y = 9 if suit_index == 3 else 11
            cv2.rectangle(
                masked,
                (max(0, int(x0)), max(yellow_start, int(center_y - delta_y))),
                (min(width - 1, int(x1)), min(height - 1, int(center_y + delta_y))),
                (0, 255, 255),
                -1,
            )
    return masked


def _ocr_tokens(masked: Any, yellow_start: int, cv2: Any, pytesseract: Any) -> list[dict[str, Any]]:
    data = pytesseract.image_to_data(
        masked[yellow_start:],
        config="--psm 11 -c tessedit_char_whitelist=AKQJT98765432",
        output_type=pytesseract.Output.DICT,
    )
    tokens: list[dict[str, Any]] = []
    for index, raw_text in enumerate(data["text"]):
        text = _clean_ranks(raw_text)
        if not text:
            continue
        x = int(data["left"][index])
        y = int(data["top"][index]) + yellow_start
        width = int(data["width"][index])
        height = int(data["height"][index])
        tokens.append(
            {
                "text": text,
                "confidence": max(0.0, min(1.0, float(data["conf"][index]) / 100.0)),
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "center_x": x + width / 2,
                "center_y": y + height / 2,
            }
        )
    return tokens


def _row_box(
    hand: str,
    suit_index: int,
    centers: dict[str, list[float]],
    compass_x: int,
    compass_width: int,
    image_height: int,
    image_width: int,
    yellow_start: int,
) -> tuple[int, int, int, int]:
    center_y = centers[hand][suit_index]
    if hand in {"N", "S"}:
        x0, x1 = max(0, compass_x - 2), min(image_width, compass_x + compass_width + 45)
    elif hand == "W":
        x0, x1 = 0, max(1, compass_x - 4)
    else:
        x0, x1 = min(image_width, compass_x + compass_width + 28), image_width
    previous = (centers[hand][suit_index - 1] + center_y) / 2 if suit_index else center_y - 14
    following = (
        (center_y + centers[hand][suit_index + 1]) / 2
        if suit_index < 3
        else image_height
    )
    if suit_index == 3 and hand in {"W", "E"}:
        following = min(image_height, center_y + 35)
    return int(x0), max(yellow_start, int(previous)), int(x1), min(image_height, int(following))


def _fallback_row(
    masked: Any,
    box: tuple[int, int, int, int],
    cv2: Any,
    pytesseract: Any,
) -> str:
    x0, y0, x1, y1 = box
    row = masked[y0:y1, x0:x1]
    if not row.size:
        return ""
    enlarged = cv2.resize(row, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY)
    candidates = [
        _clean_ranks(
            pytesseract.image_to_string(
                gray,
                config=f"--psm {psm} -c tessedit_char_whitelist=AKQJT98765432",
            )
        )
        for psm in (7, 10, 11)
    ]
    nonempty = [value for value in candidates if value]
    if not nonempty:
        return ""
    counts: dict[str, int] = {}
    for value in nonempty:
        counts[value] = counts.get(value, 0) + 1
    best = max(counts, key=counts.get)
    if counts[best] >= 2 or len(counts) == 1:
        return best
    raise LocalVisionError(f"AMBIGUOUS_CARD_OCR:{candidates}")


def extract_federation_yellow_observation(
    image_bytes: bytes,
    *,
    media_type: str,
    filename: str | None = None,
) -> ScreenshotDealObservation:
    """Extract one full federation yellow-panel deal from image bytes.

    The extractor is deliberately fail-closed. A valid result requires explicit
    pixel OCR for Board, Dealer, Vulnerability and a complete 52-card deck.
    """
    cv2, np, pytesseract = _deps()
    if not image_bytes:
        raise LocalVisionError("EMPTY_IMAGE")
    array = np.frombuffer(image_bytes, np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if image is None:
        raise LocalVisionError("IMAGE_DECODE_FAILED")
    image = _normalize(image, cv2)
    image_height, image_width = image.shape[:2]
    yellow_start = _yellow_start(image, cv2, np)
    compass_x, _, compass_width, _ = _compass(image, yellow_start, cv2)
    centers = _row_centers(image, yellow_start)
    masked = _mask_suit_glyphs(image, yellow_start, compass_x, compass_width, centers, cv2)
    tokens = _ocr_tokens(masked, yellow_start, cv2, pytesseract)

    hand_strings: dict[str, str] = {}
    hand_confidence: dict[str, dict[str, float]] = {}
    for hand in "NESW":
        values: list[str] = []
        confidences: list[float] = []
        for suit_index, suit in enumerate("SHDC"):
            box = _row_box(
                hand,
                suit_index,
                centers,
                compass_x,
                compass_width,
                image_height,
                image_width,
                yellow_start,
            )
            x0, y0, x1, y1 = box
            row_tokens = [
                token
                for token in tokens
                if x0 <= token["center_x"] <= x1 and y0 <= token["center_y"] <= y1
            ]
            row_tokens.sort(key=lambda token: (token["y"], token["x"]))
            value = "".join(token["text"] for token in row_tokens)
            if value.startswith("4") and row_tokens and row_tokens[0]["x"] < x0 + 24:
                # Residual spade-glyph OCR artifact, identified geometrically.
                value = value[1:]
            if not value:
                value = _fallback_row(masked, box, cv2, pytesseract)
                confidence = 0.55 if value else 0.90
            else:
                confidence = min(token["confidence"] for token in row_tokens)
            values.append(value)
            confidences.append(confidence)
        hand_strings[hand] = ".".join(values)
        hand_confidence[hand] = dict(zip("SHDC", confidences, strict=True))

    hands: dict[str, dict[str, str]] = {}
    cards: list[str] = []
    for hand, holding in hand_strings.items():
        parts = holding.split(".")
        if len(parts) != 4 or sum(len(part) for part in parts) != 13:
            raise LocalVisionError(f"INCOMPLETE_HAND:{hand}:{holding}")
        hands[hand] = dict(zip("SHDC", parts, strict=True))
        for suit, ranks in zip("SHDC", parts, strict=True):
            cards.extend(suit + rank for rank in ranks)
    if len(cards) != 52 or len(set(cards)) != 52:
        raise LocalVisionError(f"DECK_VALIDATION_FAILED:{len(cards)}/{len(set(cards))}")
    if set(cards) != {suit + rank for suit in "SHDC" for rank in "AKQJT98765432"}:
        raise LocalVisionError("DECK_VALIDATION_FAILED:NOT_STANDARD_DECK")

    header = 255 - cv2.cvtColor(image[:yellow_start], cv2.COLOR_BGR2GRAY)
    board_text = pytesseract.image_to_string(
        header[:, 145 : min(image_width, 215)],
        config="--psm 6 -c tessedit_char_whitelist=0123456789",
    ).strip()
    board_values = re.findall(r"\d+", board_text)
    if len(board_values) != 1:
        raise LocalVisionError(f"BOARD_OCR_FAILED:{board_text!r}")
    board = int(board_values[0])

    metadata_text = pytesseract.image_to_string(
        header[:, int(image_width * 0.59) : image_width],
        config="--psm 6",
    ).replace("\n", " ")
    dealer_match = re.search(r"Dealer\s*:\s*([NESW])", metadata_text, re.IGNORECASE)
    vulnerability_match = re.search(
        r"Vul\s*:\s*(None|N\s*-?\s*S|E\s*-?\s*W|Both|All)",
        metadata_text,
        re.IGNORECASE,
    )
    if not dealer_match or not vulnerability_match:
        raise LocalVisionError(f"METADATA_OCR_FAILED:{metadata_text!r}")
    dealer = dealer_match.group(1).upper()
    vulnerability_key = vulnerability_match.group(1).replace(" ", "").upper()
    vulnerability = VUL_MAP[vulnerability_key]

    image_sha256 = hashlib.sha256(image_bytes).hexdigest()
    return ScreenshotDealObservation(
        hands=hands,
        board_number=ObservedField(board, confidence=0.90, source="local_tesseract_federation_yellow_v1"),
        dealer=ObservedField(dealer, confidence=0.90, source="local_tesseract_federation_yellow_v1"),
        vulnerability=ObservedField(
            vulnerability,
            confidence=0.90,
            source="local_tesseract_federation_yellow_v1",
        ),
        hand_confidence=hand_confidence,
        extra_metadata={
            "vision_extractor": ObservedField(
                "local_tesseract_federation_yellow_v1",
                confidence=1.0,
                source="runtime",
            ),
            "image_sha256": ObservedField(image_sha256, confidence=1.0, source="runtime"),
            "filename": ObservedField(filename, confidence=1.0, source="runtime"),
            "media_type": ObservedField(media_type, confidence=1.0, source="runtime"),
        },
    )
