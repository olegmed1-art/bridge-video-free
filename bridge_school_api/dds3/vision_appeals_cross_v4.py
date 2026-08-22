"""Bounded EBU appeals extractor with redundant per-row pixel OCR.

The geometry is fixed by the visible N/W/E/S compass. Every holding is read directly
from its bounded row with multiple local Tesseract segmentations. No card is completed
from the deck, and Board/dealer/vulnerability are read only from the explicit appeals
header. A recognized ambiguous/incomplete row fails closed before the strict 52-unique
standard-deck gate.
"""
from __future__ import annotations

import hashlib
import re

from .screenshot import ObservedField, ScreenshotDealObservation
from .vision_appeals_cross import (
    AppealsCrossVisionError,
    _extract_appeals_metadata,
    _ocr_appeals_compass,
)
from .vision_publication import _clean_rank_text, _decode, _deps


def _context_tokens(image, pytesseract, *, psm: int):
    """Independent page-context OCR tokens for one segmentation mode."""
    data = pytesseract.image_to_data(
        image, config=f"--psm {psm}", output_type=pytesseract.Output.DICT
    )
    rows = []
    for index, raw in enumerate(data["text"]):
        text = raw.strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][index])
        except (TypeError, ValueError):
            conf = -1.0
        if conf < 0:
            continue
        rows.append(
            {
                "text": text,
                "x": float(data["left"][index]),
                "y": float(data["top"][index]),
                "w": float(data["width"][index]),
                "h": float(data["height"][index]),
                "conf": conf,
            }
        )
    return rows


def _context_row_candidate(tokens, *, x0: float, cy: float, span: float) -> str | None:
    """Recover the rank substring whose glyph box begins at the expected rank x.

    The boundary is selected only from OCR bounding-box geometry. If Tesseract merges the
    suit glyph into a token (for example ``4J10985``), only the geometrically estimated
    prefix length is removed. Neighbouring suffixes are deliberately not tried: doing so
    would create text alternatives that are not independently supported by pixels.
    """
    candidates: list[tuple[float, str]] = []
    y_tol = max(5.0, span * 0.22)
    for token in tokens:
        center_y = token["y"] + token["h"] / 2
        if abs(center_y - cy) > y_tol:
            continue
        left = token["x"]
        right = left + token["w"]
        if right < x0 + max(2.0, span * 0.08):
            continue
        if left > x0 + span * 0.45:
            continue
        compact = re.sub(r"\s+", "", token["text"].upper())
        if not compact:
            continue
        char_width = token["w"] / max(1, len(compact))
        drop = int(round(max(0.0, x0 - left) / max(1.0, char_width)))
        if drop >= len(compact):
            continue
        suffix = compact[drop:]
        suffix = re.sub(r"^[^AKQJT9876543210]+", "", suffix)
        value = _clean_rank_text(suffix)
        if not value:
            continue
        x_distance = abs(left + drop * char_width - x0)
        score = abs(center_y - cy) + x_distance - min(20.0, token["conf"] / 5.0)
        candidates.append((score, value))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    best_score = candidates[0][0]
    best_values = {value for score, value in candidates if score <= best_score + 0.35}
    return next(iter(best_values)) if len(best_values) == 1 else None


def _context_consensus(token_sets, *, x0: float, cy: float, span: float) -> str | None:
    """Require two independent page segmentations to read the same bounded holding."""
    values = [
        _context_row_candidate(tokens, x0=x0, cy=cy, span=span)
        for tokens in token_sets
    ]
    supported = [value for value in values if value]
    if len(supported) < 2 or len(set(supported)) != 1:
        return None
    return supported[0]


def _near_tie_extension(counts: dict[str, int], best_count: int) -> str | None:
    """Resolve only a strong OCR near-tie where the longer reading is directly supported.

    Tesseract can clip or isolate one or more glyphs at a bounded crop edge, producing a
    full holding together with shorter contiguous fragments of that same holding. This
    helper never invents a rank and never consults the deck: it may choose the unique
    longest *observed* value only when it has at least 90% of the top vote count and every
    other near-top value is a contiguous substring of it. Otherwise ambiguity remains
    fail-closed.
    """
    threshold = best_count * 0.90
    near = [value for value, count in counts.items() if count >= threshold]
    if len(near) < 2:
        return None
    longest_len = max(len(value) for value in near)
    longest = [value for value in near if len(value) == longest_len]
    if len(longest) != 1:
        return None
    candidate = longest[0]
    if all(value == candidate or value in candidate for value in near):
        return candidate
    return None


def _read_row(
    image,
    *,
    x0: float,
    cy: float,
    span: float,
    pytesseract,
    cv2,
    context_token_sets,
):
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
        extension = _near_tie_extension(counts, best_count)
        if extension is None:
            raise AppealsCrossVisionError(f"AMBIGUOUS_APPEALS_CARD_OCR:{counts}")
        winner = extension
    else:
        winner = best[0]
    alternatives = sorted(
        (value for value in cross_scale if value != winner),
        key=lambda value: counts[value],
        reverse=True,
    )
    if alternatives:
        high_alternatives = [
            value for value in alternatives if counts[value] * 3 >= counts[winner] * 2
        ]
        if high_alternatives:
            # When the dominant full holding has strictly more direct OCR votes and every
            # competing high-vote reading is only a clipped contiguous fragment of that
            # same observed string, keep the full direct reading. No missing card is
            # reconstructed and no deck state is consulted.
            dominant_full = (
                all(value in winner for value in high_alternatives)
                and all(counts[winner] > counts[value] for value in high_alternatives)
            )
            if not dominant_full:
                extension = _near_tie_extension(counts, max(counts.values()))
                if extension is not None:
                    winner = extension
                else:
                    context = _context_consensus(
                        context_token_sets, x0=x0, cy=cy, span=span
                    )
                    if context is None:
                        raise AppealsCrossVisionError(
                            f"AMBIGUOUS_APPEALS_CARD_OCR:{counts}:context={context}"
                        )
                    winner = context

    # A pair of independent whole-page segmentations is a second direct pixel reading,
    # not deck inference. It may replace a contaminated narrow-crop reading only when the
    # two segmentations agree exactly on the geometrically bounded row.
    context = _context_consensus(context_token_sets, x0=x0, cy=cy, span=span)
    if context is not None and context != winner:
        winner = context
        confidence = 0.80
    else:
        confidence = min(0.92, 0.62 + 0.03 * counts[winner])
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
    context_token_sets = [
        _context_tokens(image, pytesseract, psm=psm) for psm in (6, 11)
    ]
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
                context_token_sets=context_token_sets,
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
