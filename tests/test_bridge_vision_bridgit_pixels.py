from bridge_vision.bridgit_pixels import exposed_starts_from_scanlines, white_runs

W = (250, 250, 250)
D = (40, 60, 40)


def test_white_runs_ignore_tiny_highlights_and_keep_card_widths():
    row = [D] * 5 + [W] * 3 + [D] * 4 + [W] * 12 + [D] * 2
    assert white_runs(row, min_width=8) == [(12, 24)]


def test_panel_start_requires_repeated_scanline_support():
    rows = [
        [D] * 10 + [W] * 15 + [D] * 20,
        [D] * 10 + [W] * 14 + [D] * 21,
        [D] * 11 + [W] * 13 + [D] * 21,
    ]
    panels = exposed_starts_from_scanlines(rows, y0=50, min_support=2)
    assert len(panels) == 1
    assert panels[0].x in (10, 11)
    assert panels[0].exposed_width == 15


def test_single_scanline_noise_is_not_promoted_to_panel():
    rows = [[D] * 40, [D] * 5 + [W] * 20 + [D] * 15, [D] * 40]
    assert exposed_starts_from_scanlines(rows, min_support=2) == []
