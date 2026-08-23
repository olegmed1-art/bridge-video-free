"""Resident sidecar worker for bounded universal-video jobs.

The worker watches a root-owned/local spool and never accepts shell commands.
It is intentionally separate from assistant-lab.service so enabling it does not
interrupt the proven DDS3 resident worker.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from .runner import run_job


def _dirs(root: Path) -> dict[str, Path]:
    out = {name: root / name for name in ("inbox", "running", "done", "failed", "results")}
    for path in out.values():
        path.mkdir(parents=True, exist_ok=True)
    return out


def process_one(spool_root: Path) -> bool:
    paths = _dirs(spool_root)
    candidates = sorted(paths["inbox"].glob("*.json"), key=lambda p: (p.stat().st_mtime, p.name))
    if not candidates:
        return False
    source = candidates[0]
    claimed = paths["running"] / source.name
    try:
        source.rename(claimed)
    except FileNotFoundError:
        return False
    try:
        payload = json.loads(claimed.read_text(encoding="utf-8"))
        result = run_job(payload, paths["results"])
        receipt = paths["done"] / source.name
        receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        claimed.unlink(missing_ok=True)
    except Exception as exc:
        failure = {
            "status": "FAILED",
            "job_file": source.name,
            "error_type": type(exc).__name__,
            "error": str(exc)[:4000],
        }
        (paths["failed"] / source.name).write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
        claimed.unlink(missing_ok=True)
    return True


def run_forever(spool_root: Path, poll_seconds: float) -> None:
    while True:
        if process_one(spool_root):
            continue
        time.sleep(poll_seconds)


def main() -> None:
    root = Path(os.getenv("UNIVERSAL_VIDEO_SPOOL_ROOT", "/opt/bridge-school/universal-video/spool"))
    poll = max(1.0, float(os.getenv("UNIVERSAL_VIDEO_POLL_SECONDS", "2")))
    run_forever(root, poll)


if __name__ == "__main__":
    main()
