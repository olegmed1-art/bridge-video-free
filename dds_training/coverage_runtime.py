from __future__ import annotations

"""Dependency-free runtime coverage collector used by DDS self-tests.

When ``DDS_COVERAGE_ROOT`` and ``DDS_COVERAGE_DIR`` are present, sitecustomize
activates this collector in every Python process. Each process writes a unique
JSON fragment at exit; the test runner merges parent and child fragments.

When ``DDS_COVERAGE_MANIFEST`` and ``DDS_COVERAGE_SUITE`` are also supplied, the
collector traces only production modules mapped to that suite. Excluded frames
are rejected at the call boundary, so the collector does not line-trace the test
harness, stdlib, or unrelated production modules.
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
_INCLUDED_MODULES: set[str] | None = None
_LINES: dict[str, set[int]] = defaultdict(set)
_ARCS: dict[str, set[tuple[int, int]]] = defaultdict(set)
_LAST_LINE: dict[int, tuple[str, int]] = {}


def _load_included_modules() -> set[str] | None:
    manifest_text = os.environ.get("DDS_COVERAGE_MANIFEST")
    suite = os.environ.get("DDS_COVERAGE_SUITE")
    if not manifest_text and not suite:
        return None
    if not manifest_text or not suite:
        raise RuntimeError("DDS coverage manifest and suite must be supplied together")
    manifest_path = Path(manifest_text).resolve()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    tests = data.get("tests", [])
    suite_ids = {
        str(row.get("id"))
        for row in tests
        if isinstance(row, dict) and row.get("suite") == suite
    }
    if not suite_ids:
        raise RuntimeError(f"DDS coverage suite has no registered tests: {suite!r}")
    mapping = data.get("coverage", {}).get("module_tests", {})
    included = {
        str(module)
        for module, test_ids in mapping.items()
        if suite_ids.intersection(str(test_id) for test_id in test_ids)
    }
    if not included:
        raise RuntimeError(f"DDS coverage suite maps no production modules: {suite!r}")
    return included


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
    relative_text = relative.as_posix()
    if _INCLUDED_MODULES is not None and relative_text not in _INCLUDED_MODULES:
        return None
    return relative_text


def _local_trace(frame: FrameType, event: str, arg) -> Callable | None:
    frame_id = id(frame)
    previous = _LAST_LINE.get(frame_id)
    if previous is None:
        return None
    relative = previous[0]
    if event == "line":
        line = int(frame.f_lineno)
        _LINES[relative].add(line)
        _ARCS[relative].add((previous[1], line))
        _LAST_LINE[frame_id] = (relative, line)
        return _local_trace
    if event == "exception":
        # A Python exception event is not necessarily frame termination; callers may
        # catch it in the same frame. Keep the local tracer attached so coverage after
        # the handler is not silently lost.
        _ARCS[relative].add((previous[1], -1))
        return _local_trace
    if event == "return":
        _ARCS[relative].add((previous[1], -1))
        _LAST_LINE.pop(frame_id, None)
        return None
    return _local_trace


def _global_trace(frame: FrameType, event: str, arg) -> Callable | None:
    # CPython calls the global trace function for new frames. Returning None here
    # prevents all subsequent line events in an excluded frame, which is essential
    # for keeping measured coverage inexpensive enough for the 10k-corpus tests.
    if event != "call":
        return None
    relative = _relative(frame.f_code.co_filename)
    if relative is None:
        return None
    _LAST_LINE[id(frame)] = (relative, -1)
    return _local_trace


def _write_fragment() -> None:
    if _OUT_DIR is None or _ROOT is None:
        return
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "dds-runtime-coverage-fragment-v1",
        "pid": os.getpid(),
        "root": str(_ROOT),
        "included_modules": sorted(_INCLUDED_MODULES) if _INCLUDED_MODULES is not None else None,
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
    global _ACTIVE, _ROOT, _OUT_DIR, _INCLUDED_MODULES
    if _ACTIVE:
        return True
    root = os.environ.get("DDS_COVERAGE_ROOT")
    out_dir = os.environ.get("DDS_COVERAGE_DIR")
    if not root or not out_dir:
        return False
    _ROOT = Path(root).resolve()
    _OUT_DIR = Path(out_dir).resolve()
    _INCLUDED_MODULES = _load_included_modules()
    _ACTIVE = True
    sys.settrace(_global_trace)
    threading.settrace(_global_trace)
    atexit.register(_write_fragment)
    return True
