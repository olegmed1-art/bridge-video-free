from pathlib import Path

from bridge_vision.native_cards import NativeFourSeatCardDetector
from bridge_vision.ocr_cards import OcrCardLabelBackend


def test_ocr_backend_recognises_only_complete_high_confidence_card_tokens_and_seats():
    def runner(_: Path):
        return 1000, 600, [
            {"text": "A♠", "confidence": 97, "x": 490, "y": 20, "w": 20, "h": 30},
            {"text": "10h", "confidence": 96, "x": 490, "y": 550, "w": 20, "h": 30},
            {"text": "QC", "confidence": 89, "x": 10, "y": 300, "w": 20, "h": 30},
            {"text": "K", "confidence": 99, "x": 970, "y": 300, "w": 20, "h": 30},
        ]
    detector = NativeFourSeatCardDetector(OcrCardLabelBackend(runner), min_card_confidence=0.90)
    result = detector(Path("frame.png"))
    assert result["hands"] == {"N": ["AS"], "S": ["TH"]}
    assert result["confidence"] == 0.96


def test_ocr_backend_rejects_invalid_or_low_confidence_tokens_without_inventing_cards():
    backend = OcrCardLabelBackend(lambda _: (200, 100, [
        {"text": "QH", "confidence": 75, "x": 5, "y": 5, "w": 10, "h": 10},
        {"text": "X♠", "confidence": 99, "x": 5, "y": 5, "w": 10, "h": 10},
    ]), min_ocr_confidence=90)
    payload = backend(Path("frame.png"))
    assert payload["cards"] == []
    assert payload["ocr_evidence"]["rejected_card_tokens"] == [
        {"index": 0, "card": "QH", "reason": "LOW_OCR_CONFIDENCE", "confidence": 75.0}
    ]
