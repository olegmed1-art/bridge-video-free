"""CPU-first universal video runner.

This module performs deterministic/heavy media work locally and emits compact
artifacts for later semantic/domain analysis. It intentionally contains no
student-specific logic and no bridge teaching methodology.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

from .contract import (
    CONTRACT_VERSION,
    MAX_FRAME_INTERVAL_SECONDS,
    MAX_SOURCE_BYTES,
    MAX_VIDEO_SECONDS,
    MIN_FRAME_INTERVAL_SECONDS,
    MIN_SOURCE_BYTES,
    VideoJob,
    canonical_job_hash,
    validate_from_env,
)
from .drive_adapter import access_token, download_file, file_metadata
from .profiles import resolve_profile

WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+")
DEFAULT_SERVER_MAX_SOURCE_BYTES = 16 * 1024**3
_MODEL = None


def _run(args: list[str], *, timeout: int = 3600) -> str:
    proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if proc.returncode:
        tail = (proc.stderr or "")[-3000:]
        raise RuntimeError(f"subprocess failed rc={proc.returncode}: {tail}")
    return proc.stdout


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _words(text: str) -> list[str]:
    return WORD_RE.findall((text or "").lower())


def _similarity(a: str, b: str) -> float:
    aa, bb = _words(a), _words(b)
    if not aa or not bb:
        return 0.0
    ca, cb = Counter(aa), Counter(bb)
    common = sum((ca & cb).values())
    precision = common / len(bb)
    recall = common / len(aa)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _repetition_ratio(text: str) -> float:
    words = _words(text)
    if len(words) < 8:
        return 0.0
    counts = Counter(words)
    repeated = sum(count - 1 for count in counts.values() if count > 1)
    return repeated / len(words)


def _server_source_limit() -> int:
    raw = os.getenv("UNIVERSAL_VIDEO_MAX_SOURCE_BYTES", str(DEFAULT_SERVER_MAX_SOURCE_BYTES)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("UNIVERSAL_VIDEO_MAX_SOURCE_BYTES must be an integer") from exc
    if not MIN_SOURCE_BYTES <= value <= MAX_SOURCE_BYTES:
        raise RuntimeError("UNIVERSAL_VIDEO_MAX_SOURCE_BYTES outside hard safety range")
    return value


def _effective_source_limit(job: VideoJob) -> int:
    server_limit = _server_source_limit()
    requested = int(job.options.get("max_source_bytes") or server_limit)
    return min(server_limit, requested)


def _enforce_media_bounds(media: dict[str, Any], job: VideoJob, source_limit: int) -> None:
    duration = float(media.get("duration_seconds") or 0)
    if duration <= 0:
        raise RuntimeError("media duration is unavailable")
    if duration > MAX_VIDEO_SECONDS + 0.01:
        raise RuntimeError("video exceeds universal hard duration limit")
    requested_duration = job.options.get("max_duration_seconds")
    if requested_duration is not None and duration > float(requested_duration) + 0.01:
        raise RuntimeError("video exceeds job max_duration_seconds")
    size_bytes = int(media.get("size_bytes") or 0)
    if size_bytes <= 0:
        raise RuntimeError("media size is unavailable")
    if size_bytes > source_limit:
        raise RuntimeError("video exceeds configured source-size limit")


def _qc_summary(transcript: list[dict[str, Any]], qc: list[dict[str, Any]]) -> tuple[bool, int, int]:
    failed = sum(not item.get("ok", False) for item in qc)
    allowed_failed = math.floor(len(qc) * 0.20)
    passed = bool(transcript) and bool(qc) and failed <= allowed_failed
    return passed, failed, allowed_failed


def _inspect_source(job: VideoJob, *, max_source_bytes: int) -> dict[str, Any]:
    """Inspect source identity before allowing a COMPLETED result to be reused.

    Local sources use a full SHA-256. Binary Drive files normally expose a
    content checksum in Drive metadata, which makes a cheap pre-download reuse
    decision possible. If Drive supplies no content checksum, reuse is disabled
    for safety and the job is recomputed.
    """

    if job.source["kind"] == "local_path":
        path = Path(job.source["path"])
        if not path.is_file():
            raise RuntimeError("local video source does not exist")
        size = path.stat().st_size
        if size <= 0:
            raise RuntimeError("local video source is empty")
        if size > max_source_bytes:
            raise RuntimeError("local video exceeds configured source-size limit")
        sha256 = _sha256(path)
        fingerprint_payload = {
            "kind": "local_path",
            "size_bytes": size,
            "sha256": sha256,
        }
        return {
            "kind": "local_path",
            "fingerprint": _fingerprint(fingerprint_payload),
            "fingerprint_basis": "sha256+size",
            "reuse_safe": True,
            "known_sha256": sha256,
            "path": path,
        }

    token = access_token()
    meta = file_metadata(job.source["file_id"], token)
    mime = str(meta.get("mimeType") or "")
    if mime.startswith("application/vnd.google-apps."):
        raise RuntimeError("native Google Workspace files are not video sources")
    try:
        size = int(meta.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    if size > max_source_bytes:
        raise RuntimeError("Google Drive video exceeds configured source-size limit")

    checksum_key = None
    checksum_value = None
    for key in ("sha256Checksum", "md5Checksum", "sha1Checksum"):
        value = str(meta.get(key) or "").strip().lower()
        if value:
            checksum_key = key
            checksum_value = value
            break

    if checksum_key:
        fingerprint_payload = {
            "kind": "google_drive",
            "file_id": job.source["file_id"],
            "size_bytes": size,
            "checksum_kind": checksum_key,
            "checksum": checksum_value,
        }
        reuse_safe = True
        basis = f"{checksum_key}+size+file_id"
    else:
        # Keep a diagnostic fingerprint but do not use it for idempotent reuse.
        fingerprint_payload = {
            "kind": "google_drive",
            "file_id": job.source["file_id"],
            "size_bytes": size,
            "modifiedTime": meta.get("modifiedTime"),
        }
        reuse_safe = False
        basis = "metadata-only-not-reuse-safe"

    return {
        "kind": "google_drive",
        "fingerprint": _fingerprint(fingerprint_payload),
        "fingerprint_basis": basis,
        "reuse_safe": reuse_safe,
        "token": token,
        "metadata": meta,
    }


def media_probe(path: Path, *, known_sha256: str | None = None) -> dict[str, Any]:
    raw = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size,format_name:stream=index,codec_type,codec_name,width,height,sample_rate,channels",
            "-of",
            "json",
            str(path),
        ],
        timeout=60,
    )
    data = json.loads(raw)
    duration = float((data.get("format") or {}).get("duration") or 0)
    if duration <= 0:
        raise RuntimeError("media duration is unavailable")
    return {
        "duration_seconds": duration,
        "size_bytes": int((data.get("format") or {}).get("size") or path.stat().st_size),
        "format_name": (data.get("format") or {}).get("format_name"),
        "streams": data.get("streams") or [],
        "sha256": known_sha256 or _sha256(path),
    }


def _audio_chunk(video: Path, output: Path, start: float, duration: float) -> None:
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(video),
            "-t",
            f"{duration:.3f}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output),
        ],
        timeout=max(120, int(duration * 3)),
    )


def _load_model():
    global _MODEL
    if _MODEL is None:
        from faster_whisper import WhisperModel

        model_name = os.getenv("UNIVERSAL_VIDEO_WHISPER_MODEL", os.getenv("WHISPER_MODEL", "small"))
        _MODEL = WhisperModel(
            model_name,
            device="cpu",
            compute_type="int8",
            cpu_threads=max(1, int(os.getenv("UNIVERSAL_VIDEO_ASR_THREADS", "8"))),
        )
    return _MODEL


def _asr(
    path: Path,
    *,
    strict: bool = False,
    retry: bool = False,
    initial_prompt: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    model = _load_model()
    kwargs: dict[str, Any] = {
        "language": None,
        "condition_on_previous_text": False,
        "beam_size": 3 if strict else 5,
        "vad_filter": not retry,
    }
    if initial_prompt:
        kwargs["initial_prompt"] = initial_prompt[:2000]
    if strict and not retry:
        kwargs["vad_parameters"] = {
            "threshold": 0.65,
            "min_speech_duration_ms": 300,
            "min_silence_duration_ms": 800,
        }
    segments, info = model.transcribe(str(path), **kwargs)
    out: list[dict[str, Any]] = []
    for segment in segments:
        text = (segment.text or "").strip()
        if text:
            out.append({"start": float(segment.start), "end": float(segment.end), "text": text})
    return out, getattr(info, "language", None)


def _transcribe_chunk(
    audio: Path,
    *,
    initial_prompt: str | None,
) -> tuple[list[dict[str, Any]], str | None, dict[str, Any]]:
    primary, language = _asr(audio, initial_prompt=initial_prompt)
    primary_text = " ".join(x["text"] for x in primary)
    strict, _ = _asr(audio, strict=True, initial_prompt=initial_prompt)
    strict_text = " ".join(x["text"] for x in strict)
    similarity = _similarity(primary_text, strict_text)
    qc = {
        "primary_words": len(_words(primary_text)),
        "strict_words": len(_words(strict_text)),
        "similarity": round(similarity, 4),
        "repetition_ratio": round(_repetition_ratio(primary_text), 4),
        "retry_used": False,
    }
    ok = bool(primary_text) and similarity >= 0.30 and qc["repetition_ratio"] < 0.72
    if not ok:
        retry_segments, _ = _asr(audio, retry=True, initial_prompt=initial_prompt)
        retry_text = " ".join(x["text"] for x in retry_segments)
        retry_similarity = _similarity(primary_text or strict_text, retry_text)
        qc.update(
            {
                "retry_used": True,
                "retry_similarity": round(retry_similarity, 4),
                "retry_words": len(_words(retry_text)),
            }
        )
        candidates = [(primary, primary_text), (strict, strict_text), (retry_segments, retry_text)]
        candidates.sort(
            key=lambda item: (len(_words(item[1])), -_repetition_ratio(item[1])),
            reverse=True,
        )
        primary, primary_text = candidates[0]
        ok = bool(primary_text) and _repetition_ratio(primary_text) < 0.80
    qc["ok"] = ok
    return primary, language, qc


def transcribe(
    video: Path,
    work: Path,
    duration: float,
    *,
    chunk_seconds: int,
    initial_prompt: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    timeline: list[dict[str, Any]] = []
    qcs: list[dict[str, Any]] = []
    languages: Counter[str] = Counter()
    start = 0.0
    index = 0
    overlap = 1.5
    while start < duration:
        end = min(duration, start + chunk_seconds)
        wav = work / f"audio-{index:04d}.wav"
        _audio_chunk(video, wav, start, end - start)
        segments, language, qc = _transcribe_chunk(wav, initial_prompt=initial_prompt)
        if language:
            languages[language] += 1
        for segment in segments:
            timeline.append(
                {
                    "start": round(start + segment["start"], 3),
                    "end": round(min(duration, start + segment["end"]), 3),
                    "text": segment["text"],
                    "chunk": index,
                    "unreliable": not qc["ok"],
                }
            )
        qcs.append({"chunk": index, "start": round(start, 3), "end": round(end, 3), **qc})
        wav.unlink(missing_ok=True)
        if end >= duration:
            break
        start = max(start + 1, end - overlap)
        index += 1
    timeline.sort(key=lambda item: (item["start"], item["end"]))
    language = languages.most_common(1)[0][0] if languages else None
    return timeline, qcs, language


def extract_keyframes(
    video: Path,
    output_dir: Path,
    duration: float,
    *,
    interval_seconds: int = 120,
) -> list[dict[str, Any]]:
    if not MIN_FRAME_INTERVAL_SECONDS <= interval_seconds <= MAX_FRAME_INTERVAL_SECONDS:
        raise RuntimeError("frame interval outside hard safety range")
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamps = {0.0, max(0.0, duration - 0.5)}
    t = float(interval_seconds)
    while t < duration:
        timestamps.add(t)
        t += interval_seconds
    frames: list[dict[str, Any]] = []
    for index, ts in enumerate(sorted(timestamps)):
        path = output_dir / f"frame-{index:04d}-{int(ts):06d}.jpg"
        _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{ts:.3f}",
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(path),
            ],
            timeout=120,
        )
        if path.exists() and path.stat().st_size:
            frames.append({"time": round(ts, 3), "file": path.name, "sha256": _sha256(path)})
    return frames


def _materialize_source(
    job: VideoJob,
    work: Path,
    inspection: dict[str, Any],
    *,
    max_source_bytes: int,
) -> tuple[Path, dict[str, Any], str | None]:
    if job.source["kind"] == "local_path":
        path = inspection["path"]
        return path, {
            "kind": "local_path",
            "path": str(path),
            "fingerprint": inspection["fingerprint"],
            "fingerprint_basis": inspection["fingerprint_basis"],
        }, inspection.get("known_sha256")

    meta = inspection["metadata"]
    token = inspection["token"]
    source_name = str(meta.get("name") or job.source.get("name") or f"drive-{job.source['file_id']}.video")
    safe_name = re.sub(r"[^A-Za-zА-Яа-яЁё0-9._ -]+", "_", Path(source_name).name)[:180] or "source.video"
    path = work / safe_name
    downloaded = download_file(
        job.source["file_id"],
        path,
        token,
        max_bytes=max_source_bytes,
        metadata=meta,
    )
    return path, {
        "kind": "google_drive",
        "file_id": job.source["file_id"],
        "name": downloaded.get("name"),
        "mimeType": downloaded.get("mimeType"),
        "size": downloaded.get("size"),
        "modifiedTime": downloaded.get("modifiedTime"),
        "md5Checksum": downloaded.get("md5Checksum"),
        "sha1Checksum": downloaded.get("sha1Checksum"),
        "sha256Checksum": downloaded.get("sha256Checksum"),
        "fingerprint": inspection["fingerprint"],
        "fingerprint_basis": inspection["fingerprint_basis"],
        "reuse_safe": inspection["reuse_safe"],
    }, str(downloaded.get("_download_sha256") or "") or None


def _prepare_job_dir(
    output_root: Path,
    job: VideoJob,
    *,
    source_fingerprint: str | None = None,
    source_reuse_safe: bool = False,
) -> tuple[Path, str, dict[str, Any] | None]:
    job_hash = canonical_job_hash(job)
    job_dir = output_root / job.job_id
    manifest_path = job_dir / "manifest.json"
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
        if (
            source_reuse_safe
            and source_fingerprint
            and existing
            and existing.get("job_hash") == job_hash
            and existing.get("source_fingerprint") == source_fingerprint
            and existing.get("status") == "COMPLETED"
        ):
            return job_dir, job_hash, existing
    if job_dir.exists():
        shutil.rmtree(job_dir)
    job_dir.mkdir(parents=True, exist_ok=False)
    return job_dir, job_hash, None


def run_job(payload: dict[str, Any], output_root: Path) -> dict[str, Any]:
    started = time.time()
    job = validate_from_env(payload)
    profile = resolve_profile(job.profile)
    output_root.mkdir(parents=True, exist_ok=True)
    source_limit = _effective_source_limit(job)
    inspection = _inspect_source(job, max_source_bytes=source_limit)
    job_dir, job_hash, existing = _prepare_job_dir(
        output_root,
        job,
        source_fingerprint=inspection.get("fingerprint"),
        source_reuse_safe=bool(inspection.get("reuse_safe")),
    )
    if existing is not None:
        return existing
    manifest_path = job_dir / "manifest.json"

    with tempfile.TemporaryDirectory(prefix="universal-video-") as temp:
        work = Path(temp)
        source, source_provenance, known_sha256 = _materialize_source(
            job,
            work,
            inspection,
            max_source_bytes=source_limit,
        )
        media = media_probe(source, known_sha256=known_sha256)
        _enforce_media_bounds(media, job, source_limit)
        chunk_seconds = int(job.options.get("chunk_seconds") or 300)
        initial_prompt = str(job.options.get("initial_prompt") or "").strip() or None
        transcript, qc, language = transcribe(
            source,
            work,
            media["duration_seconds"],
            chunk_seconds=chunk_seconds,
            initial_prompt=initial_prompt,
        )
        transcript_words = sum(len(_words(item["text"])) for item in transcript)
        qc_pass, failed_qc, allowed_failed_qc = _qc_summary(transcript, qc)

        transcript_jsonl = job_dir / "transcript.jsonl"
        transcript_jsonl.write_text(
            "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in transcript),
            encoding="utf-8",
        )
        transcript_txt = job_dir / "transcript.txt"
        transcript_txt.write_text(
            "\n".join(f"[{item['start']:.1f}-{item['end']:.1f}] {item['text']}" for item in transcript),
            encoding="utf-8",
        )
        qc_path = job_dir / "transcript_qc.json"
        qc_path.write_text(json.dumps(qc, ensure_ascii=False, indent=2), encoding="utf-8")

        frames: list[dict[str, Any]] = []
        if "keyframes" in profile.stages:
            frames = extract_keyframes(
                source,
                job_dir / "frames",
                media["duration_seconds"],
                interval_seconds=int(job.options.get("frame_interval_seconds") or 120),
            )

        manifest = {
            "contract": CONTRACT_VERSION,
            "status": "COMPLETED" if qc_pass else "REVIEW",
            "job_id": job.job_id,
            "job_hash": job_hash,
            "source_fingerprint": inspection.get("fingerprint"),
            "source_fingerprint_basis": inspection.get("fingerprint_basis"),
            "source_reuse_safe": bool(inspection.get("reuse_safe")),
            "profile": job.profile,
            "project": job.project,
            "domain_plugin": profile.domain_plugin,
            "planned_stages": list(profile.stages),
            "source": source_provenance,
            "media": media,
            "transcript": {
                "language": language,
                "segments": len(transcript),
                "words": transcript_words,
                "qc_blocks": len(qc),
                "qc_failed": failed_qc,
                "qc_allowed_failed": allowed_failed_qc,
                "qc_pass": qc_pass,
                "jsonl": transcript_jsonl.name,
                "text": transcript_txt.name,
                "qc": qc_path.name,
            },
            "frames": frames,
            "deferred_analysis": [
                stage
                for stage in profile.stages
                if stage
                not in {
                    "media_preflight",
                    "audio_extract",
                    "transcribe",
                    "transcript_qc",
                    "timeline",
                    "keyframes",
                    "package",
                }
            ],
            "metadata": job.metadata,
            "runtime": {
                "elapsed_seconds": round(time.time() - started, 3),
                "hostname": os.uname().nodename,
                "cpu_count": os.cpu_count(),
                "whisper_model": os.getenv(
                    "UNIVERSAL_VIDEO_WHISPER_MODEL",
                    os.getenv("WHISPER_MODEL", "small"),
                ),
                "max_source_bytes": source_limit,
            },
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("job_json", type=Path)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(
            os.getenv(
                "UNIVERSAL_VIDEO_OUTPUT_ROOT",
                "/opt/bridge-school/universal-video/output",
            )
        ),
    )
    args = parser.parse_args()
    payload = json.loads(args.job_json.read_text(encoding="utf-8"))
    result = run_job(payload, args.output_root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
