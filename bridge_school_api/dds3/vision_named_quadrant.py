"""Local/free fail-closed extractor for VuBridge named-quadrant diagrams.

The supported family prints North/West/East/South around a deal and explicit
Board/Dealer/Vulnerable metadata. Cards must form the exact standard 52-card deck;
no deck complement, board-derived metadata, or bridge inference repair is permitted.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any

from .screenshot import ObservedField, ScreenshotDealObservation
from .vision_publication import (
    _clean_rank_text,
    _cluster_rows,
    _deps,
    _rank_tokens,
    _row_center,
)


class NamedQuadrantVisionError(ValueError):
    pass


def _decode_named(image_bytes: bytes, cv2: Any, np: Any) -> Any:
    if not image_bytes:
        raise NamedQuadrantVisionError("EMPTY_IMAGE")
    image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise NamedQuadrantVisionError("IMAGE_DECODE_FAILED")
    height, width = image.shape[:2]
    if width < 250 or height < 180:
        raise NamedQuadrantVisionError("IMAGE_TOO_SMALL")
    target_width = 1400
    if width != target_width:
        scale = target_width / float(width)
        image = cv2.resize(
            image,
            (target_width, max(1, round(height * scale))),
            interpolation=cv2.INTER_CUBIC if scale >= 1 else cv2.INTER_AREA,
        )
    return image


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
    best: tuple[float, dict[str, tuple[float, float, float]]] | None = None
    for n in labels["N"]:
        for w in labels["W"]:
            for e in labels["E"]:
                for s in labels["S"]:
                    middle_y = (w[1] + e[1]) / 2.0
                    center_x = (n[0] + s[0]) / 2.0
                    if abs(w[1] - e[1]) > height * 0.075 or abs(n[0] - s[0]) > width * 0.075:
                        continue
                    if not (n[1] < middle_y - height * 0.10 and s[1] > middle_y + height * 0.10):
                        continue
                    if not (w[0] < center_x - width * 0.12 and e[0] > center_x + width * 0.12):
                        continue
                    if not (w[0] < n[0] < e[0] and w[0] < s[0] < e[0]):
                        continue
                    min_conf = min(n[2], w[2], e[2], s[2])
                    horizontal_balance = abs((center_x - w[0]) - (e[0] - center_x))
                    vertical_balance = abs((middle_y - n[1]) - (s[1] - middle_y))
                    score = abs(w[1] - e[1]) + abs(n[0] - s[0]) + 0.20 * horizontal_balance + 0.10 * vertical_balance - 25 * min_conf
                    group = {"N": n, "W": w, "E": e, "S": s}
                    if best is None or score < best[0]:
                        best = (score, group)
    if best is None:
        raise NamedQuadrantVisionError("UNSUPPORTED_LAYOUT_NAMED_QUADRANT_GEOMETRY")
    return best[1]


def _column_bounds(headings: dict[str, tuple[float, float, float]], width: int) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for seat in "NESW":
        x = headings[seat][0]
        if seat in "NS":
            neighbour_distance = min(abs(x - headings["W"][0]), abs(headings["E"][0] - x))
        else:
            neighbour_distance = min(abs(x - headings["N"][0]), abs(x - headings["S"][0]))
        half = max(width * 0.075, min(width * 0.18, neighbour_distance * 0.44))
        out[seat] = (max(0, int(x - half)), min(width, int(x + half)))
    return out


def _seat_rank_rows(
    image: Any,
    *,
    seat: str,
    headings: dict[str, tuple[float, float, float]],
    bounds: dict[str, tuple[int, int]],
    pytesseract: Any,
) -> list[float]:
    height, width = image.shape[:2]
    x0, x1 = bounds[seat]
    heading_y = headings[seat][1]
    tokens = [
        token for token in _rank_tokens(image, pytesseract)
        if x0 <= token["cx"] <= x1
        and heading_y + height * 0.015 <= token["cy"] <= heading_y + height * 0.20
    ]
    rows = _cluster_rows(tokens, tolerance=max(14.0, width * 0.014))
    centers = [_row_center(row) for row in rows if row]
    selected: list[float] = []
    for center in sorted(centers):
        if selected and center - selected[-1] < height * 0.025:
            continue
        selected.append(center)
        if len(selected) == 4:
            break
    if len(selected) != 4:
        raise NamedQuadrantVisionError(f"INCOMPLETE_NAMED_HAND_ROWS:{seat}:{len(selected)}")
    return selected


def _global_rank_rows(image: Any, headings: dict[str, tuple[float, float, float]], pytesseract: Any) -> list[float]:
    bounds = _column_bounds(headings, image.shape[1])
    return _seat_rank_rows(image, seat="N", headings=headings, bounds=bounds, pytesseract=pytesseract)


def _canonical_rank_order(value: str) -> bool:
    order = "AKQJT98765432"
    if not value or len(set(value)) != len(value):
        return False
    try:
        positions = [order.index(char) for char in value]
    except ValueError:
        return False
    return positions == sorted(positions)


def _box_rank_text(source: Any, *, pytesseract: Any) -> str:
    try:
        boxes = pytesseract.image_to_boxes(
            source,
            config="--psm 7 -c tessedit_char_whitelist=AKQJT9876543210",
        )
    except Exception:
        return ""
    chars: list[tuple[int, str]] = []
    for line in boxes.splitlines():
        fields = line.split()
        if len(fields) < 5:
            continue
        try:
            left = int(fields[1])
        except ValueError:
            continue
        chars.append((left, fields[0].upper()))
    raw = "".join(char for _, char in sorted(chars, key=lambda item: item[0]))
    return _clean_rank_text(raw)


def _choose_repeated_literal(readings: list[str], *, channel: str) -> tuple[str, int] | None:
    legal = [value for value in readings if value and _canonical_rank_order(value)]
    if not legal:
        return None
    counts = Counter(legal)
    max_length = max(len(value) for value in counts)
    longest = {value: support for value, support in counts.items() if len(value) == max_length}
    best, support = max(longest.items(), key=lambda item: (item[1], item[0]))
    if len(longest) > 1 and support < 2:
        raise NamedQuadrantVisionError(f"AMBIGUOUS_CARD_OCR_{channel}:{sorted(longest)}")
    return (best, support) if support >= 2 else None


def _row_crops(image: Any, *, x0: int, x1: int, center_y: float, cv2: Any) -> list[Any]:
    height, _ = image.shape[:2]
    half = max(24, int(height * 0.024))
    y0 = max(0, int(center_y - half)); y1 = min(height, int(center_y + half))
    width = x1 - x0
    crops: list[Any] = []
    for fraction in (0.32, 0.36, 0.40):
        rx0 = min(x1 - 1, x0 + int(width * fraction))
        crop = image[y0:y1, rx0:x1]
        if crop.size == 0:
            continue
        crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        crops.append(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY))
    return crops


def _ocr_rank_row(
    image: Any,
    *,
    x0: int,
    x1: int,
    center_y: float,
    pytesseract: Any,
    cv2: Any,
) -> tuple[str, float]:
    crops = _row_crops(image, x0=x0, x1=x1, center_y=center_y, cv2=cv2)
    box_readings = [_box_rank_text(crop, pytesseract=pytesseract) for crop in crops]
    box_choice = _choose_repeated_literal(box_readings, channel="BOX")
    if box_choice is not None:
        best, support = box_choice
        return best, min(0.92, 0.68 + 0.06 * support)

    text_readings: list[str] = []
    for crop in crops:
        value = _clean_rank_text(
            pytesseract.image_to_string(
                crop,
                config="--psm 7 -c tessedit_char_whitelist=AKQJT9876543210",
            )
        )
        if value:
            text_readings.append(value)
    text_choice = _choose_repeated_literal(text_readings, channel="TEXT")
    if text_choice is None:
        raise NamedQuadrantVisionError("INCOMPLETE_NAMED_HAND_ROW")
    best, support = text_choice
    return best, min(0.88, 0.62 + 0.06 * support)


def _extract_four_column_hands(
    image: Any,
    headings: dict[str, tuple[float, float, float]],
    pytesseract: Any,
    cv2: Any,
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, float]]]:
    _, width = image.shape[:2]
    bounds = _column_bounds(headings, width)
    hands: dict[str, dict[str, str]] = {}
    confidence: dict[str, dict[str, float]] = {}
    cards: list[str] = []
    for seat in "NESW":
        x0, x1 = bounds[seat]
        row_centers = _seat_rank_rows(image, seat=seat, headings=headings, bounds=bounds, pytesseract=pytesseract)
        holdings: list[str] = []
        row_confidence: list[float] = []
        for center_y in row_centers:
            holding, conf = _ocr_rank_row(image, x0=x0, x1=x1, center_y=center_y, pytesseract=pytesseract, cv2=cv2)
            holdings.append(holding); row_confidence.append(conf)
        if sum(len(value) for value in holdings) != 13:
            raise NamedQuadrantVisionError(f"INCOMPLETE_HAND:{seat}:{'.'.join(holdings)}")
        hands[seat] = dict(zip("SHDC", holdings, strict=True))
        confidence[seat] = dict(zip("SHDC", row_confidence, strict=True))
        for suit, ranks in zip("SHDC", holdings, strict=True):
            cards.extend(suit + rank for rank in ranks)

    expected = {suit + rank for suit in "SHDC" for rank in "AKQJT98765432"}
    if len(cards) != 52 or len(set(cards)) != 52:
        raise NamedQuadrantVisionError(f"DECK_VALIDATION_FAILED:{len(cards)}/{len(set(cards))}")
    if set(cards) != expected:
        raise NamedQuadrantVisionError("DECK_VALIDATION_FAILED:NOT_STANDARD_DECK")
    return hands, confidence


def _extract_named_metadata(image: Any, pytesseract: Any) -> tuple[int, str, str, float]:
    """Read only explicit VuBridge header labels from image pixels."""
    texts = [
        pytesseract.image_to_string(image, config="--psm 6"),
        pytesseract.image_to_string(image, config="--psm 11"),
    ]
    dealer_map = {"N":"N","NORTH":"N","E":"E","EAST":"E","S":"S","SOUTH":"S","W":"W","WEST":"W"}
    vul_map = {"NONE":"None","LOVE":"None","NS":"NS","N/S":"NS","N-S":"NS","EW":"EW","E/W":"EW","E-W":"EW","BOTH":"Both","ALL":"Both"}
    for raw in texts:
        text = raw.replace("\n", " ")
        board_match = re.search(r"\bBoard\s*(?:#\s*)?[:.]?\s*(\d{1,3})\b", text, re.IGNORECASE)
        dealer_match = re.search(r"\bDealer\s*[:.]?\s*(North|East|South|West|[NESW])\b", text, re.IGNORECASE)
        vul_match = re.search(r"\bVul(?:nerable|nerability)?\s*[:.]?\s*(None|Love|N\s*[-/]?\s*S|E\s*[-/]?\s*W|Both|All)\b", text, re.IGNORECASE)
        if not board_match or not dealer_match or not vul_match:
            continue
        board = int(board_match.group(1))
        dealer = dealer_map.get(dealer_match.group(1).upper())
        vulnerability = vul_map.get(re.sub(r"\s+", "", vul_match.group(1).upper()))
        if dealer is not None and vulnerability is not None:
            return board, dealer, vulnerability, 0.80
    raise NamedQuadrantVisionError("UNSUPPORTED_LAYOUT_NAMED_QUADRANT_NO_METADATA_HEADER")


def extract_named_quadrant_observation(
    image_bytes: bytes, *, media_type: str, filename: str | None = None
) -> ScreenshotDealObservation:
    cv2, np, pytesseract = _deps()
    image = _decode_named(image_bytes, cv2, np)
    headings = _four_column_headings(image, pytesseract)
    hands, hand_confidence = _extract_four_column_hands(image, headings, pytesseract, cv2)
    board, dealer, vulnerability, metadata_confidence = _extract_named_metadata(image, pytesseract)

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
            "layout_family": ObservedField("named_quadrant", confidence=1.0, source="runtime"),
            "image_sha256": ObservedField(image_sha256, confidence=1.0, source="runtime"),
            "filename": ObservedField(filename, confidence=1.0, source="runtime"),
            "media_type": ObservedField(media_type, confidence=1.0, source="runtime"),
        },
    )
