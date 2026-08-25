"""Fail-closed conformance checks for a Universal Video result bundle.

The check proves only that a bounded technical artifact bundle is internally
consistent at the time it is observed. It deliberately does not claim that
deferred domain analysis ran or that the result has pedagogical value.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
from pathlib import Path
from typing import Any

from .contract import CONTRACT_VERSION
from .profiles import resolve_profile

HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX32 = re.compile(r"^[0-9a-f]{32}$")
WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+")
FRAME_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
RAW_EXTENSIONS = frozenset({".mp4", ".mkv", ".mov", ".avi", ".webm", ".wav", ".mp3", ".m4a", ".flac"})
REQUIRED_TRANSCRIPT_FILES = ("transcript.jsonl", "transcript.txt", "transcript_qc.json")
DEFAULT_MAX_FILE_BYTES = 256 * 1024**2
DEFAULT_MAX_TOTAL_BYTES = 512 * 1024**2
DEFAULT_MAX_FRAMES = 300
RUNNER_IMPLEMENTED_STAGES = frozenset(
    {
        "media_preflight",
        "audio_extract",
        "transcribe",
        "transcript_qc",
        "timeline",
        "keyframes",
        "package",
    }
)
QC_FAILURE_REASONS = (
    "EMPTY_ASR",
    "LOW_CROSS_ATTEMPT_CONSENSUS",
    "LOOP_REPETITION",
    "REPEATED_NONSPEECH_HALLUCINATION",
)


class ResultConformanceError(RuntimeError):
    """Raised when a result bundle cannot support a technical-ready claim."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _safe_regular(path: Path, *, max_bytes: int) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ResultConformanceError(f"missing artifact: {path.name}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ResultConformanceError(f"unsafe artifact: {path.name}")
    if not 0 <= info.st_size <= max_bytes:
        raise ResultConformanceError(f"artifact exceeds byte cap: {path.name}")
    return info


def _read_json(path: Path, *, max_bytes: int) -> Any:
    _safe_regular(path, max_bytes=max_bytes)
    try:
        return _strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResultConformanceError(f"invalid JSON artifact: {path.name}") from exc


def _strict_json_loads(raw: str) -> Any:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> Any:
        raise ValueError(f"non-finite JSON number: {value}")

    try:
        return json.loads(raw, object_pairs_hook=reject_duplicate_keys, parse_constant=reject_constant)
    except ValueError as exc:
        raise json.JSONDecodeError(str(exc), raw, 0) from exc


def _required_hex(value: Any, field: str, pattern: re.Pattern[str] = HEX64) -> str:
    text = str(value or "").strip().lower()
    if not pattern.fullmatch(text):
        raise ResultConformanceError(f"invalid {field}")
    return text


def _positive_number(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ResultConformanceError(f"invalid {field}") from exc
    if not math.isfinite(number) or number <= 0:
        raise ResultConformanceError(f"invalid {field}")
    return number


def _exact_int(value: Any, field: str, *, minimum: int = 0) -> int:
    if type(value) is not int:
        raise ResultConformanceError(f"invalid {field}")
    number = value
    if number < minimum:
        raise ResultConformanceError(f"invalid {field}")
    return number


def _bounded_number(value: Any, field: str, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResultConformanceError(f"invalid {field}")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ResultConformanceError(f"invalid {field}")
    return number


def _validate_qc_attempt(row: dict[str, Any]) -> None:
    primary_words = _exact_int(row.get("primary_words"), "QC primary_words")
    strict_words = _exact_int(row.get("strict_words"), "QC strict_words")
    similarity = _bounded_number(row.get("similarity"), "QC similarity")
    repetition = _bounded_number(row.get("repetition_ratio"), "QC repetition_ratio")
    reasons = row.get("failure_reasons")
    if (
        not isinstance(reasons, list)
        or any(not isinstance(reason, str) or reason not in QC_FAILURE_REASONS for reason in reasons)
        or len(reasons) != len(set(reasons))
    ):
        raise ResultConformanceError("invalid QC failure reasons")
    if row["no_speech"]:
        if not (
            primary_words == 0
            and strict_words == 0
            and similarity == 1.0
            and repetition == 0.0
            and row["retry_used"] is False
            and row["ok"] is True
            and row["critical"] is False
            and reasons == []
            and row["nonspeech_hallucination"] is False
        ):
            raise ResultConformanceError("contradictory no-speech QC evidence")
        for field in ("primary_duration_after_vad", "strict_duration_after_vad"):
            if _bounded_number(row.get(field), f"QC {field}", maximum=0.25) > 0.25:
                raise ResultConformanceError("invalid no-speech VAD evidence")
        return

    strict_repetition = _bounded_number(row.get("strict_repetition_ratio"), "QC strict_repetition_ratio")
    if row["retry_used"] is False:
        if not (
            primary_words > 0
            and strict_words > 0
            and similarity >= 0.30
            and repetition < 0.35
            and strict_repetition < 0.35
            and row.get("selected_attempt") == "primary"
            and _bounded_number(row.get("selected_consensus"), "QC selected_consensus") == similarity
            and row["ok"] is True
            and row["critical"] is False
            and reasons == []
            and row["nonspeech_hallucination"] is False
        ):
            raise ResultConformanceError("inconsistent non-retry QC evidence")
        return

    retry_words = _exact_int(row.get("retry_words"), "QC retry_words")
    _bounded_number(row.get("retry_similarity"), "QC retry_similarity")
    retry_duration = row.get("retry_duration_after_vad")
    if retry_duration is not None:
        _bounded_number(retry_duration, "QC retry_duration_after_vad", maximum=1_000_000.0)
    selected = str(row.get("selected_attempt") or "")
    if selected not in {"primary", "strict", "retry"}:
        raise ResultConformanceError("invalid QC selected attempt")
    selected_words = {"primary": primary_words, "strict": strict_words, "retry": retry_words}[selected]
    consensus = _bounded_number(row.get("selected_consensus"), "QC selected_consensus")
    nonempty_attempts = sum(value > 0 for value in (primary_words, strict_words, retry_words))
    adequate_consensus = nonempty_attempts >= 2 and consensus >= 0.30
    expected_ok = selected_words > 0 and repetition < 0.35 and not row["nonspeech_hallucination"] and adequate_consensus
    expected_critical = (not expected_ok) and (
        selected_words == 0 or consensus <= 0.05 or row["nonspeech_hallucination"]
    )
    expected_reasons: list[str] = []
    if selected_words == 0 and nonempty_attempts == 0:
        expected_reasons.append("EMPTY_ASR")
    elif not adequate_consensus:
        expected_reasons.append("LOW_CROSS_ATTEMPT_CONSENSUS")
    if repetition >= 0.35:
        expected_reasons.append("LOOP_REPETITION")
    if row["nonspeech_hallucination"]:
        expected_reasons.append("REPEATED_NONSPEECH_HALLUCINATION")
    if row["ok"] is not expected_ok or row["critical"] is not expected_critical or reasons != expected_reasons:
        raise ResultConformanceError("inconsistent retry QC evidence")


def _validate_source(manifest: dict[str, Any], expected_source_file_id: str | None) -> str:
    source = manifest.get("source")
    media = manifest.get("media")
    if not isinstance(source, dict) or not isinstance(media, dict):
        raise ResultConformanceError("source/media provenance missing")
    _required_hex(media.get("sha256"), "media.sha256")
    media_size = int(_positive_number(media.get("size_bytes"), "media.size_bytes"))
    _positive_number(media.get("duration_seconds"), "media.duration_seconds")

    source_fingerprint = _required_hex(manifest.get("source_fingerprint"), "source_fingerprint")
    if manifest.get("source_reuse_safe") is not True:
        raise ResultConformanceError("source was not marked reuse-safe")
    if source.get("kind") == "google_drive":
        file_id = str(source.get("file_id") or "")
        if expected_source_file_id is not None and file_id != expected_source_file_id:
            raise ResultConformanceError("unexpected source file id")
        size = int(_positive_number(source.get("size"), "source.size"))
        if size != media_size:
            raise ResultConformanceError("source/media size mismatch")
        checksum_key = ""
        checksum_value = ""
        for key in ("sha256Checksum", "md5Checksum", "sha1Checksum"):
            value = str(source.get(key) or "").strip().lower()
            if value:
                checksum_key, checksum_value = key, value
                break
        if not checksum_key:
            raise ResultConformanceError("provider checksum missing")
        checksum_patterns = {"sha256Checksum": HEX64, "sha1Checksum": HEX40, "md5Checksum": HEX32}
        if not checksum_patterns[checksum_key].fullmatch(checksum_value):
            raise ResultConformanceError("provider checksum is invalid")
        if checksum_key == "sha256Checksum" and checksum_value != str(media.get("sha256") or "").lower():
            raise ResultConformanceError("provider/media SHA-256 mismatch")
        expected_basis = f"{checksum_key}+size+file_id"
        if (
            manifest.get("source_fingerprint_basis") != expected_basis
            or source.get("fingerprint_basis") != expected_basis
            or source.get("reuse_safe") is not True
        ):
            raise ResultConformanceError("source fingerprint basis mismatch")
        expected = _fingerprint(
            {
                "kind": "google_drive",
                "file_id": file_id,
                "size_bytes": size,
                "checksum_kind": checksum_key,
                "checksum": checksum_value,
            }
        )
        binding = (
            "DIRECT_PROVIDER_SHA256"
            if checksum_key == "sha256Checksum"
            else f"RUNNER_ATTESTED_PROVIDER_{checksum_key.removesuffix('Checksum').upper()}_AND_DOWNLOADED_SHA256"
        )
    elif source.get("kind") == "local_path":
        if (
            manifest.get("source_fingerprint_basis") != "sha256+size"
            or source.get("fingerprint_basis") != "sha256+size"
        ):
            raise ResultConformanceError("source fingerprint basis mismatch")
        expected = _fingerprint(
            {
                "kind": "local_path",
                "size_bytes": media_size,
                "sha256": str(media.get("sha256") or "").lower(),
            }
        )
        binding = "LOCAL_SHA256_MANIFEST_BOUND"
    else:
        raise ResultConformanceError("unsupported source provenance")
    if expected != source_fingerprint or str(source.get("fingerprint") or "") != source_fingerprint:
        raise ResultConformanceError("source fingerprint mismatch")
    return binding


def _validate_processing(manifest: dict[str, Any]) -> None:
    fingerprint = _required_hex(manifest.get("processing_fingerprint"), "processing_fingerprint")
    revision = _required_hex(manifest.get("processing_revision"), "processing_revision", HEX40)
    model = str(manifest.get("processing_whisper_model") or "").strip()
    if not model:
        raise ResultConformanceError("processing model missing")
    expected = _fingerprint(
        {"contract": CONTRACT_VERSION, "source_revision": revision, "whisper_model": model}
    )
    if expected != fingerprint:
        raise ResultConformanceError("processing fingerprint mismatch")


def _artifact(path: Path, relative_name: str, *, max_file_bytes: int) -> dict[str, Any]:
    info = _safe_regular(path, max_bytes=max_file_bytes)
    return {"relative_name": relative_name, "size_bytes": info.st_size, "sha256": _sha256(path)}


def _artifact_set_sha256(artifacts: list[dict[str, Any]]) -> str:
    inventory = [
        [item["relative_name"], item["size_bytes"], item["sha256"]]
        for item in sorted(artifacts, key=lambda value: value["relative_name"])
    ]
    raw = json.dumps(inventory, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def verify_result(
    job_dir: Path,
    *,
    expected_job_id: str,
    expected_profile: str,
    expected_job_hash: str,
    expected_source_file_id: str | None = None,
    expected_artifact_set_sha256: str | None = None,
    evidence_phase: str = "POST_HOC_OBSERVATION",
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    max_frames: int = DEFAULT_MAX_FRAMES,
) -> dict[str, Any]:
    """Verify one immutable-looking technical result bundle.

    ``POST_HOC_OBSERVATION`` means the hashes were first observed after the
    worker had already moved the job to ``done``. New workers call this during
    finalization and label the receipt ``GENERATION_FINALIZATION``.
    """

    try:
        directory_info = job_dir.lstat()
    except OSError as exc:
        raise ResultConformanceError("result directory missing") from exc
    if stat.S_ISLNK(directory_info.st_mode) or not stat.S_ISDIR(directory_info.st_mode):
        raise ResultConformanceError("result directory is unsafe")
    if job_dir.name != expected_job_id:
        raise ResultConformanceError("result directory/job id mismatch")
    for root, dirs, files in os.walk(job_dir, followlinks=False):
        root_path = Path(root)
        for name in [*dirs, *files]:
            candidate = root_path / name
            if candidate.is_symlink():
                raise ResultConformanceError("symlink inside result bundle")
            if candidate.is_file() and candidate.suffix.lower() in RAW_EXTENSIONS:
                raise ResultConformanceError("raw media inside result bundle")

    manifest_path = job_dir / "manifest.json"
    manifest = _read_json(manifest_path, max_bytes=max_file_bytes)
    if not isinstance(manifest, dict):
        raise ResultConformanceError("manifest must be an object")
    if manifest.get("contract") != CONTRACT_VERSION:
        raise ResultConformanceError("unexpected contract version")
    if manifest.get("status") != "COMPLETED":
        raise ResultConformanceError("manifest is not technical COMPLETED")
    if manifest.get("job_id") != expected_job_id:
        raise ResultConformanceError("unexpected manifest job id")
    if manifest.get("profile") != expected_profile:
        raise ResultConformanceError("unexpected manifest profile")
    expected_job_hash = _required_hex(expected_job_hash, "expected_job_hash")
    if _required_hex(manifest.get("job_hash"), "job_hash") != expected_job_hash:
        raise ResultConformanceError("unexpected job hash")
    source_binding = _validate_source(manifest, expected_source_file_id)
    _validate_processing(manifest)

    transcript = manifest.get("transcript")
    if not isinstance(transcript, dict) or transcript.get("qc_pass") is not True:
        raise ResultConformanceError("transcript QC did not pass")
    if int(_positive_number(transcript.get("segments"), "transcript.segments")) < 1:
        raise ResultConformanceError("empty transcript")
    if int(_positive_number(transcript.get("words"), "transcript.words")) < 1:
        raise ResultConformanceError("empty transcript words")
    qc_blocks = int(_positive_number(transcript.get("qc_blocks"), "transcript.qc_blocks"))
    if int(_positive_number(transcript.get("qc_speech_blocks"), "transcript.qc_speech_blocks")) < 1:
        raise ResultConformanceError("transcript speech QC is empty")
    if int(transcript.get("qc_critical_failed") or 0) != 0:
        raise ResultConformanceError("critical transcript QC failure")
    if int(transcript.get("qc_hallucination_blocks") or 0) != 0:
        raise ResultConformanceError("transcript hallucination gate failed")
    expected_names = {
        "jsonl": "transcript.jsonl",
        "text": "transcript.txt",
        "qc": "transcript_qc.json",
    }
    for field, name in expected_names.items():
        if transcript.get(field) != name:
            raise ResultConformanceError(f"unexpected transcript {field} locator")

    artifacts = [_artifact(manifest_path, "manifest.json", max_file_bytes=max_file_bytes)]
    for name in REQUIRED_TRANSCRIPT_FILES:
        artifacts.append(_artifact(job_dir / name, name, max_file_bytes=max_file_bytes))
    if not (job_dir / "transcript.txt").read_text(encoding="utf-8").strip():
        raise ResultConformanceError("transcript text is empty")
    segments: list[dict[str, Any]] = []
    media_duration = _bounded_number(
        (manifest.get("media") or {}).get("duration_seconds"),
        "media.duration_seconds",
        minimum=0.000001,
        maximum=1_000_000.0,
    )
    try:
        raw_jsonl = (job_dir / "transcript.jsonl").read_text(encoding="utf-8")
        if not raw_jsonl or not raw_jsonl.endswith("\n"):
            raise ResultConformanceError("transcript JSONL is not canonically terminated")
        for line in raw_jsonl.splitlines():
            if not line.strip():
                raise ResultConformanceError("blank transcript JSONL line")
            item = _strict_json_loads(line)
            if not isinstance(item, dict) or not str(item.get("text") or "").strip():
                raise ResultConformanceError("invalid transcript segment")
            required_segment_fields = {"start", "end", "text", "chunk", "unreliable"}
            allowed_segment_fields = required_segment_fields | {"deduped_from_chunks"}
            if not required_segment_fields.issubset(item) or not set(item).issubset(allowed_segment_fields):
                raise ResultConformanceError("unexpected transcript segment fields")
            if not isinstance(item["text"], str) or item["text"] != item["text"].strip():
                raise ResultConformanceError("invalid transcript segment text")
            _exact_int(item.get("chunk"), "segment chunk")
            if not isinstance(item.get("unreliable"), bool):
                raise ResultConformanceError("invalid segment reliability")
            start = _bounded_number(item.get("start"), "segment start", maximum=media_duration + 0.01)
            end = _bounded_number(item.get("end"), "segment end", maximum=media_duration + 0.01)
            if end <= start or end > media_duration + 0.01:
                raise ResultConformanceError("transcript segment outside media timeline")
            if segments and (start, end) < (float(segments[-1]["start"]), float(segments[-1]["end"])):
                raise ResultConformanceError("transcript timeline is not ordered")
            segments.append(item)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        if isinstance(exc, ResultConformanceError):
            raise
        raise ResultConformanceError("invalid transcript JSONL") from exc
    if len(segments) != _exact_int(transcript.get("segments"), "transcript.segments", minimum=1):
        raise ResultConformanceError("transcript segment count mismatch")
    computed_words = sum(len(WORD_RE.findall(str(item["text"]).lower())) for item in segments)
    if computed_words != _exact_int(transcript.get("words"), "transcript.words", minimum=1):
        raise ResultConformanceError("transcript word count mismatch")
    canonical_text = "\n".join(
        f"[{float(item['start']):.1f}-{float(item['end']):.1f}] {item['text']}" for item in segments
    )
    try:
        rendered_text = (job_dir / "transcript.txt").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ResultConformanceError("invalid transcript text") from exc
    if rendered_text != canonical_text:
        raise ResultConformanceError("transcript text does not match JSONL timeline")
    qc = _read_json(job_dir / "transcript_qc.json", max_bytes=max_file_bytes)
    if not isinstance(qc, list) or len(qc) != qc_blocks or not qc:
        raise ResultConformanceError("transcript QC block count mismatch")
    seen_chunks: set[int] = set()
    previous_qc_start = -1.0
    for row in qc:
        if not isinstance(row, dict):
            raise ResultConformanceError("invalid transcript QC row")
        chunk = _exact_int(row.get("chunk"), "QC chunk")
        start = _bounded_number(row.get("start"), "QC start", maximum=media_duration + 0.01)
        end = _bounded_number(row.get("end"), "QC end", maximum=media_duration + 0.01)
        if chunk in seen_chunks or start < previous_qc_start or end <= start or end > media_duration + 0.01:
            raise ResultConformanceError("invalid transcript QC timeline")
        seen_chunks.add(chunk)
        previous_qc_start = start
        for field in ("ok", "no_speech", "critical", "nonspeech_hallucination"):
            if not isinstance(row.get(field), bool):
                raise ResultConformanceError(f"invalid transcript QC {field}")
        if not isinstance(row.get("retry_used"), bool):
            raise ResultConformanceError("invalid transcript QC retry_used")
        if row["no_speech"] and (not row["ok"] or row["critical"] or row["nonspeech_hallucination"]):
            raise ResultConformanceError("contradictory no-speech QC row")
        _validate_qc_attempt(row)
    if seen_chunks != set(range(len(qc))):
        raise ResultConformanceError("transcript QC chunks are not contiguous")
    if float(qc[0]["start"]) != 0.0 or abs(float(qc[-1]["end"]) - media_duration) > 0.01:
        raise ResultConformanceError("transcript QC does not cover media timeline")
    for previous, current in zip(qc, qc[1:]):
        expected_start = max(float(previous["start"]) + 1.0, float(previous["end"]) - 1.5)
        if abs(float(current["start"]) - expected_start) > 0.002:
            raise ResultConformanceError("transcript QC overlap schedule mismatch")
    dedupe_markers = 0
    for item in segments:
        chunk = int(item["chunk"])
        if chunk not in seen_chunks:
            raise ResultConformanceError("transcript segment references missing QC chunk")
        represented = item.get("deduped_from_chunks")
        if represented is None:
            if item["unreliable"] is not (not qc[chunk]["ok"]):
                raise ResultConformanceError("segment reliability contradicts QC")
            continue
        if (
            not isinstance(represented, list)
            or not represented
            or any(type(value) is not int or value not in seen_chunks for value in represented)
            or represented != sorted(set(represented))
            or chunk not in represented
        ):
            raise ResultConformanceError("invalid deduplicated chunk evidence")
        if item["unreliable"] and any(qc[value]["ok"] for value in represented):
            raise ResultConformanceError("deduplicated reliability contradicts QC")
        dedupe_markers += 1
    deduplicated_count = _exact_int(
        transcript.get("deduplicated_overlap_segments"),
        "transcript.deduplicated_overlap_segments",
    )
    if deduplicated_count < dedupe_markers:
        raise ResultConformanceError("deduplicated segment count is inconsistent")
    speech_qc = [row for row in qc if not row["no_speech"]]
    no_speech_blocks = len(qc) - len(speech_qc)
    failed_qc = sum(not row["ok"] for row in speech_qc)
    allowed_failed_qc = math.floor(len(speech_qc) * 0.20)
    critical_failed = sum(row["critical"] and not row["ok"] for row in speech_qc)
    hallucination_blocks = sum(row["nonspeech_hallucination"] for row in speech_qc)
    recomputed_qc_pass = bool(segments) and bool(speech_qc) and (
        failed_qc <= allowed_failed_qc and critical_failed == 0 and hallucination_blocks == 0
    )
    qc_expected = {
        "qc_blocks": len(qc),
        "qc_speech_blocks": len(speech_qc),
        "qc_no_speech_blocks": no_speech_blocks,
        "qc_failed": failed_qc,
        "qc_allowed_failed": allowed_failed_qc,
        "qc_critical_failed": critical_failed,
        "qc_hallucination_blocks": hallucination_blocks,
    }
    for field, expected_value in qc_expected.items():
        if _exact_int(transcript.get(field), f"transcript.{field}") != expected_value:
            raise ResultConformanceError(f"transcript {field} mismatch")
    if transcript.get("qc_pass") is not recomputed_qc_pass or not recomputed_qc_pass:
        raise ResultConformanceError("transcript QC pass mismatch")

    try:
        profile = resolve_profile(expected_profile)
    except ValueError as exc:
        raise ResultConformanceError("unknown expected profile") from exc
    planned_stages = manifest.get("planned_stages")
    if planned_stages != list(profile.stages):
        raise ResultConformanceError("planned stages do not match canonical profile")
    if manifest.get("domain_plugin") != profile.domain_plugin:
        raise ResultConformanceError("domain plugin does not match canonical profile")
    manifest_frames = manifest.get("frames")
    if not isinstance(manifest_frames, list):
        raise ResultConformanceError("manifest frames must be a list")
    if "keyframes" in planned_stages and not manifest_frames:
        raise ResultConformanceError("planned keyframes are missing")
    if len(manifest_frames) > max_frames:
        raise ResultConformanceError("keyframe count exceeds cap")
    seen_frames: set[str] = set()
    for item in manifest_frames:
        if not isinstance(item, dict):
            raise ResultConformanceError("invalid keyframe manifest entry")
        name = str(item.get("file") or "")
        if Path(name).name != name or Path(name).suffix.lower() not in FRAME_EXTENSIONS or name in seen_frames:
            raise ResultConformanceError("unsafe or duplicate keyframe name")
        seen_frames.add(name)
        artifact = _artifact(job_dir / "frames" / name, f"frames/{name}", max_file_bytes=max_file_bytes)
        if artifact["sha256"] != _required_hex(item.get("sha256"), "frame sha256"):
            raise ResultConformanceError("keyframe hash mismatch")
        artifacts.append(artifact)
    frames_dir = job_dir / "frames"
    if frames_dir.exists():
        if frames_dir.is_symlink() or not frames_dir.is_dir():
            raise ResultConformanceError("frames directory is unsafe")
        disk_frames = {
            path.name for path in frames_dir.iterdir() if path.is_file() and path.suffix.lower() in FRAME_EXTENSIONS
        }
        if disk_frames != seen_frames:
            raise ResultConformanceError("keyframe inventory mismatch")

    total_bytes = sum(int(item["size_bytes"]) for item in artifacts)
    if total_bytes > max_total_bytes:
        raise ResultConformanceError("technical bundle exceeds total byte cap")
    expected_deferred = [stage for stage in profile.stages if stage not in RUNNER_IMPLEMENTED_STAGES]
    deferred = manifest.get("deferred_analysis")
    if deferred != expected_deferred:
        raise ResultConformanceError("deferred analysis does not match executed profile boundary")
    if expected_deferred:
        domain_status = "DEFERRED"
    elif profile.name == "transcript_only":
        domain_status = "NOT_APPLICABLE"
    else:
        domain_status = "NOT_EVALUATED"
    artifact_set_sha256 = _artifact_set_sha256(artifacts)
    if expected_artifact_set_sha256 is not None:
        expected_inventory = _required_hex(expected_artifact_set_sha256, "expected_artifact_set_sha256")
        if artifact_set_sha256 != expected_inventory:
            raise ResultConformanceError("artifact set hash mismatch")
    return {
        "schema": "universal-video-result-conformance-v1",
        "state": "PASS",
        "evidence_phase": evidence_phase,
        "job_id": expected_job_id,
        "job_hash": expected_job_hash,
        "profile": expected_profile,
        "manifest_sha256": artifacts[0]["sha256"],
        "artifact_set_sha256": artifact_set_sha256,
        "artifact_count": len(artifacts),
        "total_bytes": total_bytes,
        "artifacts": sorted(artifacts, key=lambda item: item["relative_name"]),
        "technical_bundle_ready": True,
        "domain_analysis_status": domain_status,
        "deferred_analysis": deferred,
        "bridge_production_ready": False,
        "pedagogical_status": "NOT_EVALUATED",
        "publication_eligible": True,
        "source_binding_status": source_binding,
        "processing_revision": str(manifest.get("processing_revision") or ""),
        "processing_model": str(manifest.get("processing_whisper_model") or ""),
        "processing_origin_status": "SELF_REPORTED_MANIFEST_BOUND",
        "code_origin_verified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", required=True, type=Path)
    parser.add_argument("--expected-job-id", required=True)
    parser.add_argument("--expected-profile", required=True)
    parser.add_argument("--expected-job-hash", required=True)
    parser.add_argument("--expected-source-file-id")
    parser.add_argument("--expected-artifact-set-sha256")
    parser.add_argument("--evidence-phase", default="POST_HOC_OBSERVATION")
    args = parser.parse_args()
    try:
        report = verify_result(
            args.job_dir,
            expected_job_id=args.expected_job_id,
            expected_profile=args.expected_profile,
            expected_job_hash=args.expected_job_hash,
            expected_source_file_id=args.expected_source_file_id,
            expected_artifact_set_sha256=args.expected_artifact_set_sha256,
            evidence_phase=args.evidence_phase,
        )
    except ResultConformanceError as exc:
        print(
            json.dumps(
                {"schema": "universal-video-result-conformance-v1", "state": "FAIL", "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        raise SystemExit(2) from exc
    print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
