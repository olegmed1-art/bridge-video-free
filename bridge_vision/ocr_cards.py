"""OCR pixel backend for bridge-card labels shown in a video frame.

Recognise only complete rank+suit tokens. Incomplete or low-confidence OCR is
not evidence of a card. Seat assignment remains native-card logic.
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any, Callable, Mapping

OCR_CARD_BACKEND_VERSION = "bridge-ocr-card-labels-v1"
_TOKEN = re.compile(r"^(10|[2-9AKQJT])([SHDC♠♥♦♣])$", re.IGNORECASE)
_SUITS = {"♠": "S", "♥": "H", "♦": "D", "♣": "C"}
OcrRunner = Callable[[Path], tuple[int, int, list[Mapping[str, Any]]]]

def _normalise_token(value: Any) -> str | None:
    token = str(value or "").strip().upper().replace(" ", "")
    match = _TOKEN.fullmatch(token)
    if not match:
        return None
    rank, suit = match.groups()
    return ("T" if rank == "10" else rank) + _SUITS.get(suit, suit)

def tesseract_token_runner(frame: Path) -> tuple[int, int, list[Mapping[str, Any]]]:
    try:
        from PIL import Image
        import pytesseract
    except ImportError as exc:
        raise RuntimeError("Pillow and pytesseract are required for OCR card recognition") from exc
    with Image.open(frame) as image:
        width, height = image.size
        raw = pytesseract.image_to_data(image, config="--psm 11", output_type=pytesseract.Output.DICT)
    tokens = []
    for index, text in enumerate(raw.get("text", [])):
        tokens.append({"text": text, "confidence": raw.get("conf", [])[index], "x": raw.get("left", [])[index], "y": raw.get("top", [])[index], "w": raw.get("width", [])[index], "h": raw.get("height", [])[index]})
    return width, height, tokens

class OcrCardLabelBackend:
    def __init__(self, runner: OcrRunner | None = None, *, min_ocr_confidence: float = 90.0):
        if not 0.0 <= min_ocr_confidence <= 100.0:
            raise ValueError("min_ocr_confidence outside [0,100]")
        self.runner = runner or tesseract_token_runner
        self.min_ocr_confidence = float(min_ocr_confidence)
    def __call__(self, frame: Path) -> Mapping[str, Any]:
        width, height, tokens = self.runner(frame)
        if width <= 0 or height <= 0:
            raise ValueError("OCR frame dimensions must be positive")
        cards, rejected = [], []
        for index, token in enumerate(tokens):
            card = _normalise_token(token.get("text"))
            if card is None:
                continue
            try:
                confidence = float(token.get("confidence")); x, y, w, h = (float(token[key]) for key in ("x", "y", "w", "h"))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid OCR token at index {index}") from exc
            if w <= 0 or h <= 0:
                rejected.append({"index": index, "card": card, "reason": "INVALID_BOX"})
            elif confidence < self.min_ocr_confidence:
                rejected.append({"index": index, "card": card, "reason": "LOW_OCR_CONFIDENCE", "confidence": confidence})
            else:
                cards.append({"card": card, "confidence": confidence / 100.0, "box": {"x": x, "y": y, "w": w, "h": h}})
        return {"table_region": {"x": 0, "y": 0, "w": float(width), "h": float(height)}, "cards": cards, "ocr_evidence": {"backend_version": OCR_CARD_BACKEND_VERSION, "token_count": len(tokens), "accepted_card_token_count": len(cards), "rejected_card_tokens": rejected, "min_ocr_confidence": self.min_ocr_confidence}}

__all__ = ["OCR_CARD_BACKEND_VERSION", "OcrCardLabelBackend", "tesseract_token_runner"]
