"""Unexpected recognizer failures must not look like rejected observations."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from bridge_vision import bridgit_primary_video_r264 as primary


class FakeCapture:
    def __init__(self):
        self.released = False

    def isOpened(self):
        return True

    def get(self, property_id):
        return {1: 1.0, 2: 3, 3: 1920, 4: 1080}[property_id]

    def release(self):
        self.released = True


@pytest.fixture
def video_pass(monkeypatch, tmp_path):
    frame = SimpleNamespace(shape=(1080, 1920), copy=lambda: None)
    capture = FakeCapture()
    cv2 = SimpleNamespace(
        CAP_PROP_FPS=1, CAP_PROP_FRAME_COUNT=2,
        CAP_PROP_FRAME_WIDTH=3, CAP_PROP_FRAME_HEIGHT=4,
        IMREAD_COLOR=5, imread=lambda *_args: frame,
        VideoCapture=lambda *_args: capture,
    )
    profile = SimpleNamespace(width=1920, height=1080)
    monkeypatch.setattr(primary.rank_layout, "load_profile", lambda *_args: profile)
    monkeypatch.setattr(primary.rank_layout, "_pixel_runtime", lambda: (cv2, None))
    monkeypatch.setattr(primary.rank_layout, "_template_bank", lambda *_args: {})
    monkeypatch.setattr(primary, "variant_for_pinned_sprite_sha256", lambda *_args: 1)
    monkeypatch.setattr(primary, "load_sprite", lambda *_args, **_kwargs: SimpleNamespace(card_width=109, card_height=147))
    monkeypatch.setattr(primary, "derive_original_asset_reference", lambda *_args, **_kwargs: frame)
    monkeypatch.setattr(primary, "_frame_at", lambda *_args: frame)
    monkeypatch.setattr(primary, "frame_signature", lambda *_args: "signature")
    monkeypatch.setattr(primary, "EventFrameSelector", lambda **_kwargs: SimpleNamespace(observe=lambda *_args: SimpleNamespace(reason="stable")))
    monkeypatch.setattr(primary, "_full_geometry_gate", lambda *_args: None)

    def run():
        return primary.recognize_video_primary(
            Path("video.mp4"), reference_frame=Path("reference.png"),
            profile_path=Path("profile.json"), output_dir=tmp_path,
            gambler_sprite_path=Path("all-v1.png"), gambler_sprite_sha256="pinned",
            scan_ms=1000,
        )

    return run, capture, monkeypatch


def test_invalid_frame_signature_is_an_observation_rejection(video_pass):
    run, capture, monkeypatch = video_pass
    monkeypatch.setattr(primary, "frame_signature", lambda *_args: (_ for _ in ()).throw(ValueError("invalid frame")))
    result = run()
    assert result["status"] == "NO_FULL_LAYOUT_ACCEPTED"
    assert result["rejections"] == {"event_signature_rejected": 3}
    assert capture.released


def test_unexpected_signature_failure_propagates_and_releases_capture(video_pass):
    run, capture, monkeypatch = video_pass
    monkeypatch.setattr(primary, "frame_signature", lambda *_args: (_ for _ in ()).throw(RuntimeError("signature bug")))
    with pytest.raises(RuntimeError, match="signature bug"):
        run()
    assert capture.released


def test_selector_failure_propagates_and_releases_capture(video_pass):
    run, capture, monkeypatch = video_pass
    monkeypatch.setattr(primary, "EventFrameSelector", lambda **_kwargs: SimpleNamespace(observe=lambda *_args: (_ for _ in ()).throw(ValueError("selector state bug"))))
    with pytest.raises(ValueError, match="selector state bug"):
        run()
    assert capture.released


def test_invalid_first_geometry_remains_an_observation_rejection(video_pass):
    run, capture, _ = video_pass
    result = run()
    assert result["status"] == "NO_FULL_LAYOUT_ACCEPTED"
    assert result["rejections"] == {"full_geometry_not_proven": 3}
    assert capture.released


def test_unexpected_first_geometry_failure_propagates(video_pass):
    run, capture, monkeypatch = video_pass
    monkeypatch.setattr(primary, "_full_geometry_gate", lambda *_args: (_ for _ in ()).throw(RuntimeError("geometry bug")))
    with pytest.raises(RuntimeError, match="geometry bug"):
        run()
    assert capture.released


def test_unexpected_second_geometry_failure_propagates(video_pass):
    run, capture, monkeypatch = video_pass
    calls = 0

    def geometry(*_args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("second geometry bug")
        return {"N": {"S": 13}}

    monkeypatch.setattr(primary, "_full_geometry_gate", geometry)
    with pytest.raises(RuntimeError, match="second geometry bug"):
        run()
    assert capture.released
