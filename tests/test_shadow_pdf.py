from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageDraw
from pypdf import PdfReader

from bridge_vision.shadow_pdf import render_shadow_pdf
from bridge_vision.shadow_pbn import build_shadow_deal_views


def _frame(path: Path, marker: str) -> str:
    image = Image.new("RGB", (640, 360), "#DDE5F2")
    ImageDraw.Draw(image).text((40, 170), marker, fill="#172033")
    image.save(path, format="PNG")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _complete_suit(suit: str) -> list[str]:
    return [rank + suit for rank in "AKQJT98765432"]


def _record(frame: Path, sha256: str, *, time: float) -> dict:
    return {
        "status": "PARTIAL_BOARD_OBSERVATION",
        "frame_file": frame.name,
        "frame_sha256": sha256,
        "time": time,
        "candidates": [
            {
                "hands": {
                    "N": _complete_suit("S"),
                    "E": _complete_suit("H"),
                    "S": _complete_suit("D"),
                },
                "confidence": 0.97,
                "evidence": {
                    "canonical_promotion_allowed": False,
                    "deal_identity": {
                        "kind": "EXPLICIT_BOARD",
                        "scope": "field-test",
                        "value": "board-1",
                    },
                    "board_metadata": {
                        "status": "CONFIRMED",
                        "board_number": 1,
                        "dealer": "N",
                        "vulnerability": "NONE",
                    },
                },
            }
        ],
        "diagnostics": [],
        "conflicts": [],
    }


def test_pdf_keeps_observed_and_exact_derived_fourth_hand_visibly_separate(tmp_path: Path):
    frames = tmp_path / "frames"
    frames.mkdir()
    screenshot = frames / "board-1.png"
    sha256 = _frame(screenshot, "HASH-BOUND SCREENSHOT")
    record = _record(screenshot, sha256, time=429.0)

    views = build_shadow_deal_views([record])
    assert len(views) == 1
    assert views[0]["observed_count"] == 39
    assert views[0]["observed"]["hands"]["W"]["unknown_count"] == 13
    assert views[0]["reconstruction_status"] == "DERIVED_39_TO_13"
    assert views[0]["reconstructed"]["card_provenance"]["W"]["derived_cards"] == _complete_suit("C")

    output = tmp_path / "bridge_positions_profiled_shadow_report.pdf"
    report = render_shadow_pdf(
        [record],
        frames_root=frames,
        output_path=output,
        source="fixture-source",
    )

    assert report["pages"] == 1
    assert report["deals"] == 1
    assert report["screenshots_embedded"] == 1
    assert report["result_scope"] == "SHADOW_ONLY"
    assert report["canonical_promotion_allowed"] is False
    text = PdfReader(str(output)).pages[0].extract_text()
    assert "Распознано (OBSERVED) - 39/52" in text
    assert "Достроенный расклад - DERIVED_39_TO_13" in text
    assert "W - DERIVED" in text
    assert "Торговля" in text
    assert "NOT CANONICAL" in text
