"""Local/free fail-closed extractor for BridgeCourse slide deal diagrams."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from .screenshot import ObservedField, ScreenshotDealObservation

RANKS = set("AKQJT98765432")
STANDARD_DECK = {suit + rank for suit in "SHDC" for rank in "AKQJT98765432"}
VUL_MAP = {
    "NO": "None",
    "NONE": "None",
    "NS": "NS",
    "N-S": "NS",
    "EW": "EW",
    "E-W": "EW",
    "BOTH": "Both",
    "ALL": "Both",
}


class BridgeCourseVisionError(ValueError):
    pass


def _deps():
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        import pytesseract  # type: ignore
    except Exception as exc:
        raise BridgeCourseVisionError("LOCAL_VISION_RUNTIME_UNAVAILABLE") from exc
    return cv2, np, pytesseract


def _clean_ranks(text: str) -> str:
    text = text.upper().replace("10", "T")
    return "".join(ch for ch in text if ch in RANKS)


def _normalize(image: Any, cv2: Any) -> Any:
    height, width = image.shape[:2]
    if width < 700 or height < 400:
        raise BridgeCourseVisionError("IMAGE_TOO_SMALL")
    if not 1.55 <= width / height <= 2.05:
        raise BridgeCourseVisionError("UNSUPPORTED_BRIDGECOURSE_ASPECT")
    if width != 2134:
        scale = 2134.0 / width
        image = cv2.resize(
            image,
            (2134, max(1, round(height * scale))),
            interpolation=cv2.INTER_CUBIC,
        )
    return image


def _green_layout_components(
    image: Any, cv2: Any, np: Any
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    height, width = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([35, 60, 20]), np.array([100, 255, 220]))
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    compass: list[tuple[int, int, int, int, int]] = []
    metadata_frame: list[tuple[int, int, int, int, int]] = []
    total_area = width * height
    for component_id in range(1, count):
        x, y, box_width, box_height, area = map(int, stats[component_id])
        ratio = box_width / max(1, box_height)
        fraction = area / total_area
        if (
            x > width * 0.55
            and y > height * 0.25
            and 0.75 < ratio < 1.25
            and 0.004 < fraction < 0.03
        ):
            compass.append((area, x, y, box_width, box_height))
        if (
            x > width * 0.45
            and height * 0.15 < y < height * 0.55
            and 1.15 < ratio < 1.8
            and 0.001 < fraction < 0.02
        ):
            metadata_frame.append((area, x, y, box_width, box_height))
    if not compass or not metadata_frame:
        raise BridgeCourseVisionError("UNSUPPORTED_LAYOUT_NO_BRIDGECOURSE_GEOMETRY")
    _, cx, cy, cw, ch = max(compass)
    _, mx, my, mw, mh = max(metadata_frame)
    if my >= cy or mx >= cx:
        raise BridgeCourseVisionError(
            "UNSUPPORTED_LAYOUT_BRIDGECOURSE_GEOMETRY_CONFLICT"
        )
    return (cx, cy, cw, ch), (mx, my, mw, mh)


def _red_suit_pair(
    image: Any,
    region: tuple[int, int, int, int],
    cv2: Any,
    np: Any,
) -> list[tuple[float, float]]:
    x0, y0, x1, y1 = region
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    red = cv2.inRange(
        hsv, np.array([0, 100, 100]), np.array([12, 255, 255])
    ) | cv2.inRange(hsv, np.array([165, 100, 100]), np.array([179, 255, 255]))
    crop = red[y0:y1, x0:x1]
    count, _, stats, _ = cv2.connectedComponentsWithStats(crop, 8)
    candidates: list[tuple[int, float, float]] = []
    scale = image.shape[1] / 2134.0
    for component_id in range(1, count):
        x, y, width, height, area = map(int, stats[component_id])
        if (
            20 * scale * scale < area < 2500 * scale * scale
            and 7 * scale < height < 55 * scale
            and 7 * scale < width < 55 * scale
        ):
            candidates.append((area, x0 + x + width / 2, y0 + y + height / 2))
    pairs: list[tuple[int, tuple[int, float, float], tuple[int, float, float]]] = []
    for left_index in range(len(candidates)):
        for right_index in range(left_index + 1, len(candidates)):
            left = candidates[left_index]
            right = candidates[right_index]
            dx = abs(left[1] - right[1])
            dy = abs(left[2] - right[2])
            if dx < 15 * scale and 25 * scale < dy < 80 * scale:
                pairs.append((left[0] + right[0], left, right))
    if not pairs:
        raise BridgeCourseVisionError("BRIDGECOURSE_SUIT_GEOMETRY_AMBIGUOUS")
    _, first, second = max(pairs, key=lambda item: item[0])
    ordered = sorted([first, second], key=lambda item: item[2])
    return [(item[1], item[2]) for item in ordered]


def _rank_row(
    image: Any,
    *,
    x0: float,
    x1: int,
    center_y: float,
    spacing: float,
    cv2: Any,
    pytesseract: Any,
) -> tuple[str, float]:
    height = image.shape[0]
    y0 = max(0, int(center_y - spacing * 0.42))
    y1 = min(height, int(center_y + spacing * 0.42))
    row = image[y0:y1, int(x0):x1]
    if not row.size:
        raise BridgeCourseVisionError("BRIDGECOURSE_EMPTY_RANK_REGION")
    gray = cv2.cvtColor(row, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    data = pytesseract.image_to_data(
        gray,
        config="--psm 7 -c tessedit_char_whitelist=AKQJT1098765432",
        output_type=pytesseract.Output.DICT,
    )
    tokens: list[str] = []
    confidences: list[float] = []
    for raw_text, raw_confidence in zip(data["text"], data["conf"], strict=True):
        text = _clean_ranks(raw_text)
        if not text:
            continue
        tokens.append(text)
        confidences.append(
            max(0.0, min(1.0, float(raw_confidence) / 100.0))
        )
    value = "".join(tokens)
    if not value:
        raise BridgeCourseVisionError("BRIDGECOURSE_RANK_OCR_EMPTY")
    confidence = min(confidences) if confidences else 0.0
    return value, confidence


def _metadata(
    image: Any,
    frame: tuple[int, int, int, int],
    pytesseract: Any,
) -> tuple[int, str, str, float]:
    x, y, width, height = frame
    crop = image[y + 5 : y + height - 5, x + 5 : x + width - 5]
    data = pytesseract.image_to_data(
        crop, config="--psm 6", output_type=pytesseract.Output.DICT
    )
    tokens = [text for text in data["text"] if text.strip()]
    text = " ".join(tokens)
    board_match = re.search(r"Board\s*:\s*(\d+)", text, re.IGNORECASE)
    dealer_match = re.search(r"Dealer\s*:\s*([NESW])", text, re.IGNORECASE)
    vulnerability_match = re.search(
        r"Vul\s*:\s*(No|None|NS|N\s*-?\s*S|EW|E\s*-?\s*W|Both|All)",
        text,
        re.IGNORECASE,
    )
    if not board_match or not dealer_match or not vulnerability_match:
        raise BridgeCourseVisionError(
            f"BRIDGECOURSE_METADATA_OCR_FAILED:{text!r}"
        )
    confidence_values = [
        max(0.0, min(1.0, float(value) / 100.0))
        for token, value in zip(data["text"], data["conf"], strict=True)
        if token.strip() and float(value) >= 0
    ]
    confidence = min(confidence_values) if confidence_values else 0.0
    raw_vulnerability = vulnerability_match.group(1).replace(" ", "").upper()
    vulnerability = VUL_MAP[raw_vulnerability]
    return (
        int(board_match.group(1)),
        dealer_match.group(1).upper(),
        vulnerability,
        confidence,
    )


def extract_bridgecourse_observation(
    image_bytes: bytes,
    *,
    media_type: str,
    filename: str | None = None,
) -> ScreenshotDealObservation:
    """Extract one complete BridgeCourse slide deal directly from image bytes."""
    cv2, np, pytesseract = _deps()
    if not image_bytes:
        raise BridgeCourseVisionError("EMPTY_IMAGE")
    image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise BridgeCourseVisionError("IMAGE_DECODE_FAILED")
    image = _normalize(image, cv2)
    image_height, image_width = image.shape[:2]
    compass, metadata_frame = _green_layout_components(image, cv2, np)
    compass_x, compass_y, compass_width, compass_height = compass
    regions = {
        "N": (
            max(0, compass_x - 50),
            max(0, compass_y - 280),
            min(image_width, compass_x + compass_width + 200),
            compass_y,
        ),
        "S": (
            max(0, compass_x - 50),
            compass_y + compass_height,
            min(image_width, compass_x + compass_width + 200),
            min(image_height, compass_y + compass_height + 280),
        ),
        "W": (
            max(0, compass_x - 300),
            max(0, compass_y - 60),
            compass_x,
            min(image_height, compass_y + compass_height + 60),
        ),
        "E": (
            compass_x + compass_width,
            max(0, compass_y - 60),
            min(image_width, compass_x + compass_width + 300),
            min(image_height, compass_y + compass_height + 60),
        ),
    }
    hands: dict[str, dict[str, str]] = {}
    hand_confidence: dict[str, dict[str, float]] = {}
    cards: list[str] = []
    for hand in "NESW":
        region = regions[hand]
        red_pair = _red_suit_pair(image, region, cv2, np)
        suit_x = sum(value[0] for value in red_pair) / 2
        heart_y = red_pair[0][1]
        diamond_y = red_pair[1][1]
        spacing = diamond_y - heart_y
        if spacing <= 0:
            raise BridgeCourseVisionError("BRIDGECOURSE_ROW_SPACING_INVALID")
        row_centers = [heart_y - spacing, heart_y, diamond_y, diamond_y + spacing]
        values: list[str] = []
        confidences: list[float] = []
        for center_y in row_centers:
            value, confidence = _rank_row(
                image,
                x0=suit_x + 24,
                x1=region[2],
                center_y=center_y,
                spacing=spacing,
                cv2=cv2,
                pytesseract=pytesseract,
            )
            values.append(value)
            confidences.append(confidence)
        if sum(len(value) for value in values) != 13:
            raise BridgeCourseVisionError(
                f"INCOMPLETE_HAND:{hand}:{'.'.join(values)}"
            )
        hands[hand] = dict(zip("SHDC", values, strict=True))
        hand_confidence[hand] = dict(zip("SHDC", confidences, strict=True))
        for suit, ranks in zip("SHDC", values, strict=True):
            cards.extend(suit + rank for rank in ranks)
    if len(cards) != 52 or len(set(cards)) != 52:
        raise BridgeCourseVisionError(
            f"DECK_VALIDATION_FAILED:{len(cards)}/{len(set(cards))}"
        )
    if set(cards) != STANDARD_DECK:
        raise BridgeCourseVisionError("DECK_VALIDATION_FAILED:NOT_STANDARD_DECK")
    board, dealer, vulnerability, metadata_confidence = _metadata(
        image, metadata_frame, pytesseract
    )
    source = "local_tesseract_bridgecourse_slide_v1"
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
            "vision_extractor": ObservedField(
                source, confidence=1.0, source="runtime"
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
