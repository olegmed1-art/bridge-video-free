#!/usr/bin/env python3
"""Run the reviewed Bridgit visible-hand observer on bounded frame files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any

from bridge_vision.bridgit_visible_hand_observer import (
    OBSERVER_VERSION,
    VisibleHandObserverError,
    build_rank_bank,
    decode_frame,
    observe_frame,
    parse_profile,
)

JOB_SCHEMA = "bridgit-visible-hand-observer-job/v1"
RECEIPT_SCHEMA = "bridgit-visible-hand-observer-receipt/v1"
MAX_PROFILE_BYTES = 1024 * 1024
MAX_JOB_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_FRAMES = 256
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise VisibleHandObserverError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _inside(root: Path, raw: str | Path, field: str) -> Path:
    path = Path(raw)
    candidate = path if path.is_absolute() else root / path
    cursor = candidate
    while cursor != root and cursor != cursor.parent:
        if cursor.exists() and cursor.is_symlink():
            raise VisibleHandObserverError(f"{field} must not traverse a symlink")
        cursor = cursor.parent
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise VisibleHandObserverError(f"{field} escapes job_root") from exc
    return resolved


def _read(path: Path, limit: int, field: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise VisibleHandObserverError(f"{field} is unavailable") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise VisibleHandObserverError(f"{field} violates the file bound")
        chunks = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > limit:
            raise VisibleHandObserverError(f"{field} violates the file bound")
        return payload
    finally:
        os.close(descriptor)


def _json(path: Path, limit: int, field: str) -> dict[str, Any]:
    try:
        raw = json.loads(
            _read(path, limit, field).decode("utf-8"),
            object_pairs_hook=_reject_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VisibleHandObserverError(f"{field} is not valid UTF-8 JSON") from exc
    if not isinstance(raw, dict):
        raise VisibleHandObserverError(f"{field} must be an object")
    return raw


def _write_atomic(path: Path, payload: bytes) -> None:
    if len(payload) > MAX_OUTPUT_BYTES:
        raise VisibleHandObserverError("receipt exceeds the byte bound")
    if path.exists() and path.is_symlink():
        raise VisibleHandObserverError("output must not be a symlink")
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


def run(
    job_root: Path, profile_path: Path, job_path: Path, output_path: Path
) -> dict[str, Any]:
    root = job_root.resolve()
    if root == Path("/") or not root.is_dir() or job_root.is_symlink():
        raise VisibleHandObserverError("job_root must be a non-root real directory")
    profile_file = _inside(root, profile_path, "profile")
    job_file = _inside(root, job_path, "job")
    target = _inside(root, output_path, "output")
    if target in {profile_file, job_file} or not target.parent.is_dir():
        raise VisibleHandObserverError("output path is unsafe")

    profile = parse_profile(_json(profile_file, MAX_PROFILE_BYTES, "profile"))
    references = {}
    for reference_id, (raw_path, expected_sha) in profile.references.items():
        source = _inside(root, raw_path, "reference")
        payload = _read(source, 32 * 1024 * 1024, "reference")
        if hashlib.sha256(payload).hexdigest() != expected_sha:
            raise VisibleHandObserverError("reference frame hash mismatch")
        references[reference_id] = decode_frame(payload, profile)
    bank = build_rank_bank(profile, references)

    job = _json(job_file, MAX_JOB_BYTES, "job")
    if job.get("schema") != JOB_SCHEMA:
        raise VisibleHandObserverError("unsupported observer job schema")
    if set(job) != {"schema", "frames"}:
        raise VisibleHandObserverError("observer job contains unsupported fields")
    raw_frames = job.get("frames")
    if not isinstance(raw_frames, list) or not 1 <= len(raw_frames) <= MAX_FRAMES:
        raise VisibleHandObserverError("frame count is outside the bound")

    frames = []
    for index, raw in enumerate(raw_frames):
        if not isinstance(raw, dict):
            raise VisibleHandObserverError("frame entry must be an object")
        if set(raw) != {"path", "frame_sha256", "timestamp_ms"}:
            raise VisibleHandObserverError("frame entry contains unsupported fields")
        source = _inside(root, str(raw.get("path") or ""), "frame")
        expected_sha = str(raw.get("frame_sha256") or "")
        if not _SHA256.fullmatch(expected_sha):
            raise VisibleHandObserverError("frame hash is invalid")
        try:
            timestamp_ms = int(raw.get("timestamp_ms"))
        except (TypeError, ValueError, OverflowError) as exc:
            raise VisibleHandObserverError("timestamp_ms is invalid") from exc
        if timestamp_ms < 0 or isinstance(raw.get("timestamp_ms"), bool):
            raise VisibleHandObserverError("timestamp_ms is invalid")
        payload = _read(source, 32 * 1024 * 1024, f"frame[{index}]")
        actual_sha = hashlib.sha256(payload).hexdigest()
        if actual_sha != expected_sha:
            raise VisibleHandObserverError("frame hash mismatch")
        image = decode_frame(payload, profile)
        observation = observe_frame(image, bank, profile)
        observation.update(
            {
                "frame_sha256": actual_sha,
                "decoded_pixel_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
                "timestamp_ms": timestamp_ms,
            }
        )
        frames.append(observation)

    receipt = {
        "schema": RECEIPT_SCHEMA,
        "observer_version": OBSERVER_VERSION,
        "result_scope": "SHADOW_ONLY",
        "profile_id": profile.profile_id,
        "profile_sha256": profile.profile_sha256,
        "profile_verification_sha256": profile.verification_sha256,
        "review_sheet_sha256": profile.review_sheet_sha256,
        "frames": frames,
        "hidden_hand_inference_used": False,
        "deck_complement_used": False,
        "canonical_promotion_allowed": False,
    }
    receipt["receipt_sha256"] = hashlib.sha256(
        json.dumps(
            receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    encoded = (
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True).encode(
            "utf-8"
        )
        + b"\n"
    )
    _write_atomic(target, encoded)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Observe visible Bridgit hands in bounded frames"
    )
    parser.add_argument("--job-root", required=True, type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--job", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        receipt = run(args.job_root, args.profile, args.job, args.output)
    except (OSError, VisibleHandObserverError, RuntimeError) as exc:
        print(f"VISIBLE_HAND_OBSERVER_REJECTED: {exc}", file=os.sys.stderr)
        return 2
    observed = sum(len(frame["cards"]) for frame in receipt["frames"])
    print(
        f"SHADOW_VISIBLE_HAND_OBSERVER frames={len(receipt['frames'])} cards={observed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
