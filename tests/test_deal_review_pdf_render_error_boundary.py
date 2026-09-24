from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

from bridge_vision.deal_review_pdf import _draw_screenshot


def test_canvas_failure_propagates_instead_of_claiming_image_decode_failure(tmp_path: Path):
    frame = tmp_path / "frame.png"
    Image.new("RGB", (4, 4), "white").save(frame)
    canvas = Mock()
    canvas.drawImage.side_effect = RuntimeError("synthetic canvas failure")

    with pytest.raises(RuntimeError, match="synthetic canvas failure"):
        _draw_screenshot(canvas, x=0, y=0, width=100, height=100, shot={"path": frame})


def test_invalid_image_uses_decode_placeholder(tmp_path: Path):
    frame = tmp_path / "corrupted.png"
    frame.write_bytes(b"synthetic invalid image")
    canvas = Mock()

    assert _draw_screenshot(canvas, x=0, y=0, width=100, height=100, shot={"path": frame}) is False
    canvas.drawImage.assert_not_called()


def test_truncated_pixels_use_decode_placeholder_before_canvas(tmp_path: Path):
    frame = tmp_path / "truncated.png"
    Image.new("RGB", (100, 100), "white").save(frame)
    frame.write_bytes(frame.read_bytes()[:-100])
    canvas = Mock()

    assert _draw_screenshot(canvas, x=0, y=0, width=100, height=100, shot={"path": frame}) is False
    canvas.drawImage.assert_not_called()


def test_canvas_oserror_propagates_after_image_decode(tmp_path: Path):
    frame = tmp_path / "frame.png"
    Image.new("RGB", (4, 4), "white").save(frame)
    canvas = Mock()
    canvas.drawImage.side_effect = OSError("synthetic output failure")

    with pytest.raises(OSError, match="synthetic output failure"):
        _draw_screenshot(canvas, x=0, y=0, width=100, height=100, shot={"path": frame})
