"""Resident Oracle consumer for the Neon video queue."""
from __future__ import annotations

import os
import re
import signal
import socket
import threading
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Mapping

from bridge_worker_3_1_free import stable_job_id

from .drive_adapter import access_token, file_metadata
from .runtime_preflight import validate_video_runtime
from .terminal_evidence import (
    build_terminal_evidence,
    source_identity_from_claim,
    validate_terminal_output,
)
from .video_queue import claim_job, database_url_from_env, finish_job, heartbeat_job, retry_job

APPROVED_PROFILE = "bridge_3_1_free"
APPROVED_REVISION = "3.1-free-r25.16"
CONTENT_AMBIGUITY_PREFIXES = (
    "ASR_QC_FAILED",
    "VISUAL_GAP_CHECK_FAILED",
    "R24_CONTENT_GATE_FAILED",
)
ERROR_CODE_RE = re.compile(r"^UV_[A-Z0-9_]{1,96}$")


class NeonVideoWorkerError(RuntimeError):
    error_code = "UV_NEON_VIDEO_WORKER_FAILED"


class NeonVideoTimeoutError(NeonVideoWorkerError):
    error_code = "UV_PROCESSING_TIMEOUT"


@contextmanager
def _processing_timeout() -> Iterator[None]:
    seconds = int(os.getenv("UNIVERSAL_VIDEO_JOB_TIMEOUT_SECONDS", "21600"))
    if not 900 <= seconds <= 86400:
        raise NeonVideoWorkerError("VIDEO_QUEUE_TIMEOUT_INVALID")
    if threading.current_thread() is not threading.main_thread():
        raise NeonVideoWorkerError("VIDEO_QUEUE_TIMEOUT_REQUIRES_MAIN_THREAD")
    previous = signal.getsignal(signal.SIGALRM)

    def expired(_signum: int, _frame: object) -> None:
        raise NeonVideoTimeoutError("VIDEO_QUEUE_PROCESSING_TIMEOUT")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def worker_key_from_env() -> str:
    configured = os.getenv("BRIDGE_VIDEO_QUEUE_WORKER_KEY", "").strip()
    value = configured or f"oracle-uv-{socket.gethostname()}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}", value):
        raise NeonVideoWorkerError("VIDEO_QUEUE_WORKER_KEY_INVALID")
    return value


def _metadata_checksum(meta: Mapping[str, Any]) -> str | None:
    for key, label, length in (
        ("sha256Checksum", "sha256", 64),
        ("sha1Checksum", "sha1", 40),
        ("md5Checksum", "md5", 32),
    ):
        value = str(meta.get(key) or "").strip().lower()
        if value:
            if not re.fullmatch(rf"[0-9a-f]{{{length}}}", value):
                raise NeonVideoWorkerError("VIDEO_SOURCE_CHECKSUM_INVALID")
            return f"{label}:{value}"
    return None


def verify_claimed_source(claim: Mapping[str, Any], token: str) -> dict[str, Any]:
    """Read and exactly match all six source-identity fields."""

    if claim.get("processing_profile") != APPROVED_PROFILE:
        raise NeonVideoWorkerError("VIDEO_QUEUE_PROFILE_NOT_APPROVED")
    if claim.get("algorithm_revision") != APPROVED_REVISION:
        raise NeonVideoWorkerError("VIDEO_QUEUE_REVISION_NOT_APPROVED")
    if claim.get("stable_job_key") != stable_job_id("drive", str(claim.get("source_file_id") or "")):
        raise NeonVideoWorkerError("VIDEO_QUEUE_STABLE_ID_MISMATCH")
    expected = source_identity_from_claim(claim)
    meta = file_metadata(str(claim["source_file_id"]), token)
    parents = [str(value) for value in (meta.get("parents") or [])]
    observed = {
        "file_id": str(meta.get("id") or ""),
        "name": str(meta.get("name") or ""),
        "mime_type": str(meta.get("mimeType") or ""),
        "size_bytes": int(meta.get("size") or 0),
        "parent_folder_id": parents[0] if len(parents) == 1 else "",
        "checksum": _metadata_checksum(meta),
    }
    if observed != expected:
        raise NeonVideoWorkerError("VIDEO_QUEUE_SOURCE_READBACK_MISMATCH")
    return observed


@contextmanager
def _stable_environment(claim: Mapping[str, Any]) -> Iterator[None]:
    values = {
        "BRIDGE_JOB_ID": str(claim["stable_job_key"]),
        "BRIDGE_ORIGINAL_SOURCE_DRIVE_ID": str(claim["source_file_id"]),
        "BRIDGE_OUTPUT_FOLDER_ID": str(claim["output_folder_id"]),
        "BRIDGE_WORK_FOLDER_ID": str(claim["work_folder_id"]),
        "BRIDGE_LESSON_NUMBER": str(claim["sequence"]),
        "BRIDGE_PERSIST_DATABASE": "false",
        "BRIDGE_REQUESTED_ALGORITHM_REVISION": APPROVED_REVISION,
        "BRIDGE_SPEAKER_MODEL_CACHE": os.getenv(
            "UNIVERSAL_VIDEO_SPEAKER_MODEL_CACHE",
            "/opt/bridge-school/universal-video/model-cache/speaker",
        ),
        "WHISPER_MODEL": os.getenv("UNIVERSAL_VIDEO_WHISPER_MODEL", "small"),
    }
    keys = set(values) | {"BRIDGE_WORKER_DATABASE_URL"}
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update(values)
    os.environ.pop("BRIDGE_WORKER_DATABASE_URL", None)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def stable_review_processor(claim: Mapping[str, Any]) -> dict[str, Any]:
    source_token = access_token()
    verify_claimed_source(claim, source_token)
    with _stable_environment(claim):
        import bridge_runtime_hardening_r25_16 as hardening
        import route_drive_job_outputs

        done = hardening.run(access_token)
        if (
            not isinstance(done, dict)
            or done.get("status") != "AI_DONE"
            or done.get("job_id") != claim["stable_job_key"]
            or done.get("algorithmRevision") != APPROVED_REVISION
            or (done.get("original") or {}).get("driveId") != claim["source_file_id"]
        ):
            raise NeonVideoWorkerError("VIDEO_QUEUE_AI_DONE_MISMATCH")
        route_receipt = route_drive_job_outputs.route_outputs()
        # Processing and routing can outlive a Drive access token. Acquire a
        # new credential immediately before the terminal metadata/media reads.
        terminal_token = access_token()
        terminal = build_terminal_evidence(claim, done, route_receipt, terminal_token)
    master_pdf = done.get("masterPdf") if isinstance(done.get("masterPdf"), dict) else {}
    return {
        "master_pdf_drive_id": master_pdf.get("driveId"),
        "master_pdf_sha256": master_pdf.get("sha256"),
        "master_pdf_pages": master_pdf.get("pages"),
        "deal_review_pages": master_pdf.get("dealReviewPages"),
        "speech_segment_count": (done.get("speech") or {}).get("segmentCount"),
        "visual_evidence_count": (done.get("visual") or {}).get("evidenceCount"),
        **terminal,
    }


def _base_output(claim: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "result_mode": "SHADOW_REVIEW_ONLY",
        "canonical_promotion_allowed": False,
        "database_persistence_allowed": False,
        "publication_state": "NOT_PUBLISHED",
        "source_file_id": claim["source_file_id"],
        "stable_job_key": claim["stable_job_key"],
        "algorithm_revision": claim["algorithm_revision"],
    }


def _failure(exc: BaseException) -> tuple[str, str]:
    message = str(exc)
    if any(message.startswith(prefix) for prefix in CONTENT_AMBIGUITY_PREFIXES):
        return "AMBIGUOUS", "UV_CONTENT_AMBIGUOUS"
    explicit = str(getattr(exc, "error_code", "") or "")
    return "FAILED", explicit if ERROR_CODE_RE.fullmatch(explicit) else "UV_ITEM_FAILED"


class _Heartbeat:
    def __init__(self, database_url: str, claim: Mapping[str, Any], worker_key: str) -> None:
        self.database_url = database_url
        self.claim = claim
        self.worker_key = worker_key
        self.stop = threading.Event()
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, name="video-queue-heartbeat", daemon=True)

    def _run(self) -> None:
        while not self.stop.wait(300):
            try:
                heartbeat_job(
                    self.database_url,
                    job_id=str(self.claim["job_id"]),
                    lease_token=str(self.claim["lease_token"]),
                    worker_key=self.worker_key,
                    extend_seconds=900,
                )
            except BaseException as exc:
                self.error = exc
                return

    def __enter__(self) -> "_Heartbeat":
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop.set()
        self.thread.join(timeout=5)


def process_claim(
    database_url: str,
    claim: Mapping[str, Any],
    worker_key: str,
    *,
    processor: Callable[[Mapping[str, Any]], Mapping[str, Any]] = stable_review_processor,
) -> dict[str, Any]:
    output = _base_output(claim)
    outcome = "REVIEW_READY"
    error_code: str | None = None
    initial_source: dict[str, Any] | None = None

    with _Heartbeat(database_url, claim, worker_key) as heartbeat:
        with _processing_timeout():
            try:
                # Supported custom processors cannot bypass the same pre-run
                # identity gate used by the production processor.
                initial_source = verify_claimed_source(claim, access_token())
                candidate = dict(processor(claim))
                validate_terminal_output(claim, candidate)
                output.update(candidate)
                # A processor may not override fail-closed lifecycle fields.
                output.update(_base_output(claim))
            except Exception as exc:
                outcome, error_code = _failure(exc)
                output["error_type"] = type(exc).__name__
        if heartbeat.error is not None:
            raise NeonVideoWorkerError("VIDEO_QUEUE_HEARTBEAT_LOST") from heartbeat.error

    if outcome == "REVIEW_READY":
        try:
            # Revalidate the cryptographic receipt first, then perform the
            # source metadata/checksum read as the final external observation
            # immediately before the fenced queue transition.
            validate_terminal_output(claim, output)
            final_source = verify_claimed_source(claim, access_token())
            if initial_source is None or final_source != initial_source:
                raise NeonVideoWorkerError("VIDEO_QUEUE_SOURCE_CHANGED_DURING_PROCESSING")
        except Exception as exc:
            outcome, error_code = _failure(exc)
            output["error_type"] = type(exc).__name__

    if outcome == "FAILED":
        return retry_job(
            database_url,
            job_id=str(claim["job_id"]),
            lease_token=str(claim["lease_token"]),
            worker_key=worker_key,
            error_code=error_code or "UV_ITEM_FAILED",
            max_attempts=3,
            base_delay_seconds=60,
        )
    return finish_job(
        database_url,
        job_id=str(claim["job_id"]),
        lease_token=str(claim["lease_token"]),
        worker_key=worker_key,
        outcome=outcome,
        output=output,
        error_code=error_code,
    )


def process_one_neon(
    *,
    database_url: str | None = None,
    worker_key: str | None = None,
    processor: Callable[[Mapping[str, Any]], Mapping[str, Any]] = stable_review_processor,
) -> bool:
    validate_video_runtime()
    dsn = database_url or database_url_from_env()
    key = worker_key or worker_key_from_env()
    claim = claim_job(
        dsn,
        key,
        lease_seconds=900,
        processing_profile=APPROVED_PROFILE,
        algorithm_revision=APPROVED_REVISION,
    )
    if claim is None:
        return False
    process_claim(dsn, claim, key, processor=processor)
    return True


__all__ = [
    "APPROVED_PROFILE",
    "APPROVED_REVISION",
    "CONTENT_AMBIGUITY_PREFIXES",
    "NeonVideoWorkerError",
    "process_claim",
    "process_one_neon",
    "stable_review_processor",
    "verify_claimed_source",
    "worker_key_from_env",
]
