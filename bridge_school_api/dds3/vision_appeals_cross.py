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
from .vision_publication import (
    DEALER_MAP,
    VUL_MAP,
    _decode,
    _deps,
    _extract_hands,
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


def _ocr_appeals_compass(
    image: Any, pytesseract: Any, cv2: Any
) -> dict[str, tuple[float, float, float]]:
    """Locate a visible N/W/E/S compass from pixels using redundant OCR passes.

    Appeals pages contain surrounding prose and a small sparse compass. The first pass
    collects exact one-letter N/W/E/S tokens. If the vertical N/S axis is visible but a
    horizontal letter is missed, a second bounded pixel crop is centered strictly from
    the observed N/S coordinates and reread at high scale for exact W/E glyphs. This is
    OCR redundancy over visible compass pixels, not hand-position or bridge inference.
    """
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

    def collect(source: Any, *, scale: float = 1.0, offset_x: float = 0.0,
                offset_y: float = 0.0, whitelist: str = "NWES",
                psms: tuple[int, ...] = (6, 11, 12), min_conf: float = 0.05) -> None:
        for psm in psms:
            data = pytesseract.image_to_data(
                source,
                config=f"--psm {psm} -c tessedit_char_whitelist={whitelist}",
                output_type=pytesseract.Output.DICT,
            )
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
                cx = offset_x + (x + w / 2) / scale
                cy = offset_y + (y + h / 2) / scale
                add_label(text, cx, cy, conf)

    collect(image)

    # Small W/E labels on appeals forms are often missed by a whole-page OCR pass even
    # when N and S are read. Use only the already-observed N/S pixels to define a narrow
    # horizontal compass band, then reread exact W/E glyphs at higher scale.
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
            variants = [gray]
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            variants.append(binary)
            adaptive = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY, 31, 11,
            )
            variants.append(adaptive)
            for variant in variants:
                scaled = cv2.resize(
                    variant, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC
                )
                collect(
                    scaled,
                    scale=4.0,
                    offset_x=float(x0),
                    offset_y=float(y0),
                    whitelist="WE",
                    psms=(6, 7, 11, 12, 13),
                    min_conf=0.01,
                )

    if any(not labels[seat] for seat in "NWES"):
        raise AppealsCrossVisionError(
            "UNSUPPORTED_LAYOUT_NO_APPEALS_COMPASS:" + ",".join(
                f"{seat}={len(labels[seat])}" for seat in "NWES"
            )
        )

    height, width = image.shape[:2]
    candidates: list[tuple[float, dict[str, tuple[float, float, float]]]] = []
    for n, w, e, s in itertools.product(labels["N"], labels["W"], labels["E"], labels["S"]):
        span_x = e[0] - w[0]; span_y = s[1] - n[1]
        if not (n[1] < s[1] and w[0] < e[0]):
            continue
        if not (12 <= span_x <= width * 0.22 and 12 <= span_y <= height * 0.22):
            continue
        cx = (n[0] + s[0] + w[0] + e[0]) / 4
        cy = (n[1] + s[1] + w[1] + e[1]) / 4
        x_alignment = abs(n[0] - s[0])
        y_alignment = abs(w[1] - e[1])
        horizontal_balance = abs((cx - w[0]) - (e[0] - cx))
        vertical_balance = abs((cy - n[1]) - (s[1] - cy))
        if x_alignment > max(14.0, span_x * 0.35):
            continue
        if y_alignment > max(14.0, span_y * 0.35):
            continue
        if horizontal_balance > max(18.0, span_x * 0.45):
            continue
        if vertical_balance > max(18.0, span_y * 0.45):
            continue
        score = (
            x_alignment
            + y_alignment
            + horizontal_balance
            + vertical_balance
            - 12 * min(n[2], w[2], e[2], s[2])
        )
        candidates.append((score, {"N": n, "W": w, "E": e, "S": s}))
    if not candidates:
        raise AppealsCrossVisionError("UNSUPPORTED_LAYOUT_NO_APPEALS_COMPASS_CLUSTER")
    candidates.sort(key=lambda item: item[0])
    if len(candidates) > 1 and candidates[1][0] <= candidates[0][0] + 1.0:
        first, second = candidates[0][1], candidates[1][1]
        if any(abs(first[seat][0] - second[seat][0]) > 4 or abs(first[seat][1] - second[seat][1]) > 4 for seat in "NWES"):
            raise AppealsCrossVisionError("AMBIGUOUS_APPEALS_COMPASS_CLUSTER")
    return candidates[0][1]


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

    board, dealer, vulnerability, metadata_confidence = _extract_appeals_metadata(
        image, pytesseract, cv2
    )
    try:
        compass = _ocr_appeals_compass(image, pytesseract, cv2)
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
