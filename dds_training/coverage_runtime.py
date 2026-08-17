from __future__ import annotations

"""Dependency-free runtime coverage collector used by DDS self-tests.

When ``DDS_COVERAGE_ROOT`` and ``DDS_COVERAGE_DIR`` are present, sitecustomize
activates this collector in every Python process.  Each process writes a unique
JSON fragment at exit; the test runner merges parent and child fragments.
"""

import atexit
import json
import os
import sys
import threading
from collections import defaultdict
from pathlib import Path
from types import FrameType
from typing import Callable

_ACTIVE = False
_ROOT: Path | None = None
_OUT_DIR: Path | None = None
_LINES: dict[str, set[int]] = defaultdict(set)
_ARCS: dict[str, set[tuple[int, int]]] = defaultdict(set)
_LAST_LINE: dict[int, tuple[str, int]] = {}


def _relative(filename: str) -> str | None:
    if _ROOT is None:
        return None
    try:
        path = Path(filename).resolve()
        relative = path.relative_to(_ROOT)
    except (OSError, ValueError):
        return None
    if any(part in {".venv", ".build", ".tools", ".wheel-cache", "__pycache__", "work"} for part in relative.parts):
        return None
    if path.suffix != ".py":
        return None
    return relative.as_posix()


def _trace(frame: FrameType, event: str, arg) -> Callable | None:
    relative = _relative(frame.f_code.co_filename)
    if relative is None:
        return _trace
    frame_id = id(frame)
    if event == "call":
        _LAST_LINE[frame_id] = (relative, -1)
        return _trace
    if event == "line":
        line = int(frame.f_lineno)
        previous = _LAST_LINE.get(frame_id)
        _LINES[relative].add(line)
        if previous is not None and previous[0] == relative:
            _ARCS[relative].add((previous[1], line))
        _LAST_LINE[frame_id] = (relative, line)
        return _trace
    if event in {"return", "exception"}:
        previous = _LAST_LINE.pop(frame_id, None)
        if previous is not None and previous[0] == relative:
            _ARCS[relative].add((previous[1], -1))
    return _trace


def _write_fragment() -> None:
    if _OUT_DIR is None or _ROOT is None:
        return
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "dds-runtime-coverage-fragment-v1",
        "pid": os.getpid(),
        "root": str(_ROOT),
        "lines": {name: sorted(values) for name, values in sorted(_LINES.items())},
        "arcs": {
            name: [[start, end] for start, end in sorted(values)]
            for name, values in sorted(_ARCS.items())
        },
    }
    target = _OUT_DIR / f"coverage-{os.getpid()}.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    temporary.replace(target)


def activate_from_environment() -> bool:
    global _ACTIVE, _ROOT, _OUT_DIR
    if _ACTIVE:
        return True
    root = os.environ.get("DDS_COVERAGE_ROOT")
    out_dir = os.environ.get("DDS_COVERAGE_DIR")
    if not root or not out_dir:
        return False
    _ROOT = Path(root).resolve()
    _OUT_DIR = Path(out_dir).resolve()
    _ACTIVE = True
    sys.settrace(_trace)
    threading.settrace(_trace)
    atexit.register(_write_fragment)
    return True
