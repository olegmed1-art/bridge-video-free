#!/usr/bin/env python3
"""Run autonomous Bridgit deal recognition over a private local video."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

from bridge_school_api.dds3.model import BridgeDeal
from bridge_vision.bridgit_autonomous_deals import reconstruct_autonomous_deals
from bridge_vision.bridgit_deal_marker import (
    assign_stable_deal_markers,
    marker_fingerprint,
)
from bridge_vision.bridgit_played_card_observer import (
    build_suit_bank,
    observe_played_cards,
)
from bridge_vision.bridgit_visible_hand_observer import (
    VisibleHandObserverError,
    build_rank_bank,
    decode_frame,
    observe_frame,
    parse_profile,
)

JOB_VERSION = "bridgit-autonomous-video-v1"
RECEIPT_SCHEMA = "bridgit-autonomous-video-receipt/v1"
MAX_PROFILE_BYTES = 1024 * 1024
MAX_REFERENCE_BYTES = 32 * 1024 * 1024
MAX_VIDEO_BYTES = 8 * 1024 * 1024 * 1024
MAX_OUTPUT_BYTES = 256 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AutonomousVideoError(ValueError):
    """Raw video cannot be processed without weakening an ingress gate."""


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise AutonomousVideoError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _inside(root: Path, raw: str | Path, field: str) -> Path:
    candidate = Path(raw)
    candidate = candidate if candidate.is_absolute() else root / candidate
    cursor = candidate
    while cursor != root and cursor != cursor.parent:
        if cursor.exists() and cursor.is_symlink():
            raise AutonomousVideoError(f"{field} must not traverse a symlink")
        cursor = cursor.parent
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise AutonomousVideoError(f"{field} escapes job_root") from exc
    return resolved


def _regular_file(path: Path, limit: int, field: str) -> os.stat_result:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AutonomousVideoError(f"{field} is unavailable") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
            raise AutonomousVideoError(f"{field} violates the file bound")
        return info
    finally:
        os.close(descriptor)


def _read(path: Path, limit: int, field: str) -> bytes:
    info = _regular_file(path, limit, field)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        chunks = []
        remaining = info.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise AutonomousVideoError(f"{field} changed while being read")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(
            _read(path, MAX_PROFILE_BYTES, "profile").decode("utf-8"),
            object_pairs_hook=_reject_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AutonomousVideoError("profile is not valid UTF-8 JSON") from exc
    if not isinstance(raw, dict):
        raise AutonomousVideoError("profile must be an object")
    return raw


def _hash_file(path: Path, expected: os.stat_result) -> str:
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        while True:
            chunk = os.read(descriptor, 4 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
        expected.st_dev,
        expected.st_ino,
        expected.st_size,
        expected.st_mtime_ns,
    ):
        raise AutonomousVideoError("video changed while being hashed")
    return digest.hexdigest()


def _write_atomic(path: Path, payload: bytes) -> None:
    if len(payload) > MAX_OUTPUT_BYTES:
        raise AutonomousVideoError("receipt exceeds the byte bound")
    if path.exists() and path.is_symlink():
        raise AutonomousVideoError("output must not be a symlink")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = stream.name
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _merge_direct_cards(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    claims: dict[str, dict[str, Any]] = {}
    for item in (item for group in groups for item in group):
        card = str(item["card"])
        previous = claims.get(card)
        if previous is not None and previous["seat"] != item["seat"]:
            raise AutonomousVideoError("one frame assigns a card to two seats")
        if previous is None or float(item["confidence"]) > float(
            previous["confidence"]
        ):
            claims[card] = dict(item)
    return sorted(claims.values(), key=lambda item: (item["card"], item["seat"]))


def _validate_pbn(pbn: str) -> dict[str, Any]:
    if not pbn.startswith("N:"):
        raise AutonomousVideoError("complete deal emitted non-North PBN")
    parts = pbn[2:].split()
    if len(parts) != 4:
        raise AutonomousVideoError("complete deal emitted malformed PBN")
    hands = {}
    for seat, hand in zip(("N", "E", "S", "W"), parts):
        suits = hand.split(".")
        if len(suits) != 4:
            raise AutonomousVideoError("complete deal emitted malformed PBN hand")
        hands[seat] = dict(zip(("S", "H", "D", "C"), suits))
    BridgeDeal(hands).validate()
    return {
        "checker": "BridgeDeal.validate",
        "status": "PASS",
        "pbn_sha256": hashlib.sha256(pbn.encode()).hexdigest(),
    }


def _pixel_runtime():
    try:
        import cv2  # type: ignore
    except ImportError as exc:  # pragma: no cover - runtime dependent
        raise RuntimeError("opencv-python-headless is required") from exc
    return cv2


def run(
    job_root: Path,
    profile_path: Path,
    video_path: Path,
    output_path: Path,
    *,
    sample_ms: int = 500,
    max_sampled_frames: int = 50_000,
) -> dict[str, Any]:
    root = job_root.resolve()
    if root == Path("/") or not root.is_dir() or job_root.is_symlink():
        raise AutonomousVideoError("job_root must be a non-root real directory")
    profile_file = _inside(root, profile_path, "profile")
    video_file = _inside(root, video_path, "video")
    target = _inside(root, output_path, "output")
    if target in {profile_file, video_file} or not target.parent.is_dir():
        raise AutonomousVideoError("output path is unsafe")
    if not 100 <= sample_ms <= 5000:
        raise AutonomousVideoError("sample_ms is outside 100..5000")
    if not 2 <= max_sampled_frames <= 100_000:
        raise AutonomousVideoError("max_sampled_frames is outside 2..100000")

    profile = parse_profile(_json(profile_file))
    references = {}
    for reference_id, (raw_path, expected_sha) in profile.references.items():
        source = _inside(root, profile_file.parent / raw_path, "reference")
        payload = _read(source, MAX_REFERENCE_BYTES, "reference")
        if hashlib.sha256(payload).hexdigest() != expected_sha:
            raise AutonomousVideoError("reference frame hash mismatch")
        references[reference_id] = decode_frame(payload, profile)
    rank_bank = build_rank_bank(profile, references)
    suit_bank = build_suit_bank(profile, references)

    video_info = _regular_file(video_file, MAX_VIDEO_BYTES, "video")
    video_sha = _hash_file(video_file, video_info)
    cv2 = _pixel_runtime()
    capture = cv2.VideoCapture(str(video_file))
    if not capture.isOpened():
        raise AutonomousVideoError("video decoder could not open the source")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if not fps or fps <= 0 or frame_count <= 0:
        capture.release()
        raise AutonomousVideoError("video metadata is invalid")

    next_sample = 0
    decoded_index = 0
    sampled = 0
    visual_frames = []
    frame_rejections = []
    try:
        while True:
            ok, image = capture.read()
            if not ok:
                break
            timestamp_ms = round(decoded_index * 1000 / fps)
            decoded_index += 1
            if timestamp_ms < next_sample:
                continue
            next_sample += sample_ms
            sampled += 1
            if sampled > max_sampled_frames:
                raise AutonomousVideoError("sampled frame count exceeds the bound")
            if tuple(image.shape[:2]) != (profile.height, profile.width):
                raise AutonomousVideoError("video dimensions do not match profile")
            decoded_sha = hashlib.sha256(image.tobytes()).hexdigest()
            frame_sha = hashlib.sha256(
                f"{video_sha}:{decoded_index - 1}:{timestamp_ms}:{decoded_sha}".encode()
            ).hexdigest()
            hands = observe_frame(image, rank_bank, profile)
            played = observe_played_cards(image, rank_bank, suit_bank, profile)
            if hands["status"] == "CONFLICT" or played["status"] == "CONFLICT":
                frame_rejections.append(
                    {
                        "frame_sha256": frame_sha,
                        "timestamp_ms": timestamp_ms,
                        "reason": "PIXEL_OBSERVER_CONFLICT",
                        "hand_rejected": hands.get("rejected", []),
                        "played_conflicts": played.get("conflicts", []),
                    }
                )
                continue
            try:
                cards = _merge_direct_cards(hands["cards"], played["cards"])
            except AutonomousVideoError as exc:
                frame_rejections.append(
                    {
                        "frame_sha256": frame_sha,
                        "timestamp_ms": timestamp_ms,
                        "reason": str(exc),
                    }
                )
                continue
            if not cards:
                continue
            visual_frames.append(
                {
                    "frame_sha256": frame_sha,
                    "decoded_pixel_sha256": decoded_sha,
                    "timestamp_ms": timestamp_ms,
                    "deal_marker_fingerprint": marker_fingerprint(image, profile),
                    "cards": cards,
                }
            )
    finally:
        capture.release()

    if not visual_frames:
        raise AutonomousVideoError("no direct card observations found in video")
    markers = assign_stable_deal_markers(visual_frames)
    if not markers["accepted_frames"]:
        raise AutonomousVideoError("no stable visual deal segment found in video")
    reconstruction = reconstruct_autonomous_deals(
        markers["accepted_frames"],
        source_scope=f"sha256:{video_sha}",
    )
    pbn_validation = []
    for deal in reconstruction["deals"]:
        if deal["pbn"] is not None:
            pbn_validation.append(
                {
                    "deal_identity": deal["deal_identity"],
                    **_validate_pbn(deal["pbn"]),
                }
            )
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "version": JOB_VERSION,
        "result_scope": "SHADOW_ONLY",
        "source": {
            "filename": video_file.name,
            "sha256": video_sha,
            "size_bytes": video_info.st_size,
            "fps": fps,
            "declared_frame_count": frame_count,
            "decoded_frame_count": decoded_index,
        },
        "profile": {
            "profile_id": profile.profile_id,
            "profile_sha256": profile.profile_sha256,
            "verification_sha256": profile.verification_sha256,
            "review_sheet_sha256": profile.review_sheet_sha256,
        },
        "sampling": {
            "sample_ms": sample_ms,
            "sampled_frame_count": sampled,
            "direct_visual_frame_count": len(visual_frames),
        },
        "marker": {
            "version": markers["version"],
            "accepted_frame_count": len(markers["accepted_frames"]),
            "rejected_frames": markers["rejected_frames"],
        },
        "frame_rejections": frame_rejections,
        "reconstruction": reconstruction,
        "pbn_validation": pbn_validation,
        "uses_language_model": False,
        "requires_screenshot_review": False,
        "canonical_promotion_allowed": False,
    }
    receipt["receipt_sha256"] = hashlib.sha256(
        json.dumps(
            receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    encoded = (
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True).encode()
        + b"\n"
    )
    _write_atomic(target, encoded)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Autonomously recognize bridge deals from a Bridgit video"
    )
    parser.add_argument("--job-root", required=True, type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--sample-ms", type=int, default=500)
    parser.add_argument("--max-sampled-frames", type=int, default=50_000)
    args = parser.parse_args()
    try:
        receipt = run(
            args.job_root,
            args.profile,
            args.video,
            args.output,
            sample_ms=args.sample_ms,
            max_sampled_frames=args.max_sampled_frames,
        )
    except (
        AutonomousVideoError,
        OSError,
        RuntimeError,
        VisibleHandObserverError,
    ) as exc:
        print(f"AUTONOMOUS_VIDEO_REJECTED: {exc}", file=os.sys.stderr)
        return 2
    counts = receipt["reconstruction"]["status_counts"]
    print(
        "SHADOW_AUTONOMOUS_VIDEO "
        f"deals={receipt['reconstruction']['deal_count']} "
        f"complete_observed={counts['COMPLETE_OBSERVED']} "
        f"complete_exact={counts['COMPLETE_DERIVED_EXACT']} "
        f"partial={counts['PARTIAL']} conflicts={counts['CONFLICT']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
