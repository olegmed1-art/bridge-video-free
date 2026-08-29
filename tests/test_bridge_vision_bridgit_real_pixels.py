from pathlib import Path

import cv2
import numpy as np

from bridge_vision.bridgit_real_pixels import localize_visible_panels


def test_locator_keeps_hand_bands_and_excludes_central_card():
    image = np.zeros((600, 1000, 3), dtype=np.uint8)
    image[30:150, 300:330] = 255
    image[450:570, 600:630] = 255
    image[250:370, 480:560] = 255
    boxes = localize_visible_panels(image)
    assert [(box["x"], box["y"]) for box in boxes] == [(300, 30), (600, 450)]


def test_real_pixel_module_does_not_contain_hand_completion_api():
    text = Path("bridge_vision/bridgit_real_pixels.py").read_text(encoding="utf-8")
    assert "derive_fourth_hand" not in text
    assert "missing_card" not in text
