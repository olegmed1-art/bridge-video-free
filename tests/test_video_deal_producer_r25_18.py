import hashlib
import json
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from bridge_vision.deal_evidence import apply_deal_evidence_bundle
from bridge_vision.profiled_challenger import build_teach_profile, parse_profile
from bridge_vision.template_pixel_producer import (
    LoadedTemplatePixelProducer,
    TemplatePixelProducerError,
    load_template_pixel_bundle,
)
from bridge_vision.video_deal_producer import (
    dense_frame_timestamps,
    produce_deal_evidence_bundle,
)


SOURCE = {
    "driveId": "drive-source-universal",
    "sha256": "a" * 64,
    "sizeBytes": 123456,
}


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _png(seed: int, width: int = 12, height: int = 16) -> bytes:
    image = np.full((height, width), 255, dtype=np.uint8)
    x = 1 + seed % max(1, width - 3)
    y = 1 + (seed // max(1, width - 3)) % max(1, height - 3)
    cv2.line(image, (x, 1), (x, height - 2), 0, 1)
    cv2.line(image, (1, y), (width - 2, y), 0, 1)
    cv2.circle(image, (1 + seed % (width - 2), 1 + (seed * 3) % (height - 2)), 1, 0, -1)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return bytes(encoded)


def _hashes(labels, offset=0):
    return {label: _sha(_png(index + offset)) for index, label in enumerate(labels)}


def _profile_raw(*, reference_sha=None, ranks=None, suits=None, cards=None):
    reference_sha = reference_sha or "f" * 64
    ranks = ranks or _hashes("AKQJT98765432", 10)
    suits = suits or _hashes("SHDC", 100)
    cards = cards or _hashes([rank + suit for rank in "AKQJT98765432" for suit in "SHDC"], 200)
    return build_teach_profile(
        profile_id="universal-test-profile",
        reference_frame_sha256=reference_sha,
        reference_size={"width": 100, "height": 100},
        table_region={"x": 0, "y": 0, "w": 100, "h": 100},
        rank_templates=ranks,
        suit_templates=suits,
        card_templates=cards,
        rank_suit_channel_id="glyph-template-family-v1",
        reference_channel_id="full-card-template-v1",
        human_verified=True,
        verification={
            "method": "HUMAN_LABEL_REVIEW",
            "reviewer_id": "reviewer-1",
            "verified_at": "2026-08-29T18:00:00Z",
            "reference_frame_sha256": reference_sha,
        },
        ordering_prior={
            "human_verified": True,
            "suit_order": list("HCDS"),
            "rank_order": list("AKQJT98765432"),
            "seat_axes": {"N": "X_ASC", "E": "Y_ASC", "S": "X_ASC", "W": "Y_ASC"},
            "seat_positions": {"top": "N", "right": "E", "bottom": "S", "left": "W"},
        },
        gates={
            "min_registration_inliers": 4,
            "min_registration_inlier_ratio": 0.90,
            "min_deal_match_inliers": 4,
            "min_deal_match_inlier_ratio": 0.90,
            "min_rank_confidence": 0.90,
            "min_suit_confidence": 0.90,
            "min_reference_confidence": 0.90,
            "min_card_confidence": 0.90,
            "min_ambiguous_candidate_confidence": 0.90,
            "min_temporal_observations": 2,
            "seat_dead_zone": 0.08,
        },
    )


def _asset(member, value):
    return {"member": member, "sha256": _sha(value)}


def test_template_bundle_binds_every_verified_asset(tmp_path: Path):
    reference = _png(900, 100, 100)
    rank_bytes = {label: _png(index + 10) for index, label in enumerate("AKQJT98765432")}
    suit_bytes = {label: _png(index + 100) for index, label in enumerate("SHDC")}
    card_bytes = {
        label: _png(index + 200)
        for index, label in enumerate(rank + suit for rank in "AKQJT98765432" for suit in "SHDC")
    }
    profile = _profile_raw(
        reference_sha=_sha(reference),
        ranks={key: _sha(value) for key, value in rank_bytes.items()},
        suits={key: _sha(value) for key, value in suit_bytes.items()},
        cards={key: _sha(value) for key, value in card_bytes.items()},
    )
    parsed = parse_profile(profile)
    files = {"reference.png": reference}
    files.update({f"rank/{key}.png": value for key, value in rank_bytes.items()})
    files.update({f"suit/{key}.png": value for key, value in suit_bytes.items()})
    files.update({f"card/{key}.png": value for key, value in card_bytes.items()})
    compass = {key: _png(index + 600) for index, key in enumerate("NESW")}
    digits = {key: _png(index + 700) for index, key in enumerate("0123456789")}
    dealer = _png(800)
    files.update({f"compass/{key}.png": value for key, value in compass.items()})
    files.update({f"digit/{key}.png": value for key, value in digits.items()})
    files["dealer.png"] = dealer
    assets = {
        "reference_frame": _asset("reference.png", reference),
        "rank_templates": {key: _asset(f"rank/{key}.png", value) for key, value in rank_bytes.items()},
        "suit_templates": {key: _asset(f"suit/{key}.png", value) for key, value in suit_bytes.items()},
        "card_templates": {key: _asset(f"card/{key}.png", value) for key, value in card_bytes.items()},
        "compass_label_templates": {key: _asset(f"compass/{key}.png", value) for key, value in compass.items()},
        "dealer_marker_template": _asset("dealer.png", dealer),
        "board_digit_templates": {key: _asset(f"digit/{key}.png", value) for key, value in digits.items()},
    }
    manifest = {
        "schema": "bridge-template-pixel-producer-manifest/v1",
        "revision": "synthetic-profile-r1",
        "human_verified_assets": True,
        "profile_id": parsed.profile_id,
        "profile_verification_sha256": parsed.verification_sha256,
        "template_set_sha256": parsed.template_set_sha256,
        "frame_interval_seconds": 3,
        "assets": assets,
        "asset_verification": {
            "method": "HUMAN_LABEL_REVIEW",
            "reviewer_id": "reviewer-1",
            "verified_at": "2026-08-29T18:00:00Z",
            "reference_frame_sha256": _sha(reference),
            "assets_sha256": _sha(json.dumps(assets, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()),
        },
        "registration": {"max_features": 1000, "knn_ratio": 0.75, "ransac_reprojection_px": 3},
        "card_matching": {
            "rank_crop": {"x": 0, "y": 0, "w": 0.5, "h": 0.6},
            "suit_crop": {"x": 0, "y": 0.4, "w": 0.5, "h": 0.6},
            "full_card_threshold": 0.90,
            "glyph_threshold": 0.90,
            "min_margin": 0.02,
            "max_candidates": 52,
        },
        "compass": {
            "interface": "BRIDGIT",
            "scope": "synthetic",
            "region": {"x": 60, "y": 0, "w": 40, "h": 40},
            "seat_rois": {key: {"x": 60 + i * 2, "y": i * 2, "w": 10, "h": 10} for i, key in enumerate(("top", "right", "bottom", "left"))},
            "board_number_roi": {"x": 70, "y": 15, "w": 10, "h": 10},
            "dealer_marker_rois": {key: {"x": 80 + i * 2, "y": i * 2, "w": 10, "h": 10} for i, key in enumerate(("top", "right", "bottom", "left"))},
            "match_threshold": 0.90,
        },
    }
    files["profile.json"] = json.dumps(profile, sort_keys=True).encode()
    files["manifest.json"] = json.dumps(manifest, sort_keys=True).encode()
    bundle = tmp_path / "profile.zip"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    loaded = load_template_pixel_bundle(bundle)
    assert loaded.profile.profile_id == "universal-test-profile"
    assert loaded.backend_sha256 == hashlib.sha256(bundle.read_bytes()).hexdigest()
    assert loaded.frame_interval_seconds == 3

    traversal = tmp_path / "traversal.zip"
    with zipfile.ZipFile(traversal, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
        archive.writestr("../escape.png", b"not allowed")
    with pytest.raises(TemplatePixelProducerError, match="unsafe bundle member"):
        load_template_pixel_bundle(traversal)

    duplicate = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(duplicate, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("profile.json", files["profile.json"])
    with pytest.raises(TemplatePixelProducerError, match="repeats a member"):
        load_template_pixel_bundle(duplicate)

    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(tampered, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, b"tampered" if name == "rank/A.png" else value)
    with pytest.raises(TemplatePixelProducerError, match="asset hash mismatch"):
        load_template_pixel_bundle(tampered)


def _encoded(image):
    ok, value = cv2.imencode(".png", image)
    assert ok
    return bytes(value)


def _glyph(value: str, *, width=10, height=12):
    image = np.full((height, width), 255, dtype=np.uint8)
    cv2.putText(image, value, (0, height - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.36, 0, 1, cv2.LINE_AA)
    return image


def test_real_template_recognizer_reads_pixels_compass_and_card(tmp_path: Path):
    ranks_img = {key: _glyph(key) for key in "AKQJT98765432"}
    suits_img = {key: _glyph(key) for key in "SHDC"}
    cards_img = {}
    labels = [rank + suit for rank in "AKQJT98765432" for suit in "SHDC"]
    rng = np.random.default_rng(3118)
    for index, card in enumerate(labels):
        image = np.full((24, 20), 255, dtype=np.uint8)
        image[:12, :10] = ranks_img[card[0]]
        image[12:, :10] = suits_img[card[1]]
        code = (rng.integers(0, 2, size=(12, 8), dtype=np.uint8) * 255)
        image[6:18, 11:19] = code
        image[index % 24, 19] = 0
        cards_img[card] = image

    compass_img = {seat: _glyph(seat) for seat in "NESW"}
    digit_img = {digit: _glyph(digit) for digit in "0123456789"}
    dealer_img = _glyph("D")
    reference = np.full((150, 200), 255, dtype=np.uint8)
    reference[2:26, 70:90] = cards_img["AS"]
    seat_rois = {
        "top": {"x": 170, "y": 0, "w": 10, "h": 12},
        "right": {"x": 188, "y": 20, "w": 10, "h": 12},
        "bottom": {"x": 170, "y": 42, "w": 10, "h": 12},
        "left": {"x": 150, "y": 20, "w": 10, "h": 12},
    }
    for position, seat in zip(("top", "right", "bottom", "left"), "NESW"):
        roi = seat_rois[position]
        reference[int(roi["y"]):int(roi["y"]+roi["h"]), int(roi["x"]):int(roi["x"]+roi["w"])] = compass_img[seat]
    board_roi = {"x": 170, "y": 22, "w": 10, "h": 12}
    reference[22:34, 170:180] = digit_img["1"]
    dealer_rois = {
        "top": {"x": 188, "y": 0, "w": 10, "h": 12},
        "right": {"x": 188, "y": 40, "w": 10, "h": 12},
        "bottom": {"x": 150, "y": 42, "w": 10, "h": 12},
        "left": {"x": 150, "y": 0, "w": 10, "h": 12},
    }
    reference[0:12, 188:198] = dealer_img

    reference_bytes = _encoded(reference)
    rank_bytes = {key: _encoded(value) for key, value in ranks_img.items()}
    suit_bytes = {key: _encoded(value) for key, value in suits_img.items()}
    card_bytes = {key: _encoded(value) for key, value in cards_img.items()}
    compass_bytes = {key: _encoded(value) for key, value in compass_img.items()}
    digit_bytes = {key: _encoded(value) for key, value in digit_img.items()}
    dealer_bytes = _encoded(dealer_img)
    profile = build_teach_profile(
        profile_id="synthetic-real-pixel-profile",
        reference_frame_sha256=_sha(reference_bytes),
        reference_size={"width": 200, "height": 150},
        table_region={"x": 0, "y": 0, "w": 150, "h": 150},
        rank_templates={key: _sha(value) for key, value in rank_bytes.items()},
        suit_templates={key: _sha(value) for key, value in suit_bytes.items()},
        card_templates={key: _sha(value) for key, value in card_bytes.items()},
        rank_suit_channel_id="separate-glyph-templates-v1",
        reference_channel_id="full-card-corner-template-v1",
        human_verified=True,
        verification={"method":"HUMAN_LABEL_REVIEW","reviewer_id":"reviewer-2","verified_at":"2026-08-29T18:00:00Z","reference_frame_sha256":_sha(reference_bytes)},
        ordering_prior={"human_verified":True,"suit_order":list("HCDS"),"rank_order":list("AKQJT98765432"),"seat_axes":{"N":"X_ASC","E":"Y_ASC","S":"X_ASC","W":"Y_ASC"},"seat_positions":{"top":"N","right":"E","bottom":"S","left":"W"}},
        gates={"min_registration_inliers":4,"min_registration_inlier_ratio":0.90,"min_deal_match_inliers":4,"min_deal_match_inlier_ratio":0.90,"min_rank_confidence":0.90,"min_suit_confidence":0.90,"min_reference_confidence":0.90,"min_card_confidence":0.90,"min_ambiguous_candidate_confidence":0.90,"min_temporal_observations":2,"seat_dead_zone":0.08},
    )
    parsed = parse_profile(profile)
    files = {"reference.png": reference_bytes, "dealer.png": dealer_bytes}
    files.update({f"rank/{key}.png": value for key, value in rank_bytes.items()})
    files.update({f"suit/{key}.png": value for key, value in suit_bytes.items()})
    files.update({f"card/{key}.png": value for key, value in card_bytes.items()})
    files.update({f"compass/{key}.png": value for key, value in compass_bytes.items()})
    files.update({f"digit/{key}.png": value for key, value in digit_bytes.items()})
    manifest = {
        "schema":"bridge-template-pixel-producer-manifest/v1","revision":"synthetic-real-r1","human_verified_assets":True,
        "profile_id":parsed.profile_id,"profile_verification_sha256":parsed.verification_sha256,"template_set_sha256":parsed.template_set_sha256,"frame_interval_seconds":3,
        "assets":{
            "reference_frame":_asset("reference.png",reference_bytes),
            "rank_templates":{key:_asset(f"rank/{key}.png",value) for key,value in rank_bytes.items()},
            "suit_templates":{key:_asset(f"suit/{key}.png",value) for key,value in suit_bytes.items()},
            "card_templates":{key:_asset(f"card/{key}.png",value) for key,value in card_bytes.items()},
            "compass_label_templates":{key:_asset(f"compass/{key}.png",value) for key,value in compass_bytes.items()},
            "dealer_marker_template":_asset("dealer.png",dealer_bytes),
            "board_digit_templates":{key:_asset(f"digit/{key}.png",value) for key,value in digit_bytes.items()},
        },
        "registration":{"max_features":1000,"knn_ratio":0.75,"ransac_reprojection_px":3},
        "card_matching":{"search_region":{"x":0,"y":0,"w":150,"h":150},"rank_crop":{"x":0,"y":0,"w":0.5,"h":0.5},"suit_crop":{"x":0,"y":0.5,"w":0.5,"h":0.5},"full_card_threshold":0.90,"glyph_threshold":0.90,"min_margin":0.01,"max_candidates":52},
        "compass":{"interface":"BRIDGIT","scope":"synthetic-real","region":{"x":150,"y":0,"w":50,"h":60},"seat_rois":seat_rois,"board_number_roi":board_roi,"dealer_marker_rois":dealer_rois,"match_threshold":0.90},
    }
    manifest["asset_verification"] = {
        "method":"HUMAN_LABEL_REVIEW","reviewer_id":"reviewer-2","verified_at":"2026-08-29T18:00:00Z",
        "reference_frame_sha256":_sha(reference_bytes),
        "assets_sha256":_sha(json.dumps(manifest["assets"],ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()),
    }
    files["profile.json"] = json.dumps(profile, sort_keys=True).encode()
    files["manifest.json"] = json.dumps(manifest, sort_keys=True).encode()
    bundle = tmp_path / "real-profile.zip"
    with zipfile.ZipFile(bundle,"w",compression=zipfile.ZIP_DEFLATED) as archive:
        for name,value in files.items(): archive.writestr(name,value)
    loaded = load_template_pixel_bundle(bundle)
    frame = tmp_path / "reference.png"; frame.write_bytes(reference_bytes)
    matches = loaded.recognizer._card_matches(reference)
    assert any(item["card"] == "AS" for item in matches), matches
    raw = loaded.recognizer(frame, loaded.profile.recognizer_view())
    assert raw["deal_identity"]["value"] == "board-1"
    assert raw["board_metadata"]["dealer"]["value"] == "N"
    assert any(item["reference_match"]["card"] == "AS" for item in raw["cards"]), raw["cards"]
    card = next(item for item in raw["cards"] if item["reference_match"]["card"] == "AS")
    assert card["rank"]["value"] == "A"
    assert card["suit"]["value"] == "S"


class _FakeRecognizer:
    def __call__(self, frame: Path, profile):
        digest = hashlib.sha256(frame.read_bytes()).hexdigest()
        return {
            "frame_sha256": digest,
            "registration": {
                "reference_frame_sha256": profile["reference_frame_sha256"],
                "homography": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                "inliers": 20,
                "inlier_ratio": 0.99,
            },
            "deal_identity": {"kind": "EXPLICIT_BOARD", "scope": "universal-test", "value": "board-1"},
            "board_metadata": {
                "board_number": {"value": 1, "confidence": 0.99, "source": "VISUAL_TEXT", "evidence_locator": "compass#board"},
                "dealer": {"value": "N", "confidence": 0.99, "source": "VISUAL_TEXT", "evidence_locator": "compass#dealer"},
            },
            "cards": [{
                "box": {"x": 45, "y": 2, "w": 5, "h": 8},
                "rank": {"value": "A", "confidence": 0.99, "channel_id": "glyph-template-family-v1"},
                "suit": {"value": "S", "confidence": 0.98, "channel_id": "glyph-template-family-v1"},
                "reference_match": {"card": "AS", "confidence": 0.97, "channel_id": "full-card-template-v1"},
            }],
        }


def test_end_to_end_records_become_stable_source_bound_evidence(tmp_path: Path):
    profile = parse_profile(_profile_raw())
    loaded = LoadedTemplatePixelProducer(
        profile=profile,
        profile_sha256="1" * 64,
        backend_sha256="2" * 64,
        config_sha256="3" * 64,
        frame_interval_seconds=3,
        recognizer=_FakeRecognizer(),
    )
    shots = []
    for index, content in enumerate((b"frame-one", b"frame-two")):
        path = (tmp_path / f"frame-{index}.jpg").resolve()
        path.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        shots.append({
            "evidence_id": f"frame-{index}", "time": float(index),
            "path": str(path), "sha256": digest,
        })
    bundle, report = produce_deal_evidence_bundle(
        source=SOURCE,
        shots=shots,
        loaded=loaded,
        frame_plan={"timeline_complete": True, "expected_frame_count": 2, "extracted_frame_count": 2},
    )
    stable = apply_deal_evidence_bundle(bundle, source=SOURCE, shots=shots)
    assert report["emitted_machine_card_count"] == 1
    assert stable["summary"]["machine_card_count"] == 1
    assert stable["deals"][0]["hands"]["N"] == ["AS"]
    observation = stable["deals"][0]["card_observations"][0]
    assert set(observation["channels"]) == {"rank", "suit", "reference"}
    assert len(set(observation["channels"].values())) == 3
    assert len(observation["frames"]) == 2
    assert stable["result_scope"] == "SHADOW_ONLY"
    assert stable["production_activation_allowed"] is False


def test_dense_plan_for_6950_seconds_has_2318_frames():
    points = dense_frame_timestamps(6950, 3)
    assert len(points) == 2318
    assert points[0] == 0
    assert points[-2] == 6948
    assert points[-1] == 6950
