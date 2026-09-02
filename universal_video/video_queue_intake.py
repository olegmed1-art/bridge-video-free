"""Bounded command-line intake for one Drive folder batch."""
from __future__ import annotations

import argparse
import json
import stat
from pathlib import Path

from .video_queue import VideoQueueError, batch_status, enqueue_drive_request

MAX_INTAKE_BYTES = 16 * 1024


def _read_request(path: Path) -> dict:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise VideoQueueError("intake request must be a regular file")
    if info.st_size > MAX_INTAKE_BYTES:
        raise VideoQueueError("intake request exceeds bounded size")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VideoQueueError("invalid intake request JSON") from exc
    if not isinstance(value, dict):
        raise VideoQueueError("intake request must be an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    enqueue = commands.add_parser("enqueue")
    enqueue.add_argument("request", type=Path)
    status = commands.add_parser("status")
    status.add_argument("request_key")
    args = parser.parse_args()
    try:
        if args.command == "enqueue":
            result = enqueue_drive_request(_read_request(args.request))
        else:
            result = batch_status(args.request_key)
            if result is None:
                result = {
                    "schema": "universal-video-batch-status-v1",
                    "request_key": args.request_key,
                    "status": "MISSING",
                }
    except Exception as exc:
        code = "UV_BATCH_INTAKE_FAILED"
        if isinstance(exc, VideoQueueError):
            code = "UV_BATCH_INTAKE_INVALID"
        print(json.dumps({
            "schema": "universal-video-batch-intake-error-v1",
            "status": "FAILED",
            "error_code": code,
            "error_type": type(exc).__name__,
        }, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
