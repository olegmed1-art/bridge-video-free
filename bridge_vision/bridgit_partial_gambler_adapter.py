"""Local, shadow-only bridge from a pinned Gambler sprite to partial evidence.

The caller supplies a pixel observer; this module does not claim to classify
real video pixels. It binds the observer to one validated original sprite and
checks frame identity before calling it. No Gold-v2 packaging is needed here.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
from typing import Any, Callable, Mapping, Sequence

from bridge_vision.bridgit_partial_evidence import (
    PartialEvidenceError, SamplingContract, _frame_identity,
    accumulate_detected_frames,
)
from bridge_vision.bridgit_primary_video import (
    PrimaryVideoRecognitionError, resolve_original_gambler_asset,
)
from bridge_vision.bridgit_board_number import read_board_number
from bridge_vision.bridgit_partial_pixel_observer import GamblerPartialPixelObserver
from bridge_vision.gambler_classic_reference import (
    MAX_SPRITE_BYTES, GamblerClassicReferenceError, GamblerClassicSprite,
    validate_sprite_bytes,
)
from bridge_vision.gambler_reference_authority import authority_provenance
from bridge_vision.native_cards import NativeFourSeatCardDetector

PARTIAL_GAMBLER_ADAPTER_VERSION = "bridgit-partial-gambler-shadow-v1"
PixelObserver = Callable[[Path, GamblerClassicSprite], Mapping[str, Any]]


def _load_shadow_sprite(path: Path, *, expected_sha256: str,
                        expected_variant: int) -> GamblerClassicSprite:
    """Read a bounded pinned PNG as bytes, including on Windows.

    The frozen production reference loader is SHA-pinned by an independent CI
    contract. Keep this platform fix inside the offline shadow adapter.
    """
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
             | getattr(os, "O_NOFOLLOW", 0))
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise GamblerClassicReferenceError("sprite is unavailable") from exc
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or not 0 < meta.st_size <= MAX_SPRITE_BYTES:
            raise GamblerClassicReferenceError("sprite file is not a bounded regular file")
        chunks: list[bytes] = []
        remaining = MAX_SPRITE_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError as exc:
        raise GamblerClassicReferenceError("sprite is unavailable") from exc
    finally:
        os.close(fd)
    payload = b"".join(chunks)
    if not payload or len(payload) > MAX_SPRITE_BYTES:
        raise GamblerClassicReferenceError("sprite exceeds the byte bound")
    return validate_sprite_bytes(payload, expected_sha256=expected_sha256,
                                 expected_variant=expected_variant)


def accumulate_gambler_partial_frames(
    samples: Sequence[Mapping[str, Any]], *, asset_root: Path,
    verified_card_width_px: float, verified_card_height_px: float,
    pixel_observer: PixelObserver, contract: SamplingContract,
) -> dict[str, Any]:
    """Accept raw card boxes from an explicitly supplied partial pixel observer.

    Each sample needs a local frame_path, matching frame_sha256, source-video
    hash, episode, board occurrence and two matching board-number reads, PTS,
    index, raw size and data_class. Boxes are in the observer's table_region
    coordinate system. The result remains review-only.
    """
    contract.validate()
    try:
        variant, path, expected_sha = resolve_original_gambler_asset(
            Path(asset_root), verified_card_width_px=verified_card_width_px,
            verified_card_height_px=verified_card_height_px,
        )
        sprite = _load_shadow_sprite(path, expected_sha256=expected_sha,
                                     expected_variant=variant)
    except (PrimaryVideoRecognitionError, GamblerClassicReferenceError) as exc:
        raise PartialEvidenceError("PINNED_GAMBLER_ASSET_UNAVAILABLE") from exc

    reference_id = f"gambler-classic-v{variant}-{sprite.sprite_sha256}"
    bound_samples: list[dict[str, Any]] = []
    for sample in samples:
        if not isinstance(sample, Mapping):
            raise PartialEvidenceError("FRAME_INVALID")
        if sample.get("reference_profile_id", reference_id) != reference_id:
            raise PartialEvidenceError("REFERENCE_PROFILE_MISMATCH")
        if not isinstance(sample.get("frame_path"), Path):
            raise PartialEvidenceError("FRAME_FILE_UNAVAILABLE")
        bound = dict(sample, reference_profile_id=reference_id,
                     recognizer_version=PARTIAL_GAMBLER_ADAPTER_VERSION)
        _frame_identity(bound)
        bound_samples.append(bound)
    by_path = {Path(sample["frame_path"]).resolve(): sample for sample in bound_samples}
    if len(by_path) != len(bound_samples):
        raise PartialEvidenceError("FRAME_PATH_REUSED")
    receipts: list[dict[str, Any]] = []

    def detect(path: Path) -> dict[str, Any]:
        try:
            index = int(path.parent.name)
            sample = bound_samples[index]
            if index < 0 or path.name != sample["frame_path"].name:
                raise ValueError("snapshot identity mismatch")
        except (ValueError, IndexError) as exc:
            raise PartialEvidenceError("FRAME_SNAPSHOT_MISMATCH") from exc
        board_read = None
        decoded_size = None
        if sample["data_class"] == "REAL_SHADOW":
            import cv2  # type: ignore

            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                raise PartialEvidenceError("FRAME_DECODE_UNAVAILABLE")
            decoded_size = {"width": int(image.shape[1]), "height": int(image.shape[0])}
            if decoded_size != {axis: sample["raw_frame_size"][axis]
                                for axis in ("width", "height")}:
                raise PartialEvidenceError("RAW_FRAME_SIZE_MISMATCH")
            if (isinstance(pixel_observer, GamblerPartialPixelObserver)
                    and (pixel_observer.geometry.width, pixel_observer.geometry.height)
                    != (decoded_size["width"], decoded_size["height"])):
                raise PartialEvidenceError("OBSERVER_FRAME_SIZE_MISMATCH")
            board_read = read_board_number(image)
            if (board_read.get("status") != "READ"
                    or board_read.get("number") != sample["board_number"]):
                raise PartialEvidenceError("BOARD_NUMBER_PIXEL_MISMATCH")
            if (not isinstance(pixel_observer, GamblerPartialPixelObserver)
                    or not pixel_observer.geometry.auto_discover):
                raise PartialEvidenceError("REAL_CORNER_GEOMETRY_UNPROVEN")
        payload = pixel_observer(path, sprite)
        if not isinstance(payload, Mapping):
            raise PartialEvidenceError("PIXEL_OBSERVER_INVALID")
        if (sample["data_class"] == "REAL_SHADOW"
                and (not isinstance(payload.get("corner_detection"), Mapping)
                     or payload["corner_detection"].get("mode") != "PINNED_BORDER_SEAT_BANDS")):
            raise PartialEvidenceError("REAL_CORNER_GEOMETRY_UNPROVEN")
        unknown = payload.get("unknown") or []
        if (not isinstance(unknown, list) or len(unknown) > 52
                or any(not isinstance(item, Mapping) for item in unknown)):
            raise PartialEvidenceError("PIXEL_OBSERVER_UNKNOWN_INVALID")
        native = NativeFourSeatCardDetector(
            lambda _: payload, min_card_confidence=contract.minimum_card_confidence,
        )(path)
        native["evidence"]["rejected"].extend(
            {key: item[key] for key in ("index", "reason", "seat", "best_score", "margin")
             if key in item} for item in unknown
        )
        receipts.append({
            "frame_sha256": sample["frame_sha256"],
            "source_video_sha256": sample["source_video_sha256"],
            "timestamp_ms": sample["timestamp_ms"],
            "raw_frame_size": sample["raw_frame_size"],
            "decoded_frame_size": decoded_size,
            "board_number_read_from_pixels": board_read,
            "coordinate_space": "LOCAL_FRAME_PNG",
            "corner_detection": payload.get("corner_detection"),
            "observed_boxes": [item.get("box") for item in payload.get("cards", [])],
            "unknown": unknown,
        })
        return native

    detector = detect
    result = accumulate_detected_frames(bound_samples, detector=detector, contract=contract)
    result["reference_provenance"] = {
        **authority_provenance(variant),
        "reference_profile_id": reference_id,
        "adapter_version": PARTIAL_GAMBLER_ADAPTER_VERSION,
        "pixel_observer": "CALLER_SUPPLIED_UNVERIFIED",
    }
    result["detector_receipts"] = receipts
    return result


__all__ = ["PARTIAL_GAMBLER_ADAPTER_VERSION", "PixelObserver",
           "accumulate_gambler_partial_frames"]
