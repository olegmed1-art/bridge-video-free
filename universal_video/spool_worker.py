"""Resident sidecar worker for bounded universal-video jobs.

The worker watches a local spool and never accepts shell commands. It is
intentionally separate from assistant-lab.service so enabling it does not
interrupt the proven DDS3 resident worker.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .runner import run_job


def _dirs(root: Path) -> dict[str, Path]:
    out = {name: root / name for name in ("inbox", "running", "done", "failed", "results")}
    for path in out.values():
        path.mkdir(parents=True, exist_ok=True)
    return out


def recover_orphaned_jobs(spool_root: Path) -> dict[str, int]:
    """Recover jobs left in running/ by a terminated single resident worker.

    universal-video.service owns this spool and runs one worker process. On a
    fresh process start, any pre-existing running/*.json file is therefore an
    orphan from a previous process. Identical duplicate inbox payloads are
    deduplicated; conflicting payloads are quarantined rather than overwritten.
    """

    paths = _dirs(spool_root)
    recovered = 0
    deduplicated = 0
    conflicts = 0
    for claimed in sorted(paths["running"].glob("*.json")):
        destination = paths["inbox"] / claimed.name
        if not destination.exists():
            claimed.rename(destination)
            recovered += 1
            continue
        try:
            identical = claimed.read_bytes() == destination.read_bytes()
        except OSError:
            identical = False
        if identical:
            claimed.unlink(missing_ok=True)
            deduplicated += 1
            continue

        stamp = int(time.time())
        payload_path = paths["failed"] / f"{claimed.stem}.recovery-conflict-{stamp}.payload.json"
        receipt_path = paths["failed"] / f"{claimed.stem}.recovery-conflict-{stamp}.receipt.json"
        claimed.rename(payload_path)
        receipt_path.write_text(
            json.dumps(
                {
                    "status": "FAILED",
                    "error_type": "RECOVERY_CONFLICT",
                    "job_file": claimed.name,
                    "quarantined_payload": payload_path.name,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        conflicts += 1
    return {
        "recovered": recovered,
        "deduplicated": deduplicated,
        "conflicts": conflicts,
    }


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
        (paths["failed"] / source.name).write_text(
            json.dumps(failure, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        claimed.unlink(missing_ok=True)
    return True


def run_forever(spool_root: Path, poll_seconds: float) -> None:
    recovery = recover_orphaned_jobs(spool_root)
    if any(recovery.values()):
        print(json.dumps({"event": "spool_recovery", **recovery}, sort_keys=True), flush=True)
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
