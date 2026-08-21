"""Local/free fail-closed extractor for classic publication cross diagrams.

This extractor targets real bridge publication diagrams with North above, South below,
West left and East right of a visible N/W/E/S compass. It reads only visible pixels.
It never repairs a missing card from deck complement, never derives dealer/vulnerability
from board number, and rejects layouts whose metadata or 52-card deal is ambiguous.
"""
from __future__ import annotations

import hashlib
import itertools
import re
from typing import Any

from .screenshot import ObservedField, ScreenshotDealObservation

RANKS = set("AKQJT98765432")
VUL_MAP = {
    "NONE": "None",
    "LOVE": "None",
    "NS": "NS",
    "N/S": "NS",
    "N-S": "NS",
    "EW": "EW",
    "E/W": "EW",
    "E-W": "EW",
    "BOTH": "Both",
    "ALL": "Both",
}
DEALER_MAP = {"N": "N", "NORTH": "N", "E": "E", "EAST": "E", "S": "S", "SOUTH": "S", "W": "W", "WEST": "W"}


class PublicationVisionError(ValueError):
    pass


def _deps():
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        import pytesseract  # type: ignore
    except Exception as exc:
        raise PublicationVisionError("LOCAL_VISION_RUNTIME_UNAVAILABLE") from exc
    return cv2, np, pytesseract


def _decode(image_bytes: bytes, cv2: Any, np: Any) -> Any:
    if not image_bytes:
        raise PublicationVisionError("EMPTY_IMAGE")
    image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise PublicationVisionError("IMAGE_DECODE_FAILED")
    height, width = image.shape[:2]
    if width < 250 or height < 180:
        raise PublicationVisionError("IMAGE_TOO_SMALL")
    if width != 700:
        scale = 700.0 / width
        image = cv2.resize(image, (700, max(1, round(height * scale))), interpolation=cv2.INTER_CUBIC)
    return image


def _ocr_compass(image: Any, pytesseract: Any) -> dict[str, tuple[float, float, float]]:
    data = pytesseract.image_to_data(
        image,
        config="--psm 11 -c tessedit_char_whitelist=NWES",
        output_type=pytesseract.Output.DICT,
    )
    labels: dict[str, list[tuple[float, float, float]]] = {seat: [] for seat in "NWES"}
    for i, raw in enumerate(data["text"]):
        text = raw.strip().upper()
        if text not in labels:
            continue
        conf = max(0.0, min(1.0, float(data["conf"][i]) / 100.0))
        if conf < 0.15:
            continue
        x = int(data["left"][i]); y = int(data["top"][i]); w = int(data["width"][i]); h = int(data["height"][i])
        labels[text].append((x + w / 2, y + h / 2, conf))
    if any(not labels[seat] for seat in "NWES"):
        raise PublicationVisionError("UNSUPPORTED_LAYOUT_NO_COMPASS")

    best: tuple[float, dict[str, tuple[float, float, float]]] | None = None
    for n, w, e, s in itertools.product(labels["N"], labels["W"], labels["E"], labels["S"]):
        if not (n[1] < s[1] and w[0] < e[0]):
            continue
        center_x = (n[0] + s[0] + w[0] + e[0]) / 4
        center_y = (n[1] + s[1] + w[1] + e[1]) / 4
        vertical = abs(n[0] - s[0])
        horizontal = abs(w[1] - e[1])
        symmetry = abs((center_y - n[1]) - (s[1] - center_y)) + abs((center_x - w[0]) - (e[0] - center_x))
        span_x = e[0] - w[0]; span_y = s[1] - n[1]
        if not (10 <= span_x <= image.shape[1] * 0.35 and 10 <= span_y <= image.shape[0] * 0.35):
            continue
        score = vertical + horizontal + symmetry - 20 * min(n[2], w[2], e[2], s[2])
        candidate = {"N": n, "W": w, "E": e, "S": s}
        if best is None or score < best[0]:
            best = (score, candidate)
    if best is None:
        raise PublicationVisionError("UNSUPPORTED_LAYOUT_NO_COMPASS_CLUSTER")
    return best[1]


def _clean_rank_text(text: str) -> str:
    """Accept only tokens made entirely of visible rank notation."""
    value = re.sub(r"\s+", "", text.upper()).replace("10", "T")
    if not value or any(ch not in RANKS for ch in value):
        return ""
    return value


def _rank_tokens(image: Any, pytesseract: Any) -> list[dict[str, Any]]:
    data = pytesseract.image_to_data(
        image,
        config="--psm 11 -c tessedit_char_whitelist=AKQJT9876543210",
        output_type=pytesseract.Output.DICT,
    )
    tokens: list[dict[str, Any]] = []
    for i, raw in enumerate(data["text"]):
        text = _clean_rank_text(raw)
        if not text:
            continue
        conf = max(0.0, min(1.0, float(data["conf"][i]) / 100.0))
        x = int(data["left"][i]); y = int(data["top"][i]); w = int(data["width"][i]); h = int(data["height"][i])
        tokens.append({"text": text, "confidence": conf, "x": x, "y": y, "cx": x + w / 2, "cy": y + h / 2})
    return tokens


def _cluster_rows(tokens: list[dict[str, Any]], tolerance: float = 13.0) -> list[list[dict[str, Any]]]:
    clusters: list[list[dict[str, Any]]] = []
    for token in sorted(tokens, key=lambda item: (item["cy"], item["x"])):
        for cluster in clusters:
            center = sum(item["cy"] for item in cluster) / len(cluster)
            if abs(token["cy"] - center) <= tolerance:
                cluster.append(token)
                break
        else:
            clusters.append([token])
    for cluster in clusters:
        cluster.sort(key=lambda item: item["x"])
    clusters.sort(key=lambda cluster: sum(item["cy"] for item in cluster) / len(cluster))
    return clusters


def _row_center(row: list[dict[str, Any]]) -> float:
    return sum(token["cy"] for token in row) / len(row)


def _holding(row: list[dict[str, Any]]) -> tuple[str, float]:
    if not row:
        return "", 0.75
    value = _clean_rank_text("".join(token["text"] for token in sorted(row, key=lambda token: token["x"])))
    if not value:
        raise PublicationVisionError("AMBIGUOUS_RANK_ROW")
    return value, min(token["confidence"] for token in row)


def _extract_hands(image: Any, compass: dict[str, tuple[float, float, float]], pytesseract: Any) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, float]]]:
    """Read four visible rank rows per hand without deck-complement repair.

    West/East share a printed four-row grid. A void on one side is accepted only when the
    opposite side visibly establishes that row, so an absent rank string is tied to an
    observed row rather than inferred from missing cards. If both lateral hands omit the
    same row, the grid has fewer than four supported rows and the image is rejected.
    North/South require four directly observed rank rows and therefore fail closed on a
    void until a future suit-symbol detector can prove the empty row from pixels.
    """
    tokens = _rank_tokens(image, pytesseract)
    height, width = image.shape[:2]
    center_x = sum(value[0] for value in compass.values()) / 4
    center_y = sum(value[1] for value in compass.values()) / 4
    span_x = max(value[0] for value in compass.values()) - min(value[0] for value in compass.values())
    side_gap = max(32.0, span_x * 0.80)

    # North: closest four central rows immediately above compass N.
    north_tokens = [
        token for token in tokens
        if token["cy"] < compass["N"][1]
        and center_x - width * 0.18 < token["cx"] < center_x + width * 0.18
        and compass["N"][1] - token["cy"] < height * 0.34
    ]
    north_rows = _cluster_rows(north_tokens)
    north_rows = north_rows[-4:]
    if len(north_rows) != 4:
        raise PublicationVisionError(f"INCOMPLETE_HAND_ROWS:N:{len(north_rows)}")

    # South: closest four central rows immediately below compass S.
    south_tokens = [
        token for token in tokens
        if token["cy"] > compass["S"][1]
        and center_x - width * 0.18 < token["cx"] < center_x + width * 0.18
        and token["cy"] - compass["S"][1] < height * 0.34
    ]
    south_rows = _cluster_rows(south_tokens)
    south_rows = south_rows[:4]
    if len(south_rows) != 4:
        raise PublicationVisionError(f"INCOMPLETE_HAND_ROWS:S:{len(south_rows)}")

    # Lateral rank text from both W and E establishes one shared S/H/D/C row grid.
    lateral_tokens = [
        token for token in tokens
        if abs(token["cx"] - center_x) > side_gap
        and abs(token["cy"] - center_y) < height * 0.24
    ]
    lateral_rows = _cluster_rows(lateral_tokens)
    if len(lateral_rows) < 4:
        raise PublicationVisionError(f"INCOMPLETE_LATERAL_GRID:{len(lateral_rows)}")
    lateral_rows = sorted(lateral_rows, key=lambda row: abs(_row_center(row) - center_y))[:4]
    lateral_rows.sort(key=_row_center)

    raw_rows: dict[str, list[list[dict[str, Any]]]] = {
        "N": north_rows,
        "S": south_rows,
        "W": [[token for token in row if token["cx"] < center_x] for row in lateral_rows],
        "E": [[token for token in row if token["cx"] > center_x] for row in lateral_rows],
    }

    hands: dict[str, dict[str, str]] = {}
    confidence: dict[str, dict[str, float]] = {}
    cards: list[str] = []
    for hand in "NESW":
        holdings: list[str] = []
        row_conf: list[float] = []
        for row in raw_rows[hand]:
            value, conf = _holding(row)
            holdings.append(value)
            row_conf.append(conf)
        if sum(len(value) for value in holdings) != 13:
            raise PublicationVisionError(f"INCOMPLETE_HAND:{hand}:{'.'.join(holdings)}")
        hands[hand] = dict(zip("SHDC", holdings, strict=True))
        confidence[hand] = dict(zip("SHDC", row_conf, strict=True))
        for suit, ranks in zip("SHDC", holdings, strict=True):
            cards.extend(suit + rank for rank in ranks)

    expected = {suit + rank for suit in "SHDC" for rank in "AKQJT98765432"}
    if len(cards) != 52 or len(set(cards)) != 52:
        raise PublicationVisionError(f"DECK_VALIDATION_FAILED:{len(cards)}/{len(set(cards))}")
    if set(cards) != expected:
        raise PublicationVisionError("DECK_VALIDATION_FAILED:NOT_STANDARD_DECK")
    return hands, confidence


def _extract_metadata(image: Any, pytesseract: Any) -> tuple[int, str, str, float]:
    text = pytesseract.image_to_string(image, config="--psm 6").replace("\n", " ")
    board_match = re.search(r"\bBoard\s*[:#.]?\s*(\d{1,3})\b", text, re.IGNORECASE)
    dealer_match = re.search(r"\bDealer\s*[:.]?\s*(North|East|South|West|[NESW])\b", text, re.IGNORECASE)
    vul_match = re.search(r"\bVul(?:nerable|nerability)?\s*[:.]?\s*(None|Love|N\s*[-/]?\s*S|E\s*[-/]?\s*W|Both|All)\b", text, re.IGNORECASE)
    if vul_match is None:
        vul_match = re.search(r"\b(None|Love|N\s*[-/]?\s*S|E\s*[-/]?\s*W|Both|All)\s+Vul\b", text, re.IGNORECASE)
    if not board_match or not dealer_match or not vul_match:
        raise PublicationVisionError(f"METADATA_OCR_FAILED:{text[:240]!r}")
    board = int(board_match.group(1))
    dealer_key = dealer_match.group(1).upper()
    dealer = DEALER_MAP.get(dealer_key)
    vul_key = re.sub(r"\s+", "", vul_match.group(1).upper())
    vulnerability = VUL_MAP.get(vul_key)
    if dealer is None or vulnerability is None:
        raise PublicationVisionError("METADATA_OCR_INVALID")
    return board, dealer, vulnerability, 0.80


def extract_publication_cross_observation(image_bytes: bytes, *, media_type: str, filename: str | None = None) -> ScreenshotDealObservation:
    """Extract a full classic publication cross diagram from actual image bytes."""
    cv2, np, pytesseract = _deps()
    image = _decode(image_bytes, cv2, np)
    compass = _ocr_compass(image, pytesseract)
    hands, hand_confidence = _extract_hands(image, compass, pytesseract)
    board, dealer, vulnerability, metadata_confidence = _extract_metadata(image, pytesseract)
    image_sha256 = hashlib.sha256(image_bytes).hexdigest()
    source = "local_tesseract_publication_cross_v1"
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
