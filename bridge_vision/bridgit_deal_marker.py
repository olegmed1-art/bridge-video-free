"""Stable visual deal markers for autonomous Bridgit video segmentation."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from typing import Any

from bridge_vision.bridgit_visible_hand_observer import ObserverProfile

MARKER_VERSION = "bridgit-visual-deal-marker-v1"
_BITS = re.compile(r"^[0-9a-f]{128}$")


class DealMarkerError(ValueError):
    """Frames cannot be segmented without weakening the visual identity gate."""


def marker_fingerprint(image: Any, profile: ObserverProfile) -> str:
    """Return a 512-bit binary fingerprint of the displayed board number."""

    if tuple(image.shape[:2]) != (profile.height, profile.width):
        raise DealMarkerError("frame dimensions do not match profile")
    try:
        import cv2  # type: ignore
    except ImportError as exc:  # pragma: no cover - runtime dependent
        raise RuntimeError("opencv-python-headless is required") from exc
    x0 = round(profile.width * 0.6958)
    x1 = round(profile.width * 0.7167)
    y0 = profile.rows["N"][0] + 42
    y1 = min(profile.height, profile.rows["N"][0] + 66)
    if x1 <= x0 or y1 <= y0:
        raise DealMarkerError("profile cannot define the board marker region")
    gray = cv2.cvtColor(image[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    normalized = cv2.resize(gray, (32, 16), interpolation=cv2.INTER_AREA)
    differences = normalized < 100
    value = 0
    for bit in differences.ravel():
        value = (value << 1) | int(bit)
    return f"{value:0128x}"


def hamming_distance(left: str, right: str) -> int:
    if not _BITS.fullmatch(left) or not _BITS.fullmatch(right):
        raise DealMarkerError("marker fingerprint must be 512 lowercase bits")
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _marker_sha(bits: str) -> str:
    return hashlib.sha256(bytes.fromhex(bits)).hexdigest()


def assign_stable_deal_markers(
    raw_frames: Iterable[Mapping[str, Any]],
    *,
    max_hamming_distance: int = 8,
    min_stable_frames: int = 2,
) -> dict[str, Any]:
    """Debounce marker changes and bind accepted frames to stable segments."""

    if not 0 <= max_hamming_distance <= 64:
        raise DealMarkerError("max_hamming_distance is outside 0..64")
    if not 2 <= min_stable_frames <= 8:
        raise DealMarkerError("min_stable_frames is outside 2..8")
    frames = []
    for index, raw in enumerate(raw_frames):
        if not isinstance(raw, Mapping):
            raise DealMarkerError("marker frame must be an object")
        bits = str(raw.get("deal_marker_fingerprint") or "")
        if not _BITS.fullmatch(bits):
            raise DealMarkerError("marker fingerprint must be 512 lowercase bits")
        frames.append((index, dict(raw), bits))
    if not frames:
        raise DealMarkerError("at least one marker frame is required")

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    current_bits: str | None = None
    pending: list[tuple[int, dict[str, Any], str]] = []

    def accept(group: list[tuple[int, dict[str, Any], str]], marker: str) -> None:
        digest = _marker_sha(marker)
        for _, frame, bits in group:
            frame["deal_marker_sha256"] = digest
            frame["deal_marker_distance"] = hamming_distance(marker, bits)
            frame.pop("deal_marker_fingerprint", None)
            accepted.append(frame)

    def reject(group: list[tuple[int, dict[str, Any], str]]) -> None:
        rejected.extend(
            {
                "frame_index": index,
                "reason": "UNSTABLE_DEAL_MARKER_TRANSITION",
                "deal_marker_fingerprint": bits,
            }
            for index, _, bits in group
        )

    for item in frames:
        _, _, bits = item
        if current_bits is None:
            if pending and hamming_distance(pending[0][2], bits) > max_hamming_distance:
                reject(pending)
                pending = []
            pending.append(item)
            if len(pending) >= min_stable_frames:
                current_bits = pending[0][2]
                accept(pending, current_bits)
                pending = []
            continue

        if hamming_distance(current_bits, bits) <= max_hamming_distance:
            reject(pending)
            pending = []
            accept([item], current_bits)
            continue

        if pending and hamming_distance(pending[0][2], bits) > max_hamming_distance:
            reject(pending)
            pending = []
        pending.append(item)
        if len(pending) >= min_stable_frames:
            current_bits = pending[0][2]
            accept(pending, current_bits)
            pending = []

    reject(pending)
    accepted.sort(key=lambda frame: frame.get("timestamp_ms", 0))
    return {
        "version": MARKER_VERSION,
        "accepted_frames": accepted,
        "rejected_frames": rejected,
    }


__all__ = [
    "MARKER_VERSION",
    "DealMarkerError",
    "assign_stable_deal_markers",
    "hamming_distance",
    "marker_fingerprint",
]
