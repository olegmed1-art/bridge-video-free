"""Fail-closed startup gate for the Oracle universal-video container.

The gate intentionally emits only structured, non-secret facts.  It proves
the image identity, runtime tools, writable isolated mounts and an already
available ASR model before starting the resident worker.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import sys
from pathlib import Path
from typing import Sequence

from .runner import _load_model
from .runtime_preflight import validate_video_runtime


COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
WRITABLE_ROOT_ENV = (
    "UNIVERSAL_VIDEO_OUTPUT_ROOT",
    "UNIVERSAL_VIDEO_MEDIA_ROOT",
    "HF_HOME",
    "UNIVERSAL_VIDEO_SPEAKER_MODEL_CACHE",
)
SPOOL_LEAVES = ("inbox", "running", "done", "failed", "results", "progress")
SPEAKER_SEGMENTATION_MODEL = "pyannote-segmentation-3.0.onnx"
SPEAKER_EMBEDDING_MODEL = (
    "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
)
SPEAKER_SEGMENTATION_SHA256 = (
    "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079"
)
SPEAKER_EMBEDDING_SHA256 = (
    "1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b"
)
MIN_SPEAKER_MODEL_BYTES = 1024
ENTRYPOINT_SELF_TEST = "entrypoint-self-test"
PRECANARY_STARTUP_PROBE_ENV = "UNIVERSAL_VIDEO_PRECANARY_STARTUP_PROBE"
PRECANARY_STATUS_PATH = "/run/bridge-school/precanary-startup-status.json"
DEFAULT_WORKER_COMMAND = ["python", "-m", "universal_video.spool_worker"]
QUEUE_ENV_NAMES = (
    "BRIDGE_VIDEO_QUEUE_DATABASE_URL",
    "BRIDGE_VIDEO_QUEUE_DATABASE_URL_FILE",
    "BRIDGE_WORKER_DATABASE_URL",
)


class ContainerRuntimeUnavailable(RuntimeError):
    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


def _require_directory(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise ContainerRuntimeUnavailable("UV_CONTAINER_MOUNT_UNAVAILABLE")
    return path


def _write_probe(path: Path) -> None:
    marker = path / f".container-write-check-{os.getpid()}"
    descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    marker.unlink()


def _require_writable_directory(value: str) -> Path:
    path = _require_directory(value)
    try:
        _write_probe(path)
        return path
    except ContainerRuntimeUnavailable:
        raise
    except OSError as exc:
        raise ContainerRuntimeUnavailable("UV_CONTAINER_MOUNT_UNAVAILABLE") from exc


def _require_spool_directory(value: str) -> Path:
    """Validate the protected spool root and each worker-owned state leaf."""

    root = _require_directory(value)
    for leaf in SPOOL_LEAVES:
        _require_writable_directory(str(root / leaf))
    return root


def _speaker_artifact(cache: Path, filename: str, expected_sha256: str) -> tuple[Path, str]:
    path = cache / filename
    if path.is_symlink() or not path.is_file():
        raise ContainerRuntimeUnavailable("UV_CONTAINER_SPEAKER_MODEL_UNAVAILABLE")
    try:
        if path.stat().st_size <= MIN_SPEAKER_MODEL_BYTES:
            raise ContainerRuntimeUnavailable("UV_CONTAINER_SPEAKER_MODEL_INVALID")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except ContainerRuntimeUnavailable:
        raise
    except OSError as exc:
        raise ContainerRuntimeUnavailable("UV_CONTAINER_SPEAKER_MODEL_UNAVAILABLE") from exc
    observed_sha256 = digest.hexdigest()
    if observed_sha256 != expected_sha256:
        raise ContainerRuntimeUnavailable("UV_CONTAINER_SPEAKER_MODEL_DIGEST_MISMATCH")
    return path, observed_sha256


def _validate_speaker_models(cache: Path) -> dict[str, str]:
    segmentation, segmentation_sha = _speaker_artifact(
        cache, SPEAKER_SEGMENTATION_MODEL, SPEAKER_SEGMENTATION_SHA256
    )
    embedding, embedding_sha = _speaker_artifact(
        cache, SPEAKER_EMBEDDING_MODEL, SPEAKER_EMBEDDING_SHA256
    )
    try:
        import sherpa_onnx

        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=str(segmentation)
                )
            ),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(embedding)
            ),
            clustering=sherpa_onnx.FastClusteringConfig(
                num_clusters=2, threshold=0.5
            ),
            min_duration_on=0.3,
            min_duration_off=0.5,
        )
        if not config.validate():
            raise ContainerRuntimeUnavailable("UV_CONTAINER_SPEAKER_MODEL_INVALID")
        sherpa_onnx.OfflineSpeakerDiarization(config)
    except ContainerRuntimeUnavailable:
        raise
    except Exception as exc:
        raise ContainerRuntimeUnavailable("UV_CONTAINER_SPEAKER_MODEL_INVALID") from exc
    return {
        "segmentation_sha256": segmentation_sha,
        "embedding_sha256": embedding_sha,
    }


def validate_container_runtime() -> dict[str, object]:
    """Validate all container-only prerequisites without processing media."""

    commit = os.getenv("UNIVERSAL_VIDEO_SOURCE_COMMIT", "").strip().lower()
    if not COMMIT_RE.fullmatch(commit):
        raise ContainerRuntimeUnavailable("UV_CONTAINER_PROVENANCE_INVALID")
    mounts = {
        "UNIVERSAL_VIDEO_SPOOL_ROOT": _require_spool_directory(os.getenv("UNIVERSAL_VIDEO_SPOOL_ROOT", "")),
        **{name: _require_writable_directory(os.getenv(name, "")) for name in WRITABLE_ROOT_ENV},
    }
    speaker_models = _validate_speaker_models(
        mounts["UNIVERSAL_VIDEO_SPEAKER_MODEL_CACHE"]
    )
    try:
        runtime = validate_video_runtime()
        _load_model()
    except ContainerRuntimeUnavailable:
        raise
    except Exception as exc:
        raise ContainerRuntimeUnavailable("UV_CONTAINER_MODEL_UNAVAILABLE") from exc
    return {
        "schema": "universal-video-container-readiness-v1",
        "status": "READY",
        "source_commit": commit,
        "python": ".".join(map(str, sys.version_info[:3])),
        "runtime": runtime,
        "speaker_models": speaker_models,
        "mounts": sorted(str(path) for path in mounts.values()),
        "fallback_used": False,
    }


def _validate_precanary_startup_probe(command: Sequence[str]) -> None:
    """Allow one no-media startup probe only under an unmistakable CI contract.

    The probe still execs the image's configured worker command.  It merely
    bypasses model readiness so an empty isolated spool can prove the real
    ENTRYPOINT -> container_runtime -> CMD -> spool_worker.__main__ chain.
    Production queue configuration is explicitly forbidden in this mode.
    """

    commit = os.getenv("UNIVERSAL_VIDEO_SOURCE_COMMIT", "").strip().lower()
    if not COMMIT_RE.fullmatch(commit):
        raise ContainerRuntimeUnavailable("UV_CONTAINER_STARTUP_PROBE_INVALID")
    if list(command) != DEFAULT_WORKER_COMMAND:
        raise ContainerRuntimeUnavailable("UV_CONTAINER_STARTUP_PROBE_INVALID")
    if os.getenv("UNIVERSAL_VIDEO_STATUS_PATH", "") != PRECANARY_STATUS_PATH:
        raise ContainerRuntimeUnavailable("UV_CONTAINER_STARTUP_PROBE_INVALID")
    if os.getenv("UNIVERSAL_VIDEO_RESIDENT_ID", "") != "container":
        raise ContainerRuntimeUnavailable("UV_CONTAINER_STARTUP_PROBE_INVALID")
    if any(os.getenv(name, "").strip() for name in QUEUE_ENV_NAMES):
        raise ContainerRuntimeUnavailable("UV_CONTAINER_STARTUP_PROBE_QUEUE_FORBIDDEN")


def main(argv: Sequence[str] | None = None) -> int:
    command = list(sys.argv[1:] if argv is None else argv)
    if command == [ENTRYPOINT_SELF_TEST]:
        print(
            json.dumps(
                {
                    "schema": "universal-video-entrypoint-self-test-v1",
                    "status": "PASS",
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    if not command:
        command = list(DEFAULT_WORKER_COMMAND)
    if os.getenv(PRECANARY_STARTUP_PROBE_ENV, "") == "1":
        try:
            _validate_precanary_startup_probe(command)
        except ContainerRuntimeUnavailable as exc:
            print(
                json.dumps({"status": "FAILED", "error_code": exc.error_code}, sort_keys=True),
                file=sys.stderr,
            )
            return 78
        os.execvp(command[0], command)
        return 127
    try:
        readiness = validate_container_runtime()
    except ContainerRuntimeUnavailable as exc:
        print(json.dumps({"status": "FAILED", "error_code": exc.error_code}, sort_keys=True), file=sys.stderr)
        return 78
    print(json.dumps(readiness, sort_keys=True), flush=True)
    os.execvp(command[0], command)
    return 127


if __name__ == "__main__":  # pragma: no cover - exercised by image startup
    raise SystemExit(main())
