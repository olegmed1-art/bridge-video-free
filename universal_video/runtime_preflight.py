"""Fast local dependency checks for the resident universal-video worker.

The preflight is intentionally cheap and runs before a job can download or
transcode media. Missing deterministic local dependencies are terminal
infrastructure failures; the spool worker already archives such jobs once and
does not retry them automatically.
"""
from __future__ import annotations

import importlib.util
import shutil


class VideoRuntimeUnavailable(RuntimeError):
    pass


def validate_video_runtime() -> dict[str, str]:
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise VideoRuntimeUnavailable("VIDEO_RUNTIME_MISSING_TOOL:" + ",".join(missing))
    if importlib.util.find_spec("faster_whisper") is None:
        raise VideoRuntimeUnavailable("VIDEO_RUNTIME_MISSING_ASR:faster_whisper")
    return {
        "ffmpeg": str(shutil.which("ffmpeg")),
        "ffprobe": str(shutil.which("ffprobe")),
        "asr": "faster_whisper",
    }


__all__ = ["VideoRuntimeUnavailable", "validate_video_runtime"]
