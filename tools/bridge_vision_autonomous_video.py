#!/usr/bin/env python3
"""Run autonomous Bridgit deal recognition over a private local video."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from bridge_school_api.dds3.model import BridgeDeal
from bridge_vision.anchor_registration import (
    AnchorRegistrationError,
    register_from_upper_right_anchor,
    validate_anchor_reference_detail,
    validate_anchor_spec,
)
from bridge_vision.autonomous_frame_registration import apply_registered_game_window
from bridge_vision.bridgit_autonomous_deals import reconstruct_autonomous_deals
from bridge_vision.bridgit_deal_marker import (
    assign_stable_deal_markers,
    marker_fingerprint,
)
from bridge_vision.bridgit_played_card_observer import (
    CARD_SCALE_POLICY_VERSION,
    TABLE_GEOMETRY_VERSION,
    build_card_scale_policy,
    build_suit_bank,
    build_table_geometry_bank,
    observe_played_cards,
)
from bridge_vision.bridgit_visible_hand_observer import (
    VisibleHandObserverError,
    build_rank_bank,
    decode_frame,
    observe_frame,
    parse_profile,
)

JOB_VERSION = "bridgit-autonomous-video-v2"
RECEIPT_SCHEMA = "bridgit-autonomous-video-receipt/v2"
MAX_PROFILE_BYTES = 1024 * 1024
MAX_REFERENCE_BYTES = 32 * 1024 * 1024
MAX_VIDEO_BYTES = 8 * 1024 * 1024 * 1024
MAX_OUTPUT_BYTES = 256 * 1024 * 1024
MAX_PLAYED_EVENT_SNAPSHOTS = 4_096
MAX_PLAYED_EVENT_SNAPSHOT_BYTES = 256 * 1024 * 1024
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


def _write_played_event_snapshots(
    video_path: Path,
    output_path: Path,
    events: list[Mapping[str, Any]],
) -> dict[str, Any]:
    if not events:
        return {
            "status": "NO_ACCEPTED_PLAYED_CARD_EVENTS",
            "event_count": 0,
            "snapshot_count": 0,
            "all_events_have_snapshot": True,
            "directory": None,
            "snapshots": [],
        }
    if len(events) > MAX_PLAYED_EVENT_SNAPSHOTS:
        raise AutonomousVideoError("played-card event snapshot count exceeds the bound")

    requested: dict[int, dict[str, Any]] = {}
    for event in events:
        frame_index = event.get("source_frame_index")
        input_sha = event.get("input_decoded_pixel_sha256")
        if (
            isinstance(frame_index, bool)
            or not isinstance(frame_index, int)
            or frame_index < 0
        ):
            raise AutonomousVideoError("played-card event lacks a source frame index")
        if not _SHA256.fullmatch(str(input_sha or "")):
            raise AutonomousVideoError("played-card event lacks input pixel identity")
        entry = requested.setdefault(
            frame_index,
            {"input_decoded_pixel_sha256": input_sha, "event_refs": []},
        )
        if entry["input_decoded_pixel_sha256"] != input_sha:
            raise AutonomousVideoError(
                "one source frame has conflicting pixel identities"
            )
        entry["event_refs"].append(
            {
                "event_id": event["event_id"],
                "card": event.get("card"),
                "seat": event["seat"],
                "timestamp_ms": event["timestamp_ms"],
                "recognition_status": event["recognition_status"],
            }
        )

    final_directory = output_path.parent / f"{output_path.stem}.played-card-evidence"
    if final_directory.exists() or final_directory.is_symlink():
        raise AutonomousVideoError("played-card evidence output already exists")
    temporary = Path(
        tempfile.mkdtemp(
            dir=output_path.parent,
            prefix=f".{output_path.stem}.played-card-evidence.",
        )
    )
    snapshots: list[dict[str, Any]] = []
    total_bytes = 0
    cv2 = _pixel_runtime()
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        shutil.rmtree(temporary)
        raise AutonomousVideoError(
            "video decoder could not reopen source for snapshots"
        )
    try:
        wanted = sorted(requested)
        wanted_set = set(wanted)
        final_index = wanted[-1]
        index = 0
        while index <= final_index:
            ok, image = capture.read()
            if not ok:
                break
            if index in wanted_set:
                entry = requested[index]
                pixel_sha = hashlib.sha256(image.tobytes()).hexdigest()
                if pixel_sha != entry["input_decoded_pixel_sha256"]:
                    raise AutonomousVideoError(
                        "snapshot decode does not match recognized source pixels"
                    )
                encoded_ok, encoded = cv2.imencode(
                    ".png", image, [cv2.IMWRITE_PNG_COMPRESSION, 3]
                )
                if not encoded_ok:
                    raise AutonomousVideoError("played-card snapshot encoding failed")
                payload = encoded.tobytes()
                total_bytes += len(payload)
                if total_bytes > MAX_PLAYED_EVENT_SNAPSHOT_BYTES:
                    raise AutonomousVideoError(
                        "played-card event snapshots exceed the byte bound"
                    )
                filename = f"frame-{index:08d}.png"
                path = temporary / filename
                _write_atomic(path, payload)
                snapshots.append(
                    {
                        "path": filename,
                        "source_frame_index": index,
                        "input_decoded_pixel_sha256": pixel_sha,
                        "png_sha256": hashlib.sha256(payload).hexdigest(),
                        "size_bytes": len(payload),
                        "event_refs": entry["event_refs"],
                    }
                )
            index += 1
        if len(snapshots) != len(requested):
            raise AutonomousVideoError(
                "not all played-card event snapshots were decoded"
            )
        manifest = {
            "schema": "bridgit-played-card-event-snapshots/v1",
            "event_count": len(events),
            "snapshot_count": len(snapshots),
            "all_events_have_snapshot": sum(
                len(item["event_refs"]) for item in snapshots
            )
            == len(events),
            "total_bytes": total_bytes,
            "snapshots": snapshots,
        }
        manifest["manifest_sha256"] = hashlib.sha256(
            json.dumps(
                manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        _write_atomic(
            temporary / "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode()
            + b"\n",
        )
        if not manifest["all_events_have_snapshot"]:
            raise AutonomousVideoError("a played-card event lacks a snapshot")
        os.rename(temporary, final_directory)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    finally:
        capture.release()
    return {
        "status": "COMPLETE",
        "directory": final_directory.name,
        **manifest,
    }


def _played_region_state(played: Mapping[str, Any]) -> dict[str, str | None]:
    """Return visually occupied trick seats, preserving unreadable cards as UNKNOWN."""

    region_counts: dict[str, int] = {}
    for region in played.get("played_regions", []):
        seat = str(region.get("seat") or "")
        if seat in {"N", "E", "S", "W"}:
            region_counts[seat] = region_counts.get(seat, 0) + 1
    recognized: dict[str, str] = {}
    for item in played.get("cards", []):
        seat = str(item.get("seat") or "")
        card = str(item.get("card") or "")
        if seat in region_counts and re.fullmatch(r"[AKQJT98765432][SHDC]", card):
            recognized[seat] = card
    return {
        seat: recognized.get(seat) if count == 1 else None
        for seat, count in sorted(region_counts.items())
    }


def _new_server_play_events(
    previous: Mapping[str, str | None],
    current: Mapping[str, str | None],
    *,
    timestamp_ms: int,
    source_frame_index: int,
    frame_sha256: str,
    input_decoded_pixel_sha256: str,
    observer_status: str,
    first_event_number: int,
) -> list[dict[str, Any]]:
    """Emit append-only events only for newly occupied trick positions."""

    if not current:
        return []
    new_trick = bool(previous) and len(current) < len(previous)
    new_seats = (
        set(current) if not previous or new_trick else set(current) - set(previous)
    )
    for seat in set(current) & set(previous):
        old_card = previous[seat]
        new_card = current[seat]
        if old_card is not None and new_card is not None and old_card != new_card:
            new_seats.add(seat)
    events = []
    for offset, seat in enumerate(sorted(new_seats)):
        card = current[seat]
        events.append(
            {
                "event_id": f"server-played-{first_event_number + offset:06d}",
                "card": card,
                "seat": seat,
                "recognition_status": (
                    "VISUAL_CARD_CANDIDATE" if card is not None else "UNKNOWN_CARD"
                ),
                "timestamp_ms": timestamp_ms,
                "source_frame_index": source_frame_index,
                "frame_sha256": frame_sha256,
                "input_decoded_pixel_sha256": input_decoded_pixel_sha256,
                "observer_status": observer_status,
                "snapshot_required": True,
            }
        )
    return events


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


def _registration_profile(
    raw_profile: Mapping[str, Any], reference_ids: set[str]
) -> dict[str, Any] | None:
    raw = raw_profile.get("registration")
    if raw is None:
        return None
    if not isinstance(raw, Mapping) or set(raw) != {
        "mode",
        "reference_id",
        "interface_anchor",
    }:
        raise AutonomousVideoError("registration profile is invalid")
    if raw.get("mode") != "UPPER_RIGHT_ANCHOR":
        raise AutonomousVideoError("registration mode is unsupported")
    reference_id = str(raw.get("reference_id") or "")
    if reference_id not in reference_ids:
        raise AutonomousVideoError("registration reference is unknown")
    try:
        anchor = validate_anchor_spec(raw.get("interface_anchor"))
    except AnchorRegistrationError as exc:
        raise AutonomousVideoError(f"registration profile is invalid: {exc}") from exc
    return {"reference_id": reference_id, "interface_anchor": anchor}


def run(
    job_root: Path,
    profile_path: Path,
    video_path: Path,
    output_path: Path,
    *,
    sample_ms: int = 100,
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

    raw_profile = _json(profile_file)
    profile = parse_profile(raw_profile)
    references = {}
    for reference_id, (raw_path, expected_sha) in profile.references.items():
        source = _inside(root, profile_file.parent / raw_path, "reference")
        payload = _read(source, MAX_REFERENCE_BYTES, "reference")
        if hashlib.sha256(payload).hexdigest() != expected_sha:
            raise AutonomousVideoError("reference frame hash mismatch")
        references[reference_id] = decode_frame(payload, profile)
    rank_bank = build_rank_bank(profile, references)
    suit_bank = build_suit_bank(profile, references)
    geometry_bank = build_table_geometry_bank(profile, references)
    card_scale_policy = build_card_scale_policy(profile, geometry_bank)
    registration_profile = _registration_profile(raw_profile, set(profile.references))
    registration_reference = None
    if registration_profile is not None:
        registration_reference = references[registration_profile["reference_id"]]
        try:
            validate_anchor_reference_detail(
                registration_reference,
                [],
                registration_profile["interface_anchor"],
            )
        except AnchorRegistrationError as exc:
            raise AutonomousVideoError(
                f"registration reference is invalid: {exc}"
            ) from exc

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
    registration_lock = None
    registration_searches = 0
    registration_locked_frames = 0
    registration_rejections = 0
    registration_transforms: dict[str, dict[str, Any]] = {}
    registration_input_sizes: set[tuple[int, int]] = set()
    played_layout_proven_frames = 0
    played_layout_rejected_frames = 0
    played_layout_geometry_shas: set[str] = set()
    played_layout_transform_shas: set[str] = set()
    server_play_events: list[dict[str, Any]] = []
    previous_played_state: dict[str, str | None] = {}
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
            input_decoded_sha = hashlib.sha256(image.tobytes()).hexdigest()
            registration_evidence = None
            if registration_profile is not None:
                assert registration_reference is not None
                registration_input_sizes.add((image.shape[1], image.shape[0]))
                if registration_lock is not None:
                    try:
                        image, registration_evidence = apply_registered_game_window(
                            registration_reference,
                            image,
                            registration_profile["interface_anchor"],
                            registration_lock,
                        )
                        registration_locked_frames += 1
                    except AnchorRegistrationError:
                        registration_lock = None
                if registration_lock is None:
                    registration_searches += 1
                    try:
                        image, registration_evidence = register_from_upper_right_anchor(
                            registration_reference,
                            image,
                            registration_profile["interface_anchor"],
                        )
                        registration_lock = dict(registration_evidence)
                    except AnchorRegistrationError as exc:
                        registration_rejections += 1
                        frame_rejections.append(
                            {
                                "frame_sha256": hashlib.sha256(
                                    f"{video_sha}:{decoded_index - 1}:{timestamp_ms}:{input_decoded_sha}".encode()
                                ).hexdigest(),
                                "timestamp_ms": timestamp_ms,
                                "reason": "INTERFACE_REGISTRATION_REJECTED",
                                "detail": str(exc),
                            }
                        )
                        continue
                transform = {
                    "scale": registration_evidence["scale"],
                    "input_size": registration_evidence["input_size"],
                    "game_window": registration_evidence["game_window"],
                    "anchor_region": registration_evidence["anchor_region"],
                }
                transform_sha = hashlib.sha256(
                    json.dumps(
                        transform, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest()
                registration_transforms.setdefault(transform_sha, transform)
            elif tuple(image.shape[:2]) != (profile.height, profile.width):
                raise AutonomousVideoError(
                    "video dimensions do not match profile and no anchor registration is configured"
                )
            decoded_sha = hashlib.sha256(image.tobytes()).hexdigest()
            frame_sha = hashlib.sha256(
                f"{video_sha}:{decoded_index - 1}:{timestamp_ms}:{input_decoded_sha}".encode()
            ).hexdigest()
            hands = observe_frame(image, rank_bank, profile)
            played = observe_played_cards(
                image,
                rank_bank,
                suit_bank,
                profile,
                geometry_bank=geometry_bank,
                visible_hand_cards=hands["cards"],
            )
            current_played_state = _played_region_state(played)
            new_play_events = _new_server_play_events(
                previous_played_state,
                current_played_state,
                timestamp_ms=timestamp_ms,
                source_frame_index=decoded_index - 1,
                frame_sha256=frame_sha,
                input_decoded_pixel_sha256=input_decoded_sha,
                observer_status=str(played["status"]),
                first_event_number=len(server_play_events) + 1,
            )
            server_play_events.extend(new_play_events)
            if len(server_play_events) > MAX_PLAYED_EVENT_SNAPSHOTS:
                raise AutonomousVideoError(
                    "played-card server event count exceeds the bound"
                )
            previous_played_state = current_played_state
            layout_geometry = played.get("layout_geometry")
            if layout_geometry is None:
                played_layout_rejected_frames += 1
            else:
                played_layout_proven_frames += 1
                played_layout_geometry_shas.add(layout_geometry["geometry_sha256"])
                played_layout_transform_shas.add(layout_geometry["transform_sha256"])
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
                    "source_frame_index": decoded_index - 1,
                    "input_decoded_pixel_sha256": input_decoded_sha,
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
        expected_card_scale_policy_sha256=card_scale_policy["policy_sha256"],
    )
    played_event_snapshots = _write_played_event_snapshots(
        video_file,
        target,
        server_play_events,
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
            "frame_size": {"width": profile.width, "height": profile.height},
            "card_pixel_scale_source": (
                "EXACT_VERIFIED_PROFILE_PLUS_LIVE_REVIEWED_CARDBACK_WIDTH"
            ),
        },
        "registration": {
            "mode": (
                "UPPER_RIGHT_ANCHOR"
                if registration_profile is not None
                else "EXACT_PROFILE_DIMENSIONS"
            ),
            "search_count": registration_searches,
            "locked_frame_count": registration_locked_frames,
            "rejected_frame_count": registration_rejections,
            "input_sizes": [
                {"width": width, "height": height}
                for width, height in sorted(registration_input_sizes)
            ],
            "transforms": [
                {"transform_sha256": digest, **registration_transforms[digest]}
                for digest in sorted(registration_transforms)
            ],
        },
        "sampling": {
            "sample_ms": sample_ms,
            "sampled_frame_count": sampled,
            "direct_visual_frame_count": len(visual_frames),
            "policy": "10_HZ_EVENT_CAPTURE_WITH_UNKNOWN_PLAYED_REGION_FALLBACK",
        },
        "played_layout": {
            "version": TABLE_GEOMETRY_VERSION,
            "coordinate_source": "LIVE_TABLE_SEAT_LANDMARKS",
            "proven_frame_count": played_layout_proven_frames,
            "rejected_frame_count": played_layout_rejected_frames,
            "geometry_sha256_count": len(played_layout_geometry_shas),
            "transform_sha256_count": len(played_layout_transform_shas),
            "uses_fixed_screen_center": False,
            "geometry_bank_sha256": geometry_bank["bank_sha256"],
            "card_scale_policy_version": CARD_SCALE_POLICY_VERSION,
            "card_scale_policy_sha256": card_scale_policy["policy_sha256"],
            "responsive_policy": "RECOMPUTE_LIVE_SEAT_AXES_EVERY_FRAME",
            "unproven_geometry_action": "REVIEW_NO_PLAYED_CLAIM",
        },
        "marker": {
            "version": markers["version"],
            "accepted_frame_count": len(markers["accepted_frames"]),
            "rejected_frames": markers["rejected_frames"],
        },
        "frame_rejections": frame_rejections,
        "server_play_events": server_play_events,
        "reconstruction": reconstruction,
        "played_event_snapshots": played_event_snapshots,
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
    parser.add_argument("--sample-ms", type=int, default=100)
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
