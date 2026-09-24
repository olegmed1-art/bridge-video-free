"""Fast local dependency checks for the resident universal-video worker.

The preflight is intentionally cheap and runs before a job can download or
transcode media. Missing deterministic local dependencies are terminal
infrastructure failures; the spool worker already archives such jobs once and
does not retry them automatically.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path


class VideoRuntimeUnavailable(RuntimeError):
    pass


class VideoInputUnavailable(RuntimeError):
    """A bounded source-media failure observed before ASR or frame extraction."""

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


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


def validate_staged_video(path: Path) -> dict[str, object]:
    """Prove the freshly staged object is an audio-bearing media container.

    This gate deliberately precedes ``run_job``: a Drive object may have the
    right byte count and checksum yet still be a corrupt container or a video
    without an audio track. No model is loaded and no heavy media work starts
    on failure.
    """

    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
            raise VideoInputUnavailable("UV_MEDIA_INPUT_UNAVAILABLE")
    except OSError as exc:
        raise VideoInputUnavailable("UV_MEDIA_INPUT_UNAVAILABLE") from exc
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration,size,format_name:stream=codec_type,codec_name",
                "-of", "json", str(path),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VideoInputUnavailable("UV_MEDIA_PROBE_FAILED") from exc
    if proc.returncode:
        raise VideoInputUnavailable("UV_MEDIA_PROBE_FAILED")
    try:
        report = json.loads(proc.stdout)
        media = report.get("format") if isinstance(report, dict) else None
        streams = report.get("streams") if isinstance(report, dict) else None
        duration = float((media or {}).get("duration") or 0)
        reported_size = int((media or {}).get("size") or 0)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise VideoInputUnavailable("UV_MEDIA_PROBE_FAILED") from exc
    if duration <= 0 or reported_size != path.stat().st_size:
        raise VideoInputUnavailable("UV_MEDIA_PROBE_FAILED")
    if not any(isinstance(stream, dict) and stream.get("codec_type") == "audio" for stream in streams or []):
        raise VideoInputUnavailable("UV_MEDIA_AUDIO_TRACK_MISSING")
    return {
        "duration_seconds": duration,
        "size_bytes": reported_size,
        "format_name": (media or {}).get("format_name"),
    }


__all__ = [
    "VideoInputUnavailable", "VideoRuntimeUnavailable", "validate_staged_video",
    "validate_video_runtime",
]
