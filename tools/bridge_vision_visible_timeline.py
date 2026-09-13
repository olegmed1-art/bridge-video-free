#!/usr/bin/env python3
"""Create a sealed shadow receipt from source-bound visible-card observations."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any

from bridge_vision.bridgit_visible_timeline import (
    VisibleTimelineError,
    fuse_visible_timeline,
)

MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise VisibleTimelineError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _inside(root: Path, raw: Path, field: str) -> Path:
    candidate = raw if raw.is_absolute() else root / raw
    if candidate.is_symlink():
        raise VisibleTimelineError(f"{field} must not be a symlink")
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise VisibleTimelineError(f"{field} escapes job_root") from exc
    return resolved


def _read_bounded(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise VisibleTimelineError("input is unavailable") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise VisibleTimelineError("input must be a regular file")
        if info.st_size > MAX_INPUT_BYTES:
            raise VisibleTimelineError("input exceeds the byte limit")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read(MAX_INPUT_BYTES + 1)
        if len(payload) > MAX_INPUT_BYTES:
            raise VisibleTimelineError("input exceeds the byte limit")
        return payload
    finally:
        os.close(descriptor)


def _write_atomic(path: Path, payload: bytes) -> None:
    if len(payload) > MAX_OUTPUT_BYTES:
        raise VisibleTimelineError("receipt exceeds the byte limit")
    if path.exists() and path.is_symlink():
        raise VisibleTimelineError("output must not be a symlink")
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise VisibleTimelineError("output parent must be a real directory")
    temporary: str | None = None
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


def run(job_root: Path, input_path: Path, output_path: Path) -> dict[str, Any]:
    root = job_root.resolve()
    if root == Path("/") or not root.is_dir() or job_root.is_symlink():
        raise VisibleTimelineError("job_root must be a non-root real directory")
    source = _inside(root, input_path, "input")
    target = _inside(root, output_path, "output")
    if source == target:
        raise VisibleTimelineError("input and output must differ")
    try:
        request = json.loads(
            _read_bounded(source), object_pairs_hook=_reject_duplicates
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VisibleTimelineError("input is not valid UTF-8 JSON") from exc
    if not isinstance(request, dict):
        raise VisibleTimelineError("input root must be an object")
    unknown = set(request) - {
        "observations",
        "min_independent_frames",
        "min_confidence",
    }
    if unknown:
        raise VisibleTimelineError("input contains unsupported fields")
    receipt = fuse_visible_timeline(
        request.get("observations"),
        min_independent_frames=request.get("min_independent_frames", 2),
        min_confidence=request.get("min_confidence", 0.98),
    )
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
        description="Fuse visible Bridgit cards into a shadow temporal receipt"
    )
    parser.add_argument("--job-root", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        receipt = run(args.job_root, args.input, args.output)
    except (VisibleTimelineError, OSError) as exc:
        print(f"VISIBLE_TIMELINE_REJECTED: {exc}", file=os.sys.stderr)
        return 2
    print(receipt["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
