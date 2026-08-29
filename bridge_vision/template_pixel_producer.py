"""Profile-driven OpenCV producer for universal bridge-video card evidence.

The implementation is deliberately interface-profiled rather than tied to a
person, video, board or seat.  A ZIP bundle contains one human-verified
``profile.json``, one bounded ``manifest.json`` and the exact reference/template
pixels named by their SHA-256 digests.  No automatically harvested label is
trusted as a template.

Frames are registered to the verified reference before matching.  Card rank,
suit and the full corner are classified independently.  Compass metadata is
read from verified ROIs.  Optional auction cells require agreement between a
full-cell template and Tesseract OCR before they reach the auction observer.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import stat
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from bridge_contracts.video_auction import validate_auction_prefix
from bridge_vision.bridgit_compass import guard_recognizer_result
from bridge_vision.profiled_challenger import (
    CARDS,
    RANKS,
    SUITS,
    InterfaceProfile,
    ProfiledChallengerError,
    parse_profile,
)

BUNDLE_SCHEMA = "bridge-template-pixel-producer-bundle/v1"
MANIFEST_SCHEMA = "bridge-template-pixel-producer-manifest/v1"
PRODUCER_REVISION = "template-pixel-producer-r1"
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_MEMBERS = 256
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_CARD_CANDIDATES = 104
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
_POSITIONS = ("top", "right", "bottom", "left")
_SEATS = ("N", "E", "S", "W")


class TemplatePixelProducerError(RuntimeError):
    pass


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_json(value: bytes, name: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise TemplatePixelProducerError(f"{name} contains duplicate JSON keys")
            result[key] = item
        return result

    try:
        raw = json.loads(value.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise TemplatePixelProducerError(f"{name} is not valid UTF-8 JSON") from exc
    if not isinstance(raw, dict):
        raise TemplatePixelProducerError(f"{name} must contain a JSON object")
    return raw


def _probability(value: Any, name: str, *, minimum: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TemplatePixelProducerError(f"invalid {name}") from exc
    if not math.isfinite(number) or not minimum <= number <= 1.0:
        raise TemplatePixelProducerError(f"{name} outside [{minimum},1]")
    return number


def _positive_int(value: Any, name: str, *, maximum: int) -> int:
    if isinstance(value, bool):
        raise TemplatePixelProducerError(f"invalid {name}")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise TemplatePixelProducerError(f"invalid {name}") from exc
    if not 1 <= number <= maximum:
        raise TemplatePixelProducerError(f"invalid {name}")
    return number


def _rect(raw: Any, name: str) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        raise TemplatePixelProducerError(f"{name} must be an object")
    try:
        result = {key: float(raw[key]) for key in ("x", "y", "w", "h")}
    except (KeyError, TypeError, ValueError) as exc:
        raise TemplatePixelProducerError(f"invalid {name}") from exc
    if not all(math.isfinite(value) for value in result.values()) or result["w"] <= 0 or result["h"] <= 0:
        raise TemplatePixelProducerError(f"invalid {name}")
    return result


def _fraction_rect(raw: Any, name: str) -> dict[str, float]:
    result = _rect(raw, name)
    if result["x"] < 0 or result["y"] < 0 or result["x"] + result["w"] > 1 or result["y"] + result["h"] > 1:
        raise TemplatePixelProducerError(f"{name} leaves the card corner")
    return result


def _safe_member(name: str) -> str:
    path = PurePosixPath(str(name or ""))
    if not path.parts or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise TemplatePixelProducerError("unsafe bundle member path")
    return str(path)


@dataclass(frozen=True)
class _Asset:
    member: str
    sha256: str
    data: bytes


@dataclass(frozen=True)
class LoadedTemplatePixelProducer:
    profile: InterfaceProfile
    profile_sha256: str
    backend_sha256: str
    config_sha256: str
    frame_interval_seconds: float
    recognizer: "TemplatePixelRecognizer"


def _asset_entry(raw: Any, files: Mapping[str, bytes], name: str, *, expected_sha: str | None = None) -> _Asset:
    if not isinstance(raw, Mapping):
        raise TemplatePixelProducerError(f"missing {name} asset")
    member = _safe_member(str(raw.get("member") or ""))
    claimed = str(raw.get("sha256") or "").lower()
    data = files.get(member)
    if data is None or not _SHA256.fullmatch(claimed) or _sha_bytes(data) != claimed:
        raise TemplatePixelProducerError(f"{name} asset hash mismatch")
    if expected_sha is not None and claimed != expected_sha:
        raise TemplatePixelProducerError(f"{name} does not match the verified profile")
    return _Asset(member, claimed, data)


def _asset_map(
    raw: Any,
    files: Mapping[str, bytes],
    name: str,
    expected: Mapping[str, str],
) -> dict[str, _Asset]:
    if not isinstance(raw, Mapping) or set(raw) != set(expected):
        raise TemplatePixelProducerError(f"{name} must cover the complete verified symbol set")
    return {
        key: _asset_entry(raw[key], files, f"{name}.{key}", expected_sha=expected[key])
        for key in expected
    }


def load_template_pixel_bundle(path: Path) -> LoadedTemplatePixelProducer:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise TemplatePixelProducerError("template bundle is unavailable") from exc
    if path.is_symlink() or not path.is_file() or not 1 <= size <= MAX_BUNDLE_BYTES:
        raise TemplatePixelProducerError("template bundle size/path rejected")
    backend_sha = _sha_path(path)
    files: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if not 2 <= len(infos) <= MAX_MEMBERS:
                raise TemplatePixelProducerError("template bundle member count rejected")
            total = 0
            for info in infos:
                name = _safe_member(info.filename)
                mode = info.external_attr >> 16
                if info.is_dir() or stat.S_ISLNK(mode) or info.file_size > MAX_MEMBER_BYTES:
                    raise TemplatePixelProducerError("template bundle member rejected")
                total += info.file_size
                if total > MAX_UNCOMPRESSED_BYTES:
                    raise TemplatePixelProducerError("template bundle expands beyond its limit")
                if name in files:
                    raise TemplatePixelProducerError("template bundle repeats a member")
                files[name] = archive.read(info)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, TemplatePixelProducerError):
            raise
        raise TemplatePixelProducerError("template bundle cannot be read") from exc

    profile_bytes = files.get("profile.json")
    manifest_bytes = files.get("manifest.json")
    if profile_bytes is None or manifest_bytes is None:
        raise TemplatePixelProducerError("template bundle lacks profile.json or manifest.json")
    profile_raw = _unique_json(profile_bytes, "profile.json")
    manifest = _unique_json(manifest_bytes, "manifest.json")
    profile = parse_profile(profile_raw)
    if manifest.get("schema") != MANIFEST_SCHEMA or manifest.get("human_verified_assets") is not True:
        raise TemplatePixelProducerError("unsupported or unverified template manifest")
    if (
        manifest.get("profile_id") != profile.profile_id
        or manifest.get("profile_verification_sha256") != profile.verification_sha256
        or manifest.get("template_set_sha256") != profile.template_set_sha256
    ):
        raise TemplatePixelProducerError("template manifest does not bind the exact profile")
    revision = str(manifest.get("revision") or "")
    if not _SAFE_ID.fullmatch(revision):
        raise TemplatePixelProducerError("invalid template producer revision")
    interval = float(manifest.get("frame_interval_seconds", 3.0))
    if not math.isfinite(interval) or not 1.0 <= interval <= 30.0:
        raise TemplatePixelProducerError("frame interval outside [1,30]")

    assets = manifest.get("assets")
    if not isinstance(assets, Mapping):
        raise TemplatePixelProducerError("template manifest lacks assets")
    asset_verification = manifest.get("asset_verification")
    expected_asset_digest = _sha_bytes(_canonical_bytes(assets))
    expected_verification = {
        "method": "HUMAN_LABEL_REVIEW",
        "reviewer_id": profile.verification["reviewer_id"],
        "verified_at": profile.verification["verified_at"],
        "reference_frame_sha256": profile.reference_frame_sha256,
        "assets_sha256": expected_asset_digest,
    }
    if asset_verification != expected_verification:
        raise TemplatePixelProducerError(
            "template assets lack exact human-review binding"
        )
    reference = _asset_entry(
        assets.get("reference_frame"), files, "reference_frame", expected_sha=profile.reference_frame_sha256
    )
    ranks = _asset_map(assets.get("rank_templates"), files, "rank_templates", profile.rank_templates)
    suits = _asset_map(assets.get("suit_templates"), files, "suit_templates", profile.suit_templates)
    cards = _asset_map(assets.get("card_templates"), files, "card_templates", profile.card_templates)
    compass_labels = _asset_map(
        assets.get("compass_label_templates"), files, "compass_label_templates", {seat: str((assets.get("compass_label_templates") or {}).get(seat, {}).get("sha256") or "") for seat in _SEATS}
    )
    dealer = _asset_entry(assets.get("dealer_marker_template"), files, "dealer_marker_template")
    digits_raw = assets.get("board_digit_templates")
    if not isinstance(digits_raw, Mapping) or set(digits_raw) != set("0123456789"):
        raise TemplatePixelProducerError("board digit templates must cover 0..9")
    digits = {digit: _asset_entry(digits_raw[digit], files, f"board_digit_templates.{digit}") for digit in "0123456789"}

    auction_assets: dict[str, _Asset] = {}
    auction_raw = assets.get("auction_call_templates")
    if auction_raw is not None:
        if not isinstance(auction_raw, Mapping) or not auction_raw:
            raise TemplatePixelProducerError("auction call templates must be a non-empty object")
        auction_assets = {
            str(call): _asset_entry(entry, files, f"auction_call_templates.{call}")
            for call, entry in auction_raw.items()
        }

    config_sha = _sha_bytes(_canonical_bytes({
        "manifest": manifest,
        "profile_sha256": _sha_bytes(profile_bytes),
        "backend_sha256": backend_sha,
    }))
    recognizer = TemplatePixelRecognizer(
        profile=profile,
        manifest=manifest,
        reference=reference,
        ranks=ranks,
        suits=suits,
        cards=cards,
        compass_labels=compass_labels,
        dealer_marker=dealer,
        board_digits=digits,
        auction_calls=auction_assets,
    )
    return LoadedTemplatePixelProducer(
        profile=profile,
        profile_sha256=_sha_bytes(profile_bytes),
        backend_sha256=backend_sha,
        config_sha256=config_sha,
        frame_interval_seconds=interval,
        recognizer=recognizer,
    )


class TemplatePixelRecognizer:
    def __init__(
        self,
        *,
        profile: InterfaceProfile,
        manifest: Mapping[str, Any],
        reference: _Asset,
        ranks: Mapping[str, _Asset],
        suits: Mapping[str, _Asset],
        cards: Mapping[str, _Asset],
        compass_labels: Mapping[str, _Asset],
        dealer_marker: _Asset,
        board_digits: Mapping[str, _Asset],
        auction_calls: Mapping[str, _Asset],
    ):
        self.profile = profile
        self.manifest = dict(manifest)
        self._asset_bytes = {
            "reference": reference.data,
            "ranks": {key: value.data for key, value in ranks.items()},
            "suits": {key: value.data for key, value in suits.items()},
            "cards": {key: value.data for key, value in cards.items()},
            "compass": {key: value.data for key, value in compass_labels.items()},
            "dealer": dealer_marker.data,
            "digits": {key: value.data for key, value in board_digits.items()},
            "auction": {key: value.data for key, value in auction_calls.items()},
        }
        card_cfg = self.manifest.get("card_matching")
        compass_cfg = self.manifest.get("compass")
        registration = self.manifest.get("registration")
        if not isinstance(card_cfg, Mapping) or not isinstance(compass_cfg, Mapping) or not isinstance(registration, Mapping):
            raise TemplatePixelProducerError("template manifest lacks card/compass/registration config")
        self.card_cfg = dict(card_cfg)
        self.compass_cfg = dict(compass_cfg)
        self.registration_cfg = dict(registration)
        self.rank_crop = _fraction_rect(card_cfg.get("rank_crop"), "rank_crop")
        self.suit_crop = _fraction_rect(card_cfg.get("suit_crop"), "suit_crop")
        self.card_threshold = _probability(card_cfg.get("full_card_threshold"), "full_card_threshold", minimum=0.90)
        self.glyph_threshold = _probability(card_cfg.get("glyph_threshold"), "glyph_threshold", minimum=0.90)
        self.min_margin = _probability(card_cfg.get("min_margin", 0.02), "min_margin")
        self.compass_threshold = _probability(compass_cfg.get("match_threshold"), "compass match threshold", minimum=0.90)
        self.max_candidates = _positive_int(card_cfg.get("max_candidates", 52), "max_candidates", maximum=MAX_CARD_CANDIDATES)
        self._images: dict[str, Any] | None = None

    @staticmethod
    def _imports() -> tuple[Any, Any]:
        try:
            import cv2
            import numpy as np
        except ImportError as exc:
            raise TemplatePixelProducerError("OpenCV template runtime is unavailable") from exc
        return cv2, np

    def _decode(self, value: bytes) -> Any:
        cv2, np = self._imports()
        image = cv2.imdecode(np.frombuffer(value, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if image is None or image.size == 0:
            raise TemplatePixelProducerError("template image cannot be decoded")
        return image

    def _loaded_images(self) -> dict[str, Any]:
        if self._images is None:
            self._images = {
                "reference": self._decode(self._asset_bytes["reference"]),
                "ranks": {key: self._decode(value) for key, value in self._asset_bytes["ranks"].items()},
                "suits": {key: self._decode(value) for key, value in self._asset_bytes["suits"].items()},
                "cards": {key: self._decode(value) for key, value in self._asset_bytes["cards"].items()},
                "compass": {key: self._decode(value) for key, value in self._asset_bytes["compass"].items()},
                "dealer": self._decode(self._asset_bytes["dealer"]),
                "digits": {key: self._decode(value) for key, value in self._asset_bytes["digits"].items()},
                "auction": {key: self._decode(value) for key, value in self._asset_bytes["auction"].items()},
            }
        return self._images

    @staticmethod
    def _mask(image: Any) -> Any:
        cv2, _ = TemplatePixelRecognizer._imports()
        blurred = cv2.GaussianBlur(image, (3, 3), 0)
        return cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

    @staticmethod
    def _crop(image: Any, rect: Mapping[str, float]) -> Any:
        x = max(0, int(round(rect["x"])))
        y = max(0, int(round(rect["y"])))
        right = min(image.shape[1], int(round(rect["x"] + rect["w"])))
        bottom = min(image.shape[0], int(round(rect["y"] + rect["h"])))
        if right <= x or bottom <= y:
            raise TemplatePixelProducerError("configured ROI leaves the registered frame")
        return image[y:bottom, x:right]

    @staticmethod
    def _fraction_crop(image: Any, rect: Mapping[str, float]) -> Any:
        height, width = image.shape[:2]
        return TemplatePixelRecognizer._crop(image, {
            "x": rect["x"] * width,
            "y": rect["y"] * height,
            "w": rect["w"] * width,
            "h": rect["h"] * height,
        })

    def _score(self, crop: Any, template: Any) -> float:
        cv2, _ = self._imports()
        resized = cv2.resize(self._mask(crop), (template.shape[1], template.shape[0]), interpolation=cv2.INTER_AREA)
        score = float(cv2.matchTemplate(resized, self._mask(template), cv2.TM_CCOEFF_NORMED)[0, 0])
        return min(1.0, max(0.0, score if math.isfinite(score) else 0.0))

    def _classify(self, crop: Any, templates: Mapping[str, Any]) -> tuple[str, float, float]:
        ranked = sorted(
            ((self._score(crop, template), label) for label, template in templates.items()),
            reverse=True,
        )
        if not ranked:
            raise TemplatePixelProducerError("empty classifier template set")
        best_score, best_label = ranked[0]
        second = ranked[1][0] if len(ranked) > 1 else 0.0
        return best_label, best_score, best_score - second

    def _register(self, gray: Any, frame_sha: str) -> tuple[Any, dict[str, Any]]:
        cv2, np = self._imports()
        images = self._loaded_images()
        reference = images["reference"]
        if frame_sha == self.profile.reference_frame_sha256:
            matrix = np.eye(3, dtype="float64")
            inliers = max(self.profile.min_registration_inliers, 100)
            ratio = 1.0
        else:
            features = _positive_int(self.registration_cfg.get("max_features", 4000), "registration.max_features", maximum=20000)
            ratio_gate = _probability(self.registration_cfg.get("knn_ratio", 0.75), "registration.knn_ratio")
            ransac = float(self.registration_cfg.get("ransac_reprojection_px", 3.0))
            if not math.isfinite(ransac) or not 0.1 <= ransac <= 20:
                raise TemplatePixelProducerError("invalid registration RANSAC threshold")
            orb = cv2.ORB_create(nfeatures=features)
            kp_frame, des_frame = orb.detectAndCompute(gray, None)
            kp_ref, des_ref = orb.detectAndCompute(reference, None)
            if des_frame is None or des_ref is None or len(kp_frame) < 4 or len(kp_ref) < 4:
                matrix = np.eye(3, dtype="float64"); inliers = 1; ratio = 0.0
            else:
                matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(des_frame, des_ref, k=2)
                good = [first for first, second in matches if first.distance < ratio_gate * second.distance]
                if len(good) < 4:
                    matrix = np.eye(3, dtype="float64"); inliers = 1; ratio = 0.0
                else:
                    source = np.float32([kp_frame[item.queryIdx].pt for item in good]).reshape(-1, 1, 2)
                    target = np.float32([kp_ref[item.trainIdx].pt for item in good]).reshape(-1, 1, 2)
                    found, mask = cv2.findHomography(source, target, cv2.RANSAC, ransac)
                    if found is None or mask is None:
                        matrix = np.eye(3, dtype="float64"); inliers = 1; ratio = 0.0
                    else:
                        matrix = found
                        inliers = max(1, int(mask.ravel().sum()))
                        ratio = inliers / len(good)
        warped = cv2.warpPerspective(gray, matrix, (self.profile.reference_width, self.profile.reference_height))
        return warped, {
            "reference_frame_sha256": self.profile.reference_frame_sha256,
            "homography": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            "inliers": inliers,
            "inlier_ratio": ratio,
        }

    @staticmethod
    def _iou(left: Mapping[str, float], right: Mapping[str, float]) -> float:
        x1 = max(left["x"], right["x"]); y1 = max(left["y"], right["y"])
        x2 = min(left["x"] + left["w"], right["x"] + right["w"])
        y2 = min(left["y"] + left["h"], right["y"] + right["h"])
        inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        union = left["w"] * left["h"] + right["w"] * right["h"] - inter
        return inter / union if union > 0 else 0.0

    def _card_matches(self, warped: Any) -> list[dict[str, Any]]:
        cv2, np = self._imports()
        images = self._loaded_images()
        region_rect = _rect(self.card_cfg.get("search_region", self.profile.table_region), "card search region")
        region = self._crop(warped, region_rect)
        raw: list[dict[str, Any]] = []
        for card, template in images["cards"].items():
            if template.shape[0] > region.shape[0] or template.shape[1] > region.shape[1]:
                raise TemplatePixelProducerError("card template is larger than its search region")
            # Keep the independent full-card channel sensitive to the complete
            # grayscale corner.  Thresholding here erased small rank/suit and
            # texture differences at card borders, allowing unrelated card
            # references to collapse to the same binary mask.
            scores = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
            peaks = scores == cv2.dilate(scores, np.ones((5, 5), dtype=np.uint8))
            ys, xs = np.where(peaks & (scores >= self.card_threshold))
            ranked = sorted(((float(scores[y, x]), int(x), int(y)) for y, x in zip(ys, xs)), reverse=True)[:4]
            for score, x, y in ranked:
                raw.append({
                    "card": card,
                    "confidence": score,
                    "box": {
                        "x": region_rect["x"] + x,
                        "y": region_rect["y"] + y,
                        "w": float(template.shape[1]),
                        "h": float(template.shape[0]),
                    },
                })
        selected: list[dict[str, Any]] = []
        for candidate in sorted(raw, key=lambda item: item["confidence"], reverse=True):
            overlaps = [item for item in selected if self._iou(candidate["box"], item["box"]) >= 0.35]
            if overlaps:
                continue
            selected.append(candidate)
            if len(selected) >= self.max_candidates:
                break
        return selected

    def _board_number(self, crop: Any) -> tuple[int, float]:
        cv2, _ = self._imports()
        single, single_score, single_margin = self._classify(crop, self._loaded_images()["digits"])
        if single_score >= self.compass_threshold and single_margin >= self.min_margin:
            number = int(single)
            if number >= 1:
                return number, single_score
        mask = self._mask(crop)
        contours, _hierarchy = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes = []
        min_area = max(2.0, mask.shape[0] * mask.shape[1] * 0.002)
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if w * h >= min_area and h >= max(3, mask.shape[0] * 0.2):
                boxes.append((x, y, w, h))
        boxes.sort()
        if not 1 <= len(boxes) <= 4:
            raise TemplatePixelProducerError("board number digit segmentation failed")
        digits = []
        confidence = 1.0
        for x, y, w, h in boxes:
            label, score, margin = self._classify(crop[y:y+h, x:x+w], self._loaded_images()["digits"])
            if score < self.compass_threshold or margin < self.min_margin:
                raise TemplatePixelProducerError("board number digit below template gate")
            digits.append(label); confidence = min(confidence, score)
        number = int("".join(digits))
        if number < 1:
            raise TemplatePixelProducerError("invalid board number")
        return number, confidence

    def _compass(self, warped: Any) -> dict[str, Any]:
        cfg = self.compass_cfg
        if str(cfg.get("interface") or "").upper() != "BRIDGIT":
            raise TemplatePixelProducerError("unsupported compass profile interface")
        region = _rect(cfg.get("region"), "compass.region")
        seat_rois = cfg.get("seat_rois")
        dealer_rois = cfg.get("dealer_marker_rois")
        if not isinstance(seat_rois, Mapping) or set(seat_rois) != set(_POSITIONS):
            raise TemplatePixelProducerError("compass seat ROIs must cover all positions")
        if not isinstance(dealer_rois, Mapping) or set(dealer_rois) != set(_POSITIONS):
            raise TemplatePixelProducerError("dealer marker ROIs must cover all positions")
        labels: dict[str, Any] = {}
        for position in _POSITIONS:
            crop = self._crop(warped, _rect(seat_rois[position], f"compass.seat_rois.{position}"))
            value, score, margin = self._classify(crop, self._loaded_images()["compass"])
            if score < self.compass_threshold or margin < self.min_margin:
                raise TemplatePixelProducerError("compass label below template gate")
            labels[position] = {
                "value": value, "confidence": score,
                "evidence_locator": f"registered-frame#compass-label-{position}",
            }
        board_roi = _rect(cfg.get("board_number_roi"), "compass.board_number_roi")
        board, board_confidence = self._board_number(self._crop(warped, board_roi))
        marker_scores = []
        for position in _POSITIONS:
            crop = self._crop(warped, _rect(dealer_rois[position], f"compass.dealer_marker_rois.{position}"))
            marker_scores.append((self._score(crop, self._loaded_images()["dealer"]), position))
        marker_scores.sort(reverse=True)
        marker_score, marker_position = marker_scores[0]
        marker_margin = marker_score - marker_scores[1][0]
        if marker_score < self.compass_threshold or marker_margin < self.min_margin:
            raise TemplatePixelProducerError("dealer marker below template gate")
        return {
            "interface": "BRIDGIT",
            "human_verified_profile": True,
            "scope": str(cfg.get("scope") or self.profile.profile_id),
            "region": region,
            "seat_labels": labels,
            "board_number": {
                "value": board, "confidence": board_confidence,
                "evidence_locator": "registered-frame#board-number",
            },
            "dealer_marker": {
                "value": marker_position, "confidence": marker_score,
                "evidence_locator": "registered-frame#dealer-marker",
            },
        }

    def _auction(self, warped: Any, *, board_number: int, dealer: str) -> dict[str, Any] | None:
        cfg = self.manifest.get("auction")
        templates = self._loaded_images()["auction"]
        if cfg is None or not templates:
            return None
        if not isinstance(cfg, Mapping):
            raise TemplatePixelProducerError("auction config must be an object")
        threshold = _probability(cfg.get("template_threshold"), "auction template threshold", minimum=0.90)
        ocr_threshold = _probability(cfg.get("ocr_threshold"), "auction OCR threshold", minimum=0.90)
        columns = cfg.get("columns")
        if not isinstance(columns, Mapping) or set(columns) != set(_SEATS):
            raise TemplatePixelProducerError("auction columns must cover N,E,S,W")
        origin_y = float(cfg.get("origin_y")); row_stride = float(cfg.get("row_stride"))
        cell_width = float(cfg.get("cell_width")); cell_height = float(cfg.get("cell_height"))
        if not all(math.isfinite(v) and v > 0 for v in (row_stride, cell_width, cell_height)) or not math.isfinite(origin_y):
            raise TemplatePixelProducerError("invalid auction grid")
        max_calls = _positive_int(cfg.get("max_calls", 40), "auction.max_calls", maximum=80)
        try:
            import pytesseract
            from pytesseract import Output
        except ImportError as exc:
            raise TemplatePixelProducerError("auction OCR runtime is unavailable") from exc
        dealer_index = _SEATS.index(dealer)
        calls: list[str] = []
        cells: list[dict[str, Any]] = []
        for index in range(max_calls):
            seat = _SEATS[(dealer_index + index) % 4]
            row = (dealer_index + index) // 4
            rect = {"x": float(columns[seat]), "y": origin_y + row * row_stride, "w": cell_width, "h": cell_height}
            crop = self._crop(warped, rect)
            call, template_score, margin = self._classify(crop, templates)
            if template_score < threshold or margin < self.min_margin:
                break
            data = pytesseract.image_to_data(
                crop,
                config="--psm 10",
                output_type=Output.DICT,
            )
            tokens = [(str(text).strip(), float(conf)) for text, conf in zip(data.get("text", []), data.get("conf", [])) if str(text).strip()]
            if not tokens:
                raise TemplatePixelProducerError("auction OCR found no token")
            token, confidence100 = max(tokens, key=lambda item: item[1])
            try:
                ocr_call = validate_auction_prefix([token], dealer=dealer)["normalized_calls"][0]
            except Exception:
                from bridge_contracts.video_auction import normalize_call
                ocr_call = normalize_call(token)
            ocr_confidence = max(0.0, min(1.0, confidence100 / 100.0))
            from bridge_contracts.video_auction import normalize_call
            template_call = normalize_call(call)
            if ocr_call != template_call or ocr_confidence < ocr_threshold:
                raise TemplatePixelProducerError("auction OCR and template channels disagree")
            calls.append(template_call)
            cells.append({
                "seat": seat, "column": seat, "row": row, "box": rect,
                "ocr": {"value": ocr_call, "confidence": ocr_confidence, "channel_id": "tesseract-auction-v1"},
                "reference_match": {"value": template_call, "confidence": template_score, "channel_id": "template-auction-v1"},
                "evidence_locator": f"registered-frame#auction-cell-{index}",
            })
        if not calls:
            return None
        legality = validate_auction_prefix(calls, dealer=dealer)
        return {
            "source": "BRIDGIT_AUCTION_TABLE",
            "board_number": board_number,
            "dealer": dealer,
            "calls": cells,
            "complete": legality["terminated"],
            "evidence_locator": "registered-frame#auction-table",
        }

    def __call__(self, frame: Path, profile_view: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            return self._recognize(frame, profile_view)
        except TemplatePixelProducerError as exc:
            # The profiled challenger converts this bounded backend failure into
            # a REVIEW record instead of aborting the entire video.
            raise ProfiledChallengerError(str(exc)) from exc

    def _recognize(self, frame: Path, profile_view: Mapping[str, Any]) -> Mapping[str, Any]:
        if (
            profile_view.get("profile_id") != self.profile.profile_id
            or profile_view.get("template_set_sha256") != self.profile.template_set_sha256
            or profile_view.get("verification_sha256") != self.profile.verification_sha256
        ):
            raise TemplatePixelProducerError("recognizer received a different interface profile")
        cv2, _ = self._imports()
        image = cv2.imread(str(frame), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise TemplatePixelProducerError("video frame cannot be decoded")
        frame_sha = _sha_path(frame)
        warped, registration = self._register(image, frame_sha)
        cards = []
        images = self._loaded_images()
        for candidate in self._card_matches(warped):
            corner = self._crop(warped, candidate["box"])
            rank, rank_score, rank_margin = self._classify(self._fraction_crop(corner, self.rank_crop), images["ranks"])
            suit, suit_score, suit_margin = self._classify(self._fraction_crop(corner, self.suit_crop), images["suits"])
            if min(rank_score, suit_score) < self.glyph_threshold or min(rank_margin, suit_margin) < self.min_margin:
                continue
            cards.append({
                "box": candidate["box"],
                "rank": {"value": rank, "confidence": rank_score, "channel_id": self.profile.rank_suit_channel_id},
                "suit": {"value": suit, "confidence": suit_score, "channel_id": self.profile.rank_suit_channel_id},
                "reference_match": {
                    "card": candidate["card"], "confidence": candidate["confidence"],
                    "channel_id": self.profile.reference_channel_id,
                },
            })
        compass = self._compass(warped)
        result: dict[str, Any] = {
            "frame_sha256": frame_sha,
            "registration": registration,
            "cards": cards,
            "ordering_prior": dict(self.profile.ordering_prior),
        }
        guarded = guard_recognizer_result(
            result,
            compass,
            expected_region=self.compass_cfg["region"],
            reference_size={"width": self.profile.reference_width, "height": self.profile.reference_height},
            min_confidence=self.compass_threshold,
        )
        board_number = int(guarded["board_metadata"]["board_number"]["value"])
        dealer = str(guarded["board_metadata"]["dealer"]["value"])
        auction = self._auction(warped, board_number=board_number, dealer=dealer)
        if auction is not None:
            guarded["auction"] = auction
        return guarded


__all__ = [
    "BUNDLE_SCHEMA",
    "LoadedTemplatePixelProducer",
    "MANIFEST_SCHEMA",
    "MAX_BUNDLE_BYTES",
    "PRODUCER_REVISION",
    "TemplatePixelProducerError",
    "TemplatePixelRecognizer",
    "load_template_pixel_bundle",
]
