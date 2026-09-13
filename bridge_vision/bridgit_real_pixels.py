"""Opt-in real-pixel probe for Bridgit card corners.

This is deliberately a SHADOW-only backend.  It localizes visible white card
panels, reads rank text and suit shape through independent channels, and emits a
card only when both channels clear their own absolute and separation gates.
Templates come only from explicit labelled reference crops; no hand completion
or ordering inference is performed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import pytesseract

from .graphic_cards import GraphicCardBackend
from .native_cards import NativeFourSeatCardDetector


class BridgitRealPixelError(ValueError):
    pass


def _rank(crop) -> tuple[str | None, float]:
    crop = cv2.copyMakeBorder(crop, 8, 8, 8, 8, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    crop = cv2.resize(crop, None, fx=6, fy=6, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY)
    data = pytesseract.image_to_data(
        mask,
        config="--psm 10 -c tessedit_char_whitelist=AKQJT234567890",
        output_type=pytesseract.Output.DICT,
    )
    choices = []
    for text, confidence in zip(data.get("text", []), data.get("conf", [])):
        token = str(text).strip().upper()
        try:
            score = float(confidence) / 100.0
        except (TypeError, ValueError):
            continue
        if token == "10":
            token = "T"
        if token in set("AKQJT98765432") and 0.0 <= score <= 1.0:
            choices.append((token, score))
    return max(choices, key=lambda item: item[1]) if choices else (None, 0.0)


def _shape(crop):
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    mask = (gray < 180).astype("uint8") * 255
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    components = [(int(stats[i, cv2.CC_STAT_AREA]), i) for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] >= 5]
    if not components:
        return None
    _, index = max(components)
    component = (labels == index).astype("uint8") * 255
    contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return max(contours, key=cv2.contourArea) if contours else None


def _suit(crop, templates: Mapping[str, Any], *, max_distance: float, min_margin: float) -> tuple[str | None, float, str | None]:
    contour = _shape(crop)
    if contour is None:
        return None, 0.0, "NO_SUIT_SHAPE"
    distances = sorted(
        ((suit, float(cv2.matchShapes(contour, template, cv2.CONTOURS_MATCH_I1, 0))) for suit, template in templates.items()),
        key=lambda item: (item[1], item[0]),
    )
    suit, best = distances[0]
    second = distances[1][1] if len(distances) > 1 else 1.0
    confidence = max(0.0, 1.0 - best)
    if best > max_distance:
        return None, confidence, "LOW_SUIT_SHAPE_CONFIDENCE"
    if second - best < min_margin:
        return None, confidence, "AMBIGUOUS_SUIT_SHAPE"
    return suit, confidence, None


def localize_visible_panels(image) -> list[dict[str, int]]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, white = cv2.threshold(gray, 225, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    height, width = gray.shape
    table_right = min(width, 1200)
    panels = []
    for contour in contours:
        x, y, w, h = (int(v) for v in cv2.boundingRect(contour))
        if x >= table_right or w < 18 or h < 30 or w * h < 500:
            continue
        # Keep only the four hand bands. Central/trick cards are deliberately
        # excluded rather than assigned through bidding or temporal context.
        horizontal = h >= 100 and (y < height * 0.30 or y > height * 0.70)
        vertical = w >= 18 and (x < table_right * 0.18 or x > table_right * 0.82)
        if horizontal or vertical:
            panels.append({"x": x, "y": y, "w": w, "h": h})
    return sorted(panels, key=lambda box: (box["y"], box["x"]))


class BridgitRealPixelRunner:
    """Low-level runner consumed through GraphicCardBackend + native geometry."""

    shadow_only = True

    def __init__(self, reference_frame: Path, labelled_templates: Sequence[Mapping[str, Any]], *, max_suit_distance: float = .05, min_suit_margin: float = .08):
        reference = cv2.imread(str(reference_frame))
        if reference is None:
            raise BridgitRealPixelError("reference frame cannot be read")
        templates = {}
        for raw in labelled_templates:
            suit = str(raw.get("suit") or "").upper()
            if suit not in {"S", "H", "D", "C"} or suit in templates:
                raise BridgitRealPixelError("templates require one explicit crop per suit")
            x, y = int(raw["x"]), int(raw["y"])
            contour = _shape(reference[y + 18:y + 43, x + 1:x + 26])
            if contour is None:
                raise BridgitRealPixelError("labelled suit crop has no shape")
            templates[suit] = contour
        if set(templates) != {"S", "H", "D", "C"}:
            raise BridgitRealPixelError("complete explicit suit templates are required")
        self.templates = templates
        self.max_suit_distance = float(max_suit_distance)
        self.min_suit_margin = float(min_suit_margin)

    def __call__(self, frame: Path) -> Mapping[str, Any]:
        image = cv2.imread(str(frame))
        if image is None:
            raise BridgitRealPixelError("frame cannot be read")
        candidates = []
        for box in localize_visible_panels(image):
            x, y = box["x"], box["y"]
            rank, rank_confidence = _rank(image[y:y + 22, x + 1:x + min(box["w"], 20)])
            suit, suit_confidence, suit_reason = _suit(
                image[y + 18:y + 43, x + 1:x + min(box["w"], 26)],
                self.templates,
                max_distance=self.max_suit_distance,
                min_margin=self.min_suit_margin,
            )
            candidates.append({
                "rank": rank,
                "rank_confidence": rank_confidence,
                "suit": suit,
                "suit_confidence": suit_confidence,
                "box": box,
                "pixel_evidence": {"suit_reason": suit_reason},
            })
        height, width = image.shape[:2]
        return {
            "table_region": {"x": 0, "y": 0, "w": min(width, 1200), "h": height},
            "candidates": candidates,
        }


def build_shadow_detector(reference_frame: Path, labelled_templates: Sequence[Mapping[str, Any]], *, min_rank_confidence: float = .90, min_suit_confidence: float = .90):
    runner = BridgitRealPixelRunner(reference_frame, labelled_templates)
    detector = NativeFourSeatCardDetector(
        GraphicCardBackend(runner, min_rank_confidence=min_rank_confidence, min_suit_confidence=min_suit_confidence),
        min_card_confidence=min(min_rank_confidence, min_suit_confidence),
    )
    detector.shadow_only = True
    return detector


__all__ = ["BridgitRealPixelError", "BridgitRealPixelRunner", "build_shadow_detector", "localize_visible_panels"]
