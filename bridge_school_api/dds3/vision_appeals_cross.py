"""Local/free fail-closed extractor for appeals-form cross diagrams.

This bounded family uses the classic N/W/E/S cross hand geometry together with an
explicit appeals-form metadata header such as ``Board no 2 / Dealer East / N/S
vulnerable``. Cards are read only from pixels; no deck complement, board-derived
metadata, bridge inference repair, paid/cloud vision, or alternate solver is used.
"""
from __future__ import annotations

import hashlib
import itertools
import re
from typing import Any

from .screenshot import ObservedField, ScreenshotDealObservation
from .vision_publication import DEALER_MAP, VUL_MAP, _clean_rank_text, _decode, _deps


class AppealsCrossVisionError(ValueError):
    pass


def _dedicated_dealer_read(image: Any, pytesseract: Any, cv2: Any) -> str | None:
    """Read the word immediately to the right of the explicit Dealer label."""
    height, width = image.shape[:2]
    candidates: list[str] = []
    diagnostics: list[str] = []
    full_dealers = {"NORTH": "N", "EAST": "E", "SOUTH": "S", "WEST": "W"}
    for detector_psm in (6, 11):
        data = pytesseract.image_to_data(image, config=f"--psm {detector_psm}", output_type=pytesseract.Output.DICT)
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
                    text = pytesseract.image_to_string(source, config=f"--psm {psm} -c tessedit_char_whitelist=NorthEastSouthWestNESW")
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
            raise AppealsCrossVisionError(f"APPEALS_DEALER_EXACT_OCR_FAILED:{diagnostics[:16]}")
        return None
    counts = {value: candidates.count(value) for value in set(candidates)}
    if len(counts) != 1:
        raise AppealsCrossVisionError(f"APPEALS_DEALER_OCR_AMBIGUOUS:{candidates}")
    best = next(iter(counts))
    if counts[best] < 2:
        raise AppealsCrossVisionError(f"APPEALS_DEALER_OCR_INSUFFICIENT:{candidates}")
    return best


def _ocr_appeals_compass(image: Any, pytesseract: Any, cv2: Any) -> dict[str, tuple[float, float, float]]:
    """Locate a visible N/W/E/S compass from pixels with fail-closed redundancy."""
    labels: dict[str, list[tuple[float, float, float]]] = {seat: [] for seat in "NWES"}
    seen: set[tuple[str, int, int]] = set()

    def add_label(text: str, cx: float, cy: float, conf: float) -> None:
        text = text.strip().upper()
        if text not in labels:
            return
        key = (text, round(cx), round(cy))
        if key in seen:
            return
        seen.add(key)
        labels[text].append((cx, cy, conf))

    def collect(source: Any, *, scale: float = 1.0, offset_x: float = 0.0, offset_y: float = 0.0,
                whitelist: str = "NWES", psms: tuple[int, ...] = (6, 11, 12), min_conf: float = 0.05) -> None:
        for psm in psms:
            data = pytesseract.image_to_data(source, config=f"--psm {psm} -c tessedit_char_whitelist={whitelist}", output_type=pytesseract.Output.DICT)
            for index, raw in enumerate(data["text"]):
                text = raw.strip().upper()
                if text not in labels:
                    continue
                try:
                    conf = max(0.0, min(1.0, float(data["conf"][index]) / 100.0))
                except (TypeError, ValueError):
                    conf = 0.0
                if conf < min_conf:
                    continue
                x = float(data["left"][index]); y = float(data["top"][index])
                w = float(data["width"][index]); h = float(data["height"][index])
                add_label(text, offset_x + (x + w / 2) / scale, offset_y + (y + h / 2) / scale, conf)

    def collect_boxes(source: Any, expected: str, *, scale: float, offset_x: float, offset_y: float) -> None:
        height = source.shape[0]
        for psm in (10, 13):
            raw_boxes = pytesseract.image_to_boxes(source, config=f"--psm {psm} -c tessedit_char_whitelist={expected}")
            for line in raw_boxes.splitlines():
                parts = line.split()
                if len(parts) < 5 or parts[0].upper() != expected:
                    continue
                try:
                    x0, y0, x1, y1 = map(float, parts[1:5])
                except ValueError:
                    continue
                cx = offset_x + ((x0 + x1) / 2) / scale
                cy_from_top = height - ((y0 + y1) / 2)
                cy = offset_y + cy_from_top / scale
                add_label(expected, cx, cy, 0.50)

    collect(image)
    if labels["N"] and labels["S"] and (not labels["W"] or not labels["E"]):
        height, width = image.shape[:2]
        for n, s in itertools.product(labels["N"], labels["S"]):
            span_y = s[1] - n[1]
            if span_y <= 12 or abs(n[0] - s[0]) > max(16.0, span_y * 0.4):
                continue
            cx = (n[0] + s[0]) / 2
            cy = (n[1] + s[1]) / 2
            half_width = max(50.0, span_y * 1.35)
            half_height = max(18.0, span_y * 0.28)
            x0 = max(0, int(cx - half_width)); x1 = min(width, int(cx + half_width))
            y0 = max(0, int(cy - half_height)); y1 = min(height, int(cy + half_height))
            crop = image[y0:y1, x0:x1]
            if not crop.size:
                continue
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            adaptive = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11)
            for variant in (gray, binary, adaptive):
                scaled = cv2.resize(variant, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
                collect(scaled, scale=4.0, offset_x=float(x0), offset_y=float(y0), whitelist="WE", psms=(6, 7, 11, 12, 13), min_conf=0.01)
            split = max(1, min(crop.shape[1] - 1, int(cx - x0)))
            gap = max(2, int(span_y * 0.08))
            halves = {
                "W": (crop[:, :max(1, split - gap)], x0),
                "E": (crop[:, min(crop.shape[1] - 1, split + gap):], x0 + min(crop.shape[1] - 1, split + gap)),
            }
            for expected, (half, half_x0) in halves.items():
                if labels[expected] or not half.size:
                    continue
                hgray = cv2.cvtColor(half, cv2.COLOR_BGR2GRAY)
                _, hbin = cv2.threshold(hgray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                for variant in (hgray, hbin):
                    scaled = cv2.resize(variant, None, fx=6, fy=6, interpolation=cv2.INTER_CUBIC)
                    collect(scaled, scale=6.0, offset_x=float(half_x0), offset_y=float(y0), whitelist=expected, psms=(10, 13), min_conf=0.0)
                    collect_boxes(scaled, expected, scale=6.0, offset_x=float(half_x0), offset_y=float(y0))

    if any(not labels[seat] for seat in "NWES"):
        raise AppealsCrossVisionError("UNSUPPORTED_LAYOUT_NO_APPEALS_COMPASS:" + ",".join(f"{seat}={len(labels[seat])}" for seat in "NWES"))
    height, width = image.shape[:2]
    candidates: list[tuple[float, dict[str, tuple[float, float, float]]]] = []
    for n, w, e, s in itertools.product(labels["N"], labels["W"], labels["E"], labels["S"]):
        span_x = e[0] - w[0]; span_y = s[1] - n[1]
        if not (n[1] < s[1] and w[0] < e[0]): continue
        if not (12 <= span_x <= width * 0.22 and 12 <= span_y <= height * 0.22): continue
        cx = (n[0] + s[0] + w[0] + e[0]) / 4
        cy = (n[1] + s[1] + w[1] + e[1]) / 4
        x_alignment = abs(n[0] - s[0]); y_alignment = abs(w[1] - e[1])
        horizontal_balance = abs((cx - w[0]) - (e[0] - cx)); vertical_balance = abs((cy - n[1]) - (s[1] - cy))
        if x_alignment > max(14.0, span_x * 0.35) or y_alignment > max(14.0, span_y * 0.35): continue
        if horizontal_balance > max(18.0, span_x * 0.45) or vertical_balance > max(18.0, span_y * 0.45): continue
        score = x_alignment + y_alignment + horizontal_balance + vertical_balance - 12 * min(n[2], w[2], e[2], s[2])
        candidates.append((score, {"N": n, "W": w, "E": e, "S": s}))
    if not candidates: raise AppealsCrossVisionError("UNSUPPORTED_LAYOUT_NO_APPEALS_COMPASS_CLUSTER")
    candidates.sort(key=lambda item: item[0])
    if len(candidates) > 1 and candidates[1][0] <= candidates[0][0] + 1.0:
        first, second = candidates[0][1], candidates[1][1]
        if any(abs(first[seat][0] - second[seat][0]) > 4 or abs(first[seat][1] - second[seat][1]) > 4 for seat in "NWES"):
            raise AppealsCrossVisionError("AMBIGUOUS_APPEALS_COMPASS_CLUSTER")
    return candidates[0][1]


def _read_rank_row(image: Any, *, x0: float, cy: float, span: float, pytesseract: Any, cv2: Any) -> tuple[str, float]:
    """Read one bounded visible holding row from pixels only."""
    height, width = image.shape[:2]
    radius = max(5, int(span * 0.15))
    left = max(0, int(x0)); right = min(width, int(left + max(78.0, span * 2.25)))
    top = max(0, int(cy - radius)); bottom = min(height, int(cy + radius + 1))
    crop = image[top:bottom, left:right]
    if not crop.size: raise AppealsCrossVisionError("EMPTY_APPEALS_HOLDING_CROP")
    crop = cv2.resize(crop, None, fx=5, fy=5, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    adaptive = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 9)
    readings: list[str] = []
    for source in (gray, binary, adaptive):
        for psm in (7, 8, 13):
            value = _clean_rank_text(pytesseract.image_to_string(source, config=f"--psm {psm} -c tessedit_char_whitelist=AKQJT9876543210"))
            readings.append(value)
    nonempty = [value for value in readings if value]
    if not nonempty: return "", 0.60
    counts = {value: nonempty.count(value) for value in set(nonempty)}
    best = max(counts, key=counts.get)
    if len(counts) > 1 and counts[best] < 2:
        raise AppealsCrossVisionError(f"AMBIGUOUS_APPEALS_CARD_OCR:{readings}")
    return best, 0.72 if counts[best] >= 3 else 0.64


def _extract_appeals_hands(image: Any, compass: dict[str, tuple[float, float, float]], pytesseract: Any, cv2: Any) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, float]]]:
    """Extract four hands from the bounded EBU appeals cross geometry.

    Crop coordinates are anchored to the actually OCR-observed compass. They are layout
    geometry only; neither missing cards nor metadata are inferred from bridge rules.
    Each result still has to pass the full 52-unique standard-deck gate.
    """
    n, w, e, s = (compass[seat] for seat in "NWES")
    span = s[1] - n[1]
    if span <= 12: raise AppealsCrossVisionError("APPEALS_COMPASS_SPAN_INVALID")
    axis_x = (n[0] + s[0]) / 2; center_y = (n[1] + s[1]) / 2
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
    hands: dict[str, dict[str, str]] = {}; confidence: dict[str, dict[str, float]] = {}; cards: list[str] = []
    for seat in "NESW":
        holdings: list[str] = []; confs: list[float] = []
        for cy in rows[seat]:
            value, conf = _read_rank_row(image, x0=starts[seat], cy=cy, span=span, pytesseract=pytesseract, cv2=cv2)
            holdings.append(value); confs.append(conf)
        if sum(len(value) for value in holdings) != 13:
            raise AppealsCrossVisionError(f"INCOMPLETE_APPEALS_HAND:{seat}:{'.'.join(holdings)}")
        hands[seat] = dict(zip("SHDC", holdings, strict=True)); confidence[seat] = dict(zip("SHDC", confs, strict=True))
        for suit, ranks in zip("SHDC", holdings, strict=True): cards.extend(suit + rank for rank in ranks)
    expected = {suit + rank for suit in "SHDC" for rank in "AKQJT98765432"}
    if len(cards) != 52 or len(set(cards)) != 52:
        raise AppealsCrossVisionError(f"APPEALS_DECK_VALIDATION_FAILED:{len(cards)}/{len(set(cards))}")
    if set(cards) != expected: raise AppealsCrossVisionError("APPEALS_DECK_VALIDATION_FAILED:NOT_STANDARD_DECK")
    return hands, confidence


def _extract_appeals_metadata(image: Any, pytesseract: Any, cv2: Any) -> tuple[int, str, str, float]:
    text = pytesseract.image_to_string(image, config="--psm 6").replace("\n", " ")
    header = re.search(r"\bBoard\s*(?:no|number)\b", text, re.IGNORECASE)
    if header is None: raise AppealsCrossVisionError("UNSUPPORTED_LAYOUT_APPEALS_HEADER")
    board_match = re.search(r"\bBoard\s*(?:no|number)\s*[:#.]?\s*(\d{1,3})\b", text, re.IGNORECASE)
    dealer_match = re.search(r"\bDealer\s*[:.]?\s*(North|East|South|West|[NESW])\b", text, re.IGNORECASE)
    vul_match = re.search(r"\b(None|Love|N\s*[-/]?\s*S|E\s*[-/]?\s*W|Both|All)\s+vulnerable\b", text, re.IGNORECASE)
    if not board_match or not vul_match: raise AppealsCrossVisionError(f"APPEALS_METADATA_OCR_FAILED:{text[:240]!r}")
    dealer = DEALER_MAP.get(dealer_match.group(1).upper()) if dealer_match else None
    if dealer is None: dealer = _dedicated_dealer_read(image, pytesseract, cv2)
    vul_key = re.sub(r"\s+", "", vul_match.group(1).upper()); vulnerability = VUL_MAP.get(vul_key)
    if dealer is None or vulnerability is None: raise AppealsCrossVisionError(f"APPEALS_METADATA_OCR_FAILED:{text[:240]!r}")
    return int(board_match.group(1)), dealer, vulnerability, 0.78


def extract_appeals_cross_observation(image_bytes: bytes, *, media_type: str, filename: str | None = None) -> ScreenshotDealObservation:
    cv2, np, pytesseract = _deps(); image = _decode(image_bytes, cv2, np)
    board, dealer, vulnerability, metadata_confidence = _extract_appeals_metadata(image, pytesseract, cv2)
    try:
        compass = _ocr_appeals_compass(image, pytesseract, cv2)
        hands, hand_confidence = _extract_appeals_hands(image, compass, pytesseract, cv2)
    except Exception as exc:
        raise AppealsCrossVisionError(str(exc)) from exc
    image_sha256 = hashlib.sha256(image_bytes).hexdigest(); source = "local_tesseract_appeals_cross_v1"
    return ScreenshotDealObservation(
        hands=hands,
        board_number=ObservedField(board, confidence=metadata_confidence, source=source),
        dealer=ObservedField(dealer, confidence=metadata_confidence, source=source),
        vulnerability=ObservedField(vulnerability, confidence=metadata_confidence, source=source),
        hand_confidence=hand_confidence,
        extra_metadata={
            "vision_extractor": ObservedField(source, confidence=1.0, source="runtime"),
            "image_sha256": ObservedField(image_sha256, confidence=1.0, source="runtime"),
            "filename": ObservedField(filename, confidence=1.0, source="runtime"),
            "media_type": ObservedField(media_type, confidence=1.0, source="runtime"),
        },
    )
