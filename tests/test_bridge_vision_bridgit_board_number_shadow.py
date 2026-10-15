"""Synthetic board-tile checks for the offline partial-observation adapter."""

import pytest

from bridge_vision.bridgit_board_number import read_board_number


@pytest.mark.parametrize("number", [1, 2, 12, 21])
def test_reader_accepts_clear_synthetic_compass_digits(number):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    frame = np.full((1010, 1920, 3), 215, dtype=np.uint8)
    cv2.putText(frame, str(number), (1236 if number > 9 else 1240, 66),
                cv2.FONT_HERSHEY_TRIPLEX, 0.5 if number > 9 else 0.55,
                (0, 0, 0), 1, cv2.LINE_AA)
    assert read_board_number(frame)["number"] == number


def test_reader_rejects_blank_or_unsupported_frame():
    np = pytest.importorskip("numpy")
    assert read_board_number(np.full((1010, 1920, 3), 215, dtype=np.uint8))["status"] == "UNREADABLE"
    assert read_board_number(np.full((100, 100, 3), 215, dtype=np.uint8))["reason"] == "UNSUPPORTED_FRAME"
