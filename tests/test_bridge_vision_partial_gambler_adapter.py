"""Synthetic contract checks; no pinned sprite bytes or real-video claims."""
from hashlib import sha256
from pathlib import Path

import pytest

import bridge_vision.bridgit_partial_gambler_adapter as adapter
from bridge_vision.bridgit_adjacent_candidate_profile import PROFILE
from bridge_vision.bridgit_partial_evidence import PartialEvidenceError, SamplingContract
from bridge_vision.gambler_classic_reference import GamblerClassicSprite
from bridge_vision.gambler_reference_authority import pinned_sprite_sha256


def _contract() -> SamplingContract:
    return SamplingContract(PROFILE.version, 1000, 3000, PROFILE.max_gap_ms, 0.80)


def _samples(tmp_path: Path) -> list[dict]:
    samples = []
    for index, timestamp in enumerate((1000, 4000)):
        path = tmp_path / f"frame-{index}.png"
        data = f"synthetic-frame-{index}".encode()
        path.write_bytes(data)
        read = {"status": "READ", "number": 12}
        samples.append({
            "frame_path": path, "frame_sha256": sha256(data).hexdigest(),
            "source_video_sha256": "a" * 64, "episode_id": "episode-a",
            "board_epoch": 1, "board_number": 12,
            "board_number_read": dict(read), "board_number_confirming_read": dict(read),
            "timestamp_ms": timestamp, "observation_index": index,
            "raw_frame_size": {"width": 1920, "height": 1010},
            "data_class": "SYNTHETIC_REGRESSION",
        })
    return samples


def _bind_synthetic_sprite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Replace only the asset loading seam; production requires pinned bytes."""
    path = tmp_path / "synthetic-sprite.png"
    expected = pinned_sprite_sha256(5)
    sprite = GamblerClassicSprite(5, expected, 1417, 588, 109, 147, b"synthetic-only")

    def resolve(root, *, verified_card_width_px, verified_card_height_px):
        assert root == tmp_path
        assert (verified_card_width_px, verified_card_height_px) == (109, 147)
        return 5, path, expected

    def load(selected, *, expected_sha256, expected_variant):
        assert (selected, expected_sha256, expected_variant) == (path, expected, 5)
        return sprite

    monkeypatch.setattr(adapter, "resolve_original_gambler_asset", resolve)
    monkeypatch.setattr(adapter, "_load_shadow_sprite", load)


def _run(samples: list[dict], tmp_path: Path, observer):
    return adapter.accumulate_gambler_partial_frames(
        samples, asset_root=tmp_path, verified_card_width_px=109,
        verified_card_height_px=147, pixel_observer=observer, contract=_contract(),
    )


def test_unknown_factor_components_reach_temporal_history_without_promotion(tmp_path, monkeypatch):
    _bind_synthetic_sprite(monkeypatch, tmp_path)
    diagnostic = {"best_card": "KS", "rank_margin": .7, "suit_margin": .001,
                  "roi_xyxy": [10, 20, 30, 40], "roi_bgr_sha256": "b" * 64}
    unknown = {"index": 0, "reason": "CARD_UNCERTAIN", "factor_diagnostic": diagnostic,
               "receipt": {"anchor_xy": [10, 20]}}
    def observer(path, sprite):
        return {"table_region": {"x": 0, "y": 0, "w": 100, "h": 100},
                "cards": [], "unknown": [unknown]}
    result = _run(_samples(tmp_path), tmp_path, observer)
    history = result["recovery"]["history"][0]
    assert history["frame"]["rejected_observations"][0] == unknown
    assert history["component_observations"][0]["raw_factor_diagnostic"] == diagnostic
    assert len(history["component_observations"][0]["rank"]["domain"]) == 13
    assert result["recovery"]["status"] == "CLOSED_UNKNOWN"


def test_shadow_sprite_loader_preserves_png_bytes_on_windows(tmp_path: Path):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    ok, encoded = cv2.imencode(".png", np.zeros((588, 1417, 4), dtype=np.uint8))
    assert ok
    payload = encoded.tobytes()
    path = tmp_path / "all.png"
    path.write_bytes(payload)
    sprite = adapter._load_shadow_sprite(path, expected_sha256=sha256(payload).hexdigest(),
                                          expected_variant=5)
    assert sprite.payload == payload


def _real_png_samples(tmp_path: Path) -> list[dict]:
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    samples = _samples(tmp_path)
    for index, sample in enumerate(samples):
        image = np.zeros((934, 1680, 3), dtype=np.uint8)
        image[0, 0, 0] = index
        assert cv2.imwrite(str(sample["frame_path"]), image)
        sample["frame_sha256"] = sha256(sample["frame_path"].read_bytes()).hexdigest()
        sample["raw_frame_size"] = {"width": 1680, "height": 934}
        sample["data_class"] = "REAL_SHADOW"
    return samples


class _SyntheticAutoObserver:
    geometry = type("Geometry", (), {"auto_discover": True, "width": 1680, "height": 934})()

    def __init__(self):
        self.called = []

    def __call__(self, path, sprite):
        self.called.append(path)
        return {"table_region": {"x": 0, "y": 0, "w": 100, "h": 100},
                "cards": [], "unknown": [],
                "corner_detection": {"mode": "PINNED_BORDER_SEAT_BANDS"}}


def test_missing_pinned_asset_fails_closed_before_pixel_observer(tmp_path: Path):
    called = []
    with pytest.raises(PartialEvidenceError, match="PINNED_GAMBLER_ASSET_UNAVAILABLE"):
        _run(_samples(tmp_path), tmp_path, lambda path, sprite: called.append(path))
    assert called == []


def test_synthetic_observer_receives_bound_sprite_and_accumulates_review_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    _bind_synthetic_sprite(monkeypatch, tmp_path)
    samples = _samples(tmp_path)
    called = []

    def observer(path, sprite):
        called.append((path, sprite.sprite_sha256))
        card = "AS" if path.name == samples[0]["frame_path"].name else "KS"
        return {"table_region": {"x": 0, "y": 0, "w": 100, "h": 100},
                "cards": [{"card": card, "confidence": 0.95,
                           "box": {"x": 45, "y": 5, "w": 10, "h": 10}}]}

    result = _run(samples, tmp_path, observer)
    track = result["tracks"][0]
    assert track["status"] == "REVIEW_PARTIAL"
    assert track["frame_sha256s"] == [sample["frame_sha256"] for sample in samples]
    assert track["identity"]["board_number"] == 12
    assert track["deal"]["hands"]["N"]["cards"] == ["AS", "KS"]
    assert track["canonical_promotion_allowed"] is False
    assert result["real_video_accuracy_evaluated"] is False
    assert result["reference_provenance"]["pinned_sprite_sha256"] == pinned_sprite_sha256(5)
    assert [receipt["frame_sha256"] for receipt in result["detector_receipts"]] == [
        sample["frame_sha256"] for sample in samples]
    assert all(receipt["coordinate_space"] == "LOCAL_FRAME_PNG"
               and receipt["board_number_read_from_pixels"] is None
               for receipt in result["detector_receipts"])
    assert [(path.name, sprite_sha) for path, sprite_sha in called] == [
        (sample["frame_path"].name, pinned_sprite_sha256(5)) for sample in samples]


@pytest.mark.parametrize("fault,error", [
    ("board", "FRAME_IDENTITY_UNPROVEN"),
    ("reference", "REFERENCE_PROFILE_MISMATCH"),
    ("hash", "FRAME_HASH_MISMATCH"),
])
def test_identity_and_frame_integrity_faults_never_reach_unverified_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str, error: str,
):
    _bind_synthetic_sprite(monkeypatch, tmp_path)
    samples = _samples(tmp_path)
    if fault == "board":
        samples[0]["board_number_confirming_read"] = {"status": "UNREADABLE", "number": None}
    elif fault == "reference":
        samples[0]["reference_profile_id"] = "other-reference"
    else:
        samples[0]["frame_sha256"] = "0" * 64
    called = []
    with pytest.raises(PartialEvidenceError, match=error):
        _run(samples, tmp_path, lambda path, sprite: called.append(path))
    assert called == []


def test_real_shadow_requires_upper_right_board_read_and_automatic_corners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    _bind_synthetic_sprite(monkeypatch, tmp_path)
    samples = _real_png_samples(tmp_path)
    observed = []

    def pixel_observer(path, sprite):
        observed.append(path)
        return {"table_region": {"x": 0, "y": 0, "w": 100, "h": 100},
                "cards": [], "unknown": []}

    monkeypatch.setattr(adapter, "read_board_number",
                        lambda image: {"status": "UNREADABLE", "number": None})
    with pytest.raises(PartialEvidenceError, match="BOARD_NUMBER_PIXEL_MISMATCH"):
        _run(samples, tmp_path, pixel_observer)
    assert observed == []
    monkeypatch.setattr(adapter, "read_board_number",
                        lambda image: {"status": "READ", "number": 12})
    with pytest.raises(PartialEvidenceError, match="REAL_CORNER_GEOMETRY_UNPROVEN"):
        _run(samples, tmp_path, pixel_observer)
    from bridge_vision.bridgit_partial_pixel_observer import (
        CardCorner, GamblerPartialPixelObserver, PartialPixelGeometry,
    )
    manual = GamblerPartialPixelObserver(
        PartialPixelGeometry(1680, 934, 10, 10, (0, 0, 100, 100),
                             (CardCorner(40, 0, "N"),)))
    with pytest.raises(PartialEvidenceError, match="REAL_CORNER_GEOMETRY_UNPROVEN"):
        _run(samples, tmp_path, manual)
    monkeypatch.setattr(adapter, "GamblerPartialPixelObserver", _SyntheticAutoObserver)
    result = _run(samples, tmp_path, _SyntheticAutoObserver())
    assert len(result["tracks"]) == 1
    assert result["tracks"][0]["canonical_promotion_allowed"] is False


@pytest.mark.parametrize("declared", [
    {"width": 1, "height": 934},
    {"width": 1680, "height": 1},
])
def test_real_shadow_rejects_declared_size_that_disagrees_with_decoded_pixels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declared: dict,
):
    _bind_synthetic_sprite(monkeypatch, tmp_path)
    samples = _real_png_samples(tmp_path)
    samples[0]["raw_frame_size"] = declared
    monkeypatch.setattr(adapter, "GamblerPartialPixelObserver", _SyntheticAutoObserver)
    board_reads = []
    monkeypatch.setattr(adapter, "read_board_number",
                        lambda image: board_reads.append(image.shape) or {"status": "READ", "number": 12})
    observer = _SyntheticAutoObserver()
    with pytest.raises(PartialEvidenceError, match="RAW_FRAME_SIZE_MISMATCH"):
        _run(samples, tmp_path, observer)
    assert board_reads == []
    assert observer.called == []


@pytest.mark.parametrize("declared", [
    None, {}, {"width": None, "height": 934},
    {"width": True, "height": 934}, {"width": "1680", "height": 934},
    {"width": 0, "height": 934}, {"width": 1680, "height": -1},
])
def test_real_shadow_rejects_unknown_or_malformed_declared_size_before_decode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declared,
):
    _bind_synthetic_sprite(monkeypatch, tmp_path)
    samples = _real_png_samples(tmp_path)
    samples[0]["raw_frame_size"] = declared
    monkeypatch.setattr(adapter, "GamblerPartialPixelObserver", _SyntheticAutoObserver)
    board_reads = []
    monkeypatch.setattr(adapter, "read_board_number",
                        lambda image: board_reads.append(image.shape) or {"status": "READ", "number": 12})
    observer = _SyntheticAutoObserver()
    with pytest.raises(PartialEvidenceError, match="FRAME_IDENTITY_UNPROVEN"):
        _run(samples, tmp_path, observer)
    assert board_reads == []
    assert observer.called == []


def test_real_shadow_receipt_binds_declared_and_decoded_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    _bind_synthetic_sprite(monkeypatch, tmp_path)
    samples = _real_png_samples(tmp_path)
    monkeypatch.setattr(adapter, "GamblerPartialPixelObserver", _SyntheticAutoObserver)
    monkeypatch.setattr(adapter, "read_board_number",
                        lambda image: {"status": "READ", "number": 12})
    observer = _SyntheticAutoObserver()
    result = _run(samples, tmp_path, observer)
    assert [path.name for path in observer.called] == [
        sample["frame_path"].name for sample in samples]
    assert [receipt["decoded_frame_size"] for receipt in result["detector_receipts"]] == [
        sample["raw_frame_size"] for sample in samples]
    assert [receipt["raw_frame_size"] for receipt in result["detector_receipts"]] == [
        sample["raw_frame_size"] for sample in samples]


def test_real_shadow_rejects_observer_geometry_before_pixel_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    _bind_synthetic_sprite(monkeypatch, tmp_path)
    samples = _real_png_samples(tmp_path)
    monkeypatch.setattr(adapter, "GamblerPartialPixelObserver", _SyntheticAutoObserver)
    observer = _SyntheticAutoObserver()
    observer.geometry = type("WrongGeometry", (),
                             {"auto_discover": True, "width": 1, "height": 1})()
    with pytest.raises(PartialEvidenceError, match="OBSERVER_FRAME_SIZE_MISMATCH"):
        _run(samples, tmp_path, observer)
    assert observer.called == []
