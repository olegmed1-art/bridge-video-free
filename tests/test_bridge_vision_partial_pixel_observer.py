"""Synthetic raster tests for the actual deterministic partial pixel observer."""
from hashlib import sha256
import os
from pathlib import Path

import pytest

import bridge_vision.bridgit_partial_gambler_adapter as adapter
from bridge_vision.bridgit_adjacent_candidate_profile import PROFILE
from bridge_vision.bridgit_partial_evidence import SamplingContract
from bridge_vision.bridgit_partial_pixel_observer import (
    CardCorner, GamblerPartialPixelObserver, PartialPixelGeometry,
    PartialPixelObservationError,
)
from bridge_vision.gambler_classic_reference import (
    CARDS, RANKS, SUITS, GamblerClassicSprite, card_box, decode_card_cells,
)
from bridge_vision.gambler_reference_authority import pinned_sprite_sha256


def _synthetic_sprite() -> GamblerClassicSprite:
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    image = np.full((588, 1417, 4), 255, dtype=np.uint8)
    for suit in SUITS:
        for rank in RANKS:
            x0, y0, x1, y1 = card_box(5, rank + suit)
            cell = image[y0:y1, x0:x1]
            cv2.rectangle(cell, (0, 0), (108, 146), (0, 0, 0, 255), 1)
            cv2.putText(cell, rank, (4, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                        (0, 0, 0, 255), 2)
            cv2.putText(cell, suit, (4, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                        (0, 0, 0, 255), 2)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    payload = encoded.tobytes()
    return GamblerClassicSprite(5, sha256(payload).hexdigest(), 1417, 588, 109, 147, payload)


def _geometry() -> PartialPixelGeometry:
    return PartialPixelGeometry(600, 400, 109, 147, (0, 0, 600, 400),
                                (CardCorner(205, 0, "N"), CardCorner(350, 0, "N"),
                                 CardCorner(205, 250, "S")))


def _frame(tmp_path: Path, sprite: GamblerClassicSprite) -> Path:
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    frame = np.full((400, 600, 3), (0, 110, 0), dtype=np.uint8)
    cells = decode_card_cells(sprite)
    frame[0:147, 205:314] = cv2.cvtColor(cells["AH"], cv2.COLOR_BGRA2BGR)
    frame[0:147, 350:459] = 128  # Covered corner remains unknown.
    frame[250:397, 205:314] = cv2.cvtColor(cells["KS"], cv2.COLOR_BGRA2BGR)
    path = tmp_path / "partial.png"
    assert cv2.imwrite(str(path), frame)
    return path


def test_native_size_sprite_recognizes_only_visible_synthetic_corners(tmp_path: Path):
    sprite = _synthetic_sprite()
    result = GamblerPartialPixelObserver(_geometry())(_frame(tmp_path, sprite), sprite)
    assert [item["card"] for item in result["cards"]] == ["AH", "KS"]
    assert result["unknown"] == [{"index": 1, "reason": "CORNER_OCCLUDED", "seat": "N"}]
    assert all(item["confidence"] >= 0.78 for item in result["cards"])


def test_blank_visible_corner_stays_unknown_instead_of_filling_from_deck(tmp_path: Path):
    cv2 = pytest.importorskip("cv2")
    sprite = _synthetic_sprite()
    path = _frame(tmp_path, sprite)
    image = cv2.imread(str(path))
    image[0:147, 350:459] = 255
    assert cv2.imwrite(str(path), image)
    result = GamblerPartialPixelObserver(_geometry())(path, sprite)
    assert [item["card"] for item in result["cards"]] == ["AH", "KS"]
    assert result["unknown"][0]["reason"] == "CARD_UNCERTAIN"


def test_wrong_frame_geometry_and_card_scale_fail_closed(tmp_path: Path):
    sprite = _synthetic_sprite()
    path = _frame(tmp_path, sprite)
    wrong_scale = PartialPixelGeometry(600, 400, 100, 147, (0, 0, 600, 400),
                                       (CardCorner(205, 0, "N"),))
    with pytest.raises(PartialPixelObservationError, match="CARD_SCALE_UNSUPPORTED"):
        GamblerPartialPixelObserver(wrong_scale)(path, sprite)
    wrong_frame = PartialPixelGeometry(601, 400, 109, 147, (0, 0, 601, 400),
                                       (CardCorner(205, 0, "N"),))
    with pytest.raises(PartialPixelObservationError, match="FRAME_GEOMETRY_MISMATCH"):
        GamblerPartialPixelObserver(wrong_frame)(path, sprite)


def test_declared_seat_must_match_explicit_table_geometry():
    wrong = PartialPixelGeometry(600, 400, 109, 147, (0, 0, 600, 400),
                                  (CardCorner(205, 0, "S"),))
    with pytest.raises(PartialPixelObservationError, match="CARD_SEAT_GEOMETRY_MISMATCH"):
        GamblerPartialPixelObserver(wrong)


def test_automatic_corner_discovery_uses_bounded_seat_bands(tmp_path: Path):
    sprite = _synthetic_sprite()
    geometry = PartialPixelGeometry(600, 400, 109, 147, (0, 0, 600, 400),
                                    (), auto_discover=True)
    result = GamblerPartialPixelObserver(geometry)(_frame(tmp_path, sprite), sprite)
    assert result["corner_detection"]["mode"] == "PINNED_BORDER_SEAT_BANDS"
    assert {item["card"] for item in result["cards"]} == {"AH", "KS"}
    assert all(item["confidence"] >= .78 for item in result["cards"])


def test_auto_discovery_returns_unknown_when_no_card_corner_exists(tmp_path: Path):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    sprite = _synthetic_sprite()
    path = tmp_path / "empty-table.png"
    assert cv2.imwrite(str(path), np.full((400, 600, 3), (0, 110, 0), dtype=np.uint8))
    geometry = PartialPixelGeometry(600, 400, 109, 147, (0, 0, 600, 400),
                                    (), auto_discover=True)
    result = GamblerPartialPixelObserver(geometry)(path, sprite)
    assert result["cards"] == []
    assert result["unknown"] == [{"index": 0, "reason": "NO_CORNERS_FOUND"}]


def test_duplicate_template_identity_stays_unknown(tmp_path: Path):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    sprite = _synthetic_sprite()
    image = cv2.imdecode(np.frombuffer(sprite.payload, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    ax0, ay0, ax1, ay1 = card_box(5, "AH")
    sx0, sy0, sx1, sy1 = card_box(5, "AS")
    image[sy0:sy1, sx0:sx1] = image[ay0:ay1, ax0:ax1]
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    payload = encoded.tobytes()
    duplicate = GamblerClassicSprite(5, sha256(payload).hexdigest(), 1417, 588,
                                     109, 147, payload)
    result = GamblerPartialPixelObserver(_geometry())(_frame(tmp_path, duplicate), duplicate)
    assert "AH" not in [item["card"] for item in result["cards"]]
    assert result["unknown"][0]["reason"] == "CARD_UNCERTAIN"


def test_pixel_observer_to_accumulator_preserves_unknown_and_deal_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    cv2 = pytest.importorskip("cv2")
    sprite = _synthetic_sprite()
    first = _frame(tmp_path, sprite)
    second = tmp_path / "next-occurrence.png"
    image = cv2.imread(str(first))
    image[0, 0] = (0, 111, 0)
    assert cv2.imwrite(str(second), image)
    monkeypatch.setattr(adapter, "resolve_original_gambler_asset",
                        lambda root, **kwargs: (5, tmp_path / "synthetic-sprite.png",
                                                sprite.sprite_sha256))
    monkeypatch.setattr(adapter, "_load_shadow_sprite", lambda path, **kwargs: sprite)
    samples = []
    for index, path in enumerate((first, second)):
        read = {"status": "READ", "number": 12}
        samples.append({
            "frame_path": path, "frame_sha256": sha256(path.read_bytes()).hexdigest(),
            "source_video_sha256": "a" * 64, "episode_id": "synthetic-episode",
            "board_epoch": index + 1, "board_number": 12,
            "board_number_read": dict(read), "board_number_confirming_read": dict(read),
            "timestamp_ms": 1000 + 3000 * index, "observation_index": index,
            "raw_frame_size": {"width": 600, "height": 400},
            "data_class": "SYNTHETIC_REGRESSION",
        })
    result = adapter.accumulate_gambler_partial_frames(
        samples, asset_root=tmp_path, verified_card_width_px=109,
        verified_card_height_px=147, pixel_observer=GamblerPartialPixelObserver(_geometry()),
        contract=SamplingContract(PROFILE.version, 1000, 3000, PROFILE.max_gap_ms, 0.80),
    )
    assert len(result["tracks"]) == 2
    assert [track["identity"]["board_epoch"] for track in result["tracks"]] == [1, 2]
    for track, sample in zip(result["tracks"], samples):
        assert track["status"] == "REVIEW_SINGLE_FRAME"
        assert track["frame_sha256s"] == [sample["frame_sha256"]]
        assert track["deal"]["hands"]["N"]["cards"] == ["AH"]
        assert track["deal"]["hands"]["S"]["cards"] == ["KS"]
        assert any(item["reason"] == "CORNER_OCCLUDED"
                   and item["frame_sha256"] == sample["frame_sha256"]
                   for item in track["review_observations"])
        assert track["canonical_promotion_allowed"] is False


@pytest.mark.parametrize("variant", range(1, 9))
def test_all_52_original_cards_and_unknown_controls_at_each_pinned_size(
    tmp_path: Path, variant: int,
):
    """Run with BRIDGIT_PINNED_GAMBLER_ASSET_ROOT; skip when assets are absent."""
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    root = os.environ.get("BRIDGIT_PINNED_GAMBLER_ASSET_ROOT")
    if not root:
        pytest.skip("verified local Gambler assets not supplied")
    sprite = adapter._load_shadow_sprite(Path(root) / f"all-v{variant}.png",
                         expected_sha256=pinned_sprite_sha256(variant), expected_variant=variant)
    cells = decode_card_cells(sprite)
    cw, ch = sprite.card_width, sprite.card_height
    geometry = PartialPixelGeometry(3 * cw, 3 * ch, cw, ch, (0, 0, 3 * cw, 3 * ch),
                                    (CardCorner(cw, 0, "N"),))
    observer = GamblerPartialPixelObserver(geometry)
    path = tmp_path / "native-cell.png"
    failures = []
    for card in CARDS:
        image = np.full((3 * ch, 3 * cw, 3), (0, 110, 0), dtype=np.uint8)
        cell = cells[card]
        unit = float(np.iinfo(cell.dtype).max)
        if cell.shape[2] == 4:
            alpha = cell[:, :, 3:4].astype(np.float32) / unit
            colour = cell[:, :, :3].astype(np.float32) / unit
            cell = np.rint(255.0 * (colour * alpha + 1.0 - alpha)).astype(np.uint8)
        else:
            cell = np.rint(cell.astype(np.float32) * 255.0 / unit).astype(np.uint8)
        image[:ch, cw:2 * cw] = cell
        assert cv2.imwrite(str(path), image)
        observed = observer(path, sprite)
        found = observed["cards"][0]["card"] if observed["cards"] else observed["unknown"][0]["reason"]
        if found != card:
            failures.append((card, found))
    assert not failures, f"variant {variant}: {failures}"

    # Controls use an actual native cell; no rank/suit can be inferred from
    # a covered or blank corner, and a clipped ROI cannot be read at all.
    image[:ch, cw:cw + max(1, round(cw * 0.15))] = 128
    assert cv2.imwrite(str(path), image)
    assert observer(path, sprite)["unknown"][0]["reason"] == "CORNER_OCCLUDED"
    image[:ch, cw:2 * cw] = 255
    assert cv2.imwrite(str(path), image)
    assert observer(path, sprite)["unknown"][0]["reason"] == "CARD_UNCERTAIN"
    incomplete = GamblerPartialPixelObserver(
        PartialPixelGeometry(3 * cw, 3 * ch, cw, ch, (0, 0, 3 * cw, 3 * ch),
                             (CardCorner(3 * cw - 3, 0, "E"),)))
    assert incomplete(path, sprite)["unknown"][0]["reason"] == "CORNER_INCOMPLETE"

    automatic = GamblerPartialPixelObserver(
        PartialPixelGeometry(3 * cw, 3 * ch, cw, ch, (0, 0, 3 * cw, 3 * ch),
                             (), auto_discover=True))
    for card in ("8H", "KS"):
        image = np.full((3 * ch, 3 * cw, 3), (0, 110, 0), dtype=np.uint8)
        cell = cells[card]
        unit = float(np.iinfo(cell.dtype).max)
        if cell.shape[2] == 4:
            alpha = cell[:, :, 3:4].astype(np.float32) / unit
            colour = cell[:, :, :3].astype(np.float32) / unit
            cell = np.rint(255.0 * (colour * alpha + 1.0 - alpha)).astype(np.uint8)
        else:
            cell = np.rint(cell.astype(np.float32) * 255.0 / unit).astype(np.uint8)
        image[:ch, cw:2 * cw] = cell
        assert cv2.imwrite(str(path), image)
        assert [item["card"] for item in automatic(path, sprite)["cards"]] == [card]


@pytest.mark.parametrize("change", ("shift_1px", "jpeg_q95", "scale_102pct"))
def test_auto_geometry_on_independently_transformed_pinned_cells(
    tmp_path: Path, change: str,
):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    root = os.environ.get("BRIDGIT_PINNED_GAMBLER_ASSET_ROOT")
    if not root:
        pytest.skip("verified local Gambler assets not supplied")
    sprite = adapter._load_shadow_sprite(Path(root) / "all-v5.png",
                         expected_sha256=pinned_sprite_sha256(5), expected_variant=5)
    cells = decode_card_cells(sprite)
    cw, ch = sprite.card_width, sprite.card_height
    observer = GamblerPartialPixelObserver(
        PartialPixelGeometry(3 * cw, 3 * ch, cw, ch, (0, 0, 3 * cw, 3 * ch),
                             (), auto_discover=True))
    cases = ("8H", "KS", "TC", "AD", "JD", "5C", "TH", "4S")
    correct = unknown = 0
    for identity in cases:
        image = np.full((3 * ch, 3 * cw, 3), (0, 110, 0), dtype=np.uint8)
        cell = cells[identity]
        unit = float(np.iinfo(cell.dtype).max)
        if cell.shape[2] == 4:
            alpha = cell[:, :, 3:4].astype(np.float32) / unit
            colour = cell[:, :, :3].astype(np.float32) / unit
            cell = np.rint(255.0 * (colour * alpha + 1.0 - alpha)).astype(np.uint8)
        else:
            cell = np.rint(cell.astype(np.float32) * 255.0 / unit).astype(np.uint8)
        x, y = cw, 0
        if change == "shift_1px":
            x, y = x + 1, y + 1
        elif change == "scale_102pct":
            cell = cv2.resize(cell, (round(cw * 1.02), round(ch * 1.02)),
                              interpolation=cv2.INTER_LINEAR)
        height, width = cell.shape[:2]
        image[y:y + height, x:x + width] = cell
        path = tmp_path / ("transformed.jpg" if change == "jpeg_q95" else "transformed.png")
        if change == "jpeg_q95":
            assert cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, 95])
        else:
            assert cv2.imwrite(str(path), image)
        result = observer(path, sprite)
        values = [item["card"] for item in result["cards"]]
        assert not values or values == [identity], (change, identity, values)
        correct += bool(values)
        unknown += not values
    assert correct + unknown == len(cases)
    if change == "shift_1px":
        assert correct == len(cases)


def test_auto_geometry_single_frame_transform_stability_without_owner_labels(tmp_path: Path):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    root = os.environ.get("BRIDGIT_PINNED_GAMBLER_ASSET_ROOT")
    source_name = os.environ.get("BRIDGIT_SINGLE_FRAME_SMOKE")
    if not root or not source_name:
        pytest.skip("local pinned assets and single frame not supplied")
    source = Path(source_name)
    assert sha256(source.read_bytes()).hexdigest() == (
        "b7a960a7bfcf3e3cbc4dc191988baa2d31ec740d72fb58e2a9c61f77ef103c13")
    sprite = adapter._load_shadow_sprite(Path(root) / "all-v5.png",
                         expected_sha256=pinned_sprite_sha256(5), expected_variant=5)
    geometry = PartialPixelGeometry(1680, 934, 109, 147, (0, 0, 1332, 934),
                                    (), auto_discover=True)
    observer = GamblerPartialPixelObserver(geometry)
    baseline = observer(source, sprite)["cards"]
    assert baseline
    original = cv2.imread(str(source))
    changed = {
        "shift": cv2.warpAffine(original, np.float32([[1, 0, 1], [0, 1, 1]]),
                                 (1680, 934), borderValue=(0, 110, 0)),
        "jpeg": original,
    }
    for mode, image in changed.items():
        path = tmp_path / ("shift.png" if mode == "shift" else "jpeg.jpg")
        options = [cv2.IMWRITE_JPEG_QUALITY, 95] if mode == "jpeg" else []
        assert cv2.imwrite(str(path), image, options)
        for item in observer(path, sprite)["cards"]:
            dx = dy = 1 if mode == "shift" else 0
            near = [base for base in baseline
                    if abs(base["box"]["x"] + dx - item["box"]["x"]) <= 5
                    and abs(base["box"]["y"] + dy - item["box"]["y"]) <= 5]
            assert near and item["card"] == near[0]["card"]
    scaled = cv2.resize(original, (1714, 953), interpolation=cv2.INTER_LINEAR)
    scaled_path = tmp_path / "scale-102.png"
    assert cv2.imwrite(str(scaled_path), scaled)
    measured_scale = PartialPixelGeometry(1714, 953, 111, 150,
                                          (0, 0, 1359, 953), (), auto_discover=True)
    with pytest.raises(PartialPixelObservationError, match="CARD_SCALE_UNSUPPORTED"):
        GamblerPartialPixelObserver(measured_scale)(scaled_path, sprite)
