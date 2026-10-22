"""Resident Oracle consumer for the Neon video queue."""
from __future__ import annotations

import os
import re
import signal
import socket
import threading
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Mapping
from uuid import UUID

from bridge_worker_3_1_free import stable_job_id

from .drive_adapter import access_token, file_metadata
from .route_receipt_v2 import discover_route_receipt
from .queue_staging_handoff import ExactLeaseStagingHandoff
from .private_result_handoff import (
    PrivateOneJobConfigurationError, PrivateResultStagingHandoff,
)
from .runtime_preflight import validate_video_runtime
from .terminal_evidence_v2 import (
    build_terminal_evidence,
    reverify_terminal_output_live,
    source_identity_from_claim,
)
from .video_queue import claim_job, database_url_from_env, finish_job, heartbeat_job, retry_job
from .owner_effects import terminal_effect
from .workload_lock import shared_workload_lock

verify_terminal_output_live = reverify_terminal_output_live

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
def _processing_timeout(native_budget=None) -> Iterator[None]:
    seconds = (native_budget.work_seconds if native_budget is not None
               else int(os.getenv("UNIVERSAL_VIDEO_JOB_TIMEOUT_SECONDS", "21600")))
    if (native_budget is None and not 900 <= seconds <= 86400):
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
    """Read and exactly match source identity plus the observed Drive revision."""
    if claim.get("processing_profile") != APPROVED_PROFILE:
        raise NeonVideoWorkerError("VIDEO_QUEUE_PROFILE_NOT_APPROVED")
    if claim.get("algorithm_revision") != APPROVED_REVISION:
        raise NeonVideoWorkerError("VIDEO_QUEUE_REVISION_NOT_APPROVED")
    if claim.get("stable_job_key") != stable_job_id("drive", str(claim.get("source_file_id") or "")):
        raise NeonVideoWorkerError("VIDEO_QUEUE_STABLE_ID_MISMATCH")

    expected = source_identity_from_claim(claim)
    meta = file_metadata(str(claim["source_file_id"]), token)
    if meta.get("trashed") is not False:
        raise NeonVideoWorkerError("VIDEO_SOURCE_TRASHED_OR_UNKNOWN")
    parents = [str(value) for value in (meta.get("parents") or [])]
    normalized = {
        "file_id": str(meta.get("id") or ""),
        "name": str(meta.get("name") or ""),
        "mime_type": str(meta.get("mimeType") or ""),
        "size_bytes": int(meta.get("size") or 0),
        "parent_folder_id": parents[0] if len(parents) == 1 else "",
        "checksum": _metadata_checksum(meta),
    }
    if normalized != expected:
        raise NeonVideoWorkerError("VIDEO_QUEUE_SOURCE_READBACK_MISMATCH")

    modified_time = str(meta.get("modifiedTime") or "")
    version = str(meta.get("version") or "")
    if not modified_time or not version:
        raise NeonVideoWorkerError("VIDEO_SOURCE_REVISION_MISSING")
    return {
        "id": normalized["file_id"],
        "name": normalized["name"],
        "mime_type": normalized["mime_type"],
        "size_bytes": normalized["size_bytes"],
        "parents": [normalized["parent_folder_id"]],
        "checksum": normalized["checksum"],
        "modified_time": modified_time,
        "version": version,
    }


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


def stable_review_processor(claim: Mapping[str, Any], *, sealed_observations=None,
                            completed_done=None) -> dict[str, Any]:
    """Run the approved processor and construct v2 evidence from live Drive objects."""
    with _stable_environment(claim):
        import bridge_runtime_hardening_r25_16 as hardening
        sealed_mode = sealed_observations is not None or completed_done is not None
        if sealed_mode:
            done = hardening.run(access_token, claim=claim,
                sealed_observations=sealed_observations, completed_done=completed_done)
        else:
            done = hardening.run(access_token)
        if (
            not isinstance(done, dict)
            or done.get("status") != "AI_DONE"
            or done.get("job_id") != claim["stable_job_key"]
            or done.get("algorithmRevision") != APPROVED_REVISION
            or (done.get("original") or {}).get("driveId") != claim["source_file_id"]
        ):
            raise NeonVideoWorkerError("VIDEO_QUEUE_AI_DONE_MISMATCH")
        if not sealed_mode:
            import route_drive_job_outputs
            if route_drive_job_outputs.main() != 0:
                raise NeonVideoWorkerError("VIDEO_QUEUE_OUTPUT_ROUTE_FAILED")
        terminal_token = access_token()
        if sealed_mode:
            from .terminal_evidence_v2 import sealed_ai_done_name
            route_receipt = discover_route_receipt(claim, done, terminal_token,
                ai_done_name=sealed_ai_done_name(claim))
        else:
            route_receipt = discover_route_receipt(claim, done, terminal_token)
        terminal = build_terminal_evidence(claim, done, route_receipt, terminal_token,
            **({"sealed_observations": sealed_observations} if sealed_mode else {}))

    master_pdf = done.get("masterPdf") if isinstance(done.get("masterPdf"), dict) else {}
    return {
        **terminal,
        "master_pdf_pages": master_pdf.get("pages"),
        "deal_review_pages": master_pdf.get("dealReviewPages"),
        "speech_segment_count": (done.get("speech") or {}).get("segmentCount"),
        "visual_evidence_count": (done.get("visual") or {}).get("evidenceCount"),
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
    def __init__(self, database_url: str, claim: Mapping[str, Any], worker_key: str,
                 subset_adapter: Any = None, *, native_budget=None) -> None:
        self.database_url = database_url
        self.claim = claim
        self.worker_key = worker_key
        self.subset_adapter = subset_adapter
        self.native_budget = native_budget
        if native_budget is not None:
            from .native_video_lease import require_native_budget
            require_native_budget(claim, worker_key, native_budget)
        self.stop = threading.Event()
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, name="video-queue-heartbeat", daemon=True)

    def _run(self) -> None:
        while not self.stop.wait(1 if self.native_budget is not None else 300):
            try:
                if self.native_budget is not None:
                    # BIND fixes the expiry. Never call legacy renewal to make
                    # this native lane appear live beyond its admitted window.
                    self.native_budget.check(self.claim, self.worker_key)
                    continue
                heartbeat = (self.subset_adapter.heartbeat_job
                             if self.subset_adapter is not None else heartbeat_job)
                heartbeat(
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
    subset_adapter: Any = None,
    staging_handoff: ExactLeaseStagingHandoff | PrivateResultStagingHandoff | None = None,
    private_approval_waiter: Callable[[Mapping[str, Any], str, str, str], Mapping[str, Any]] | None = None,
    private_failure_handler: Callable[[Mapping[str, Any], str], None] | None = None,
    native_budget=None,
) -> dict[str, Any]:
    """Process a lease; an optional private failure handler must raise.

    The handler runs before retry_job, including post-processor failures.
    Omitting it preserves the existing retry policy.
    """
    from .native_video_lease import require_native_budget
    require_native_budget(claim, worker_key, native_budget)
    if staging_handoff is not None and not isinstance(
            staging_handoff, (ExactLeaseStagingHandoff, PrivateResultStagingHandoff)):
        raise NeonVideoWorkerError("VIDEO_STAGING_EXACT_LEASE_AUTHORIZATION_REQUIRED")
    private_handoff = isinstance(staging_handoff, PrivateResultStagingHandoff)
    if native_budget is not None:
        from .terminal_finish_contract import Assignment
        assignment = getattr(staging_handoff, 'assignment', None)
        if (not private_handoff or type(assignment) is not Assignment
                or not callable(private_failure_handler)):
            raise NeonVideoWorkerError('NATIVE_PRIVATE_HANDOFF_REQUIRED')
        bound_task = assignment.value['autopilot']
        if (assignment.sha256 != native_budget.assignment_sha256
                or bound_task['task_id'] != native_budget.task_id
                or bound_task['lease_epoch'] != native_budget.lease_epoch):
            raise NeonVideoWorkerError('NATIVE_PRIVATE_HANDOFF_BINDING_CHANGED')
    if private_handoff and not callable(getattr(subset_adapter, 'finish_private_job', None)):
        raise NeonVideoWorkerError("VIDEO_PRIVATE_RESULT_SUBSET_ADAPTER_REQUIRED")
    if private_handoff and not callable(getattr(subset_adapter, 'connection_validator', None)):
        raise NeonVideoWorkerError("VIDEO_PRIVATE_RESULT_CONNECTION_VALIDATOR_REQUIRED")
    if private_handoff and os.getenv('BRIDGE_WORKER_DATABASE_URL', '').strip():
        raise NeonVideoWorkerError("VIDEO_PRIVATE_RESULT_BROAD_WRITER_DSN_FORBIDDEN")
    if private_approval_waiter is not None and (
            not private_handoff or not callable(private_approval_waiter)):
        raise NeonVideoWorkerError("VIDEO_PRIVATE_APPROVAL_WAITER_INVALID")
    if private_failure_handler is not None and (
            not private_handoff or not callable(private_failure_handler)):
        raise NeonVideoWorkerError("VIDEO_PRIVATE_FAILURE_HANDLER_INVALID")
    if isinstance(staging_handoff, ExactLeaseStagingHandoff) and staging_handoff.writer_dsn == database_url:
        raise NeonVideoWorkerError("VIDEO_STAGING_SEPARATE_WRITER_REQUIRED")
    output = _base_output(claim)
    outcome = "REVIEW_READY"
    error_code: str | None = None
    initial_source: dict[str, Any] | None = None
    prework_observation_id: str | None = None

    native_options = {'native_budget': native_budget} if native_budget is not None else {}
    with _Heartbeat(database_url, claim, worker_key, subset_adapter, **native_options) as heartbeat:
        with _processing_timeout(**native_options):
            try:
                initial_source = verify_claimed_source(claim, access_token())
                if subset_adapter is not None:
                    prework_observation_id = subset_adapter.before_work(
                        database_url, claim, worker_key)
                if native_budget is not None:
                    native_budget.require_work_window(claim, worker_key)
                output.update(dict(processor(claim)))
                if native_budget is not None:
                    native_budget.check(claim, worker_key)
                if private_handoff and staging_handoff.assignment is not None:
                    if (not isinstance(output.get('saved_frame_observations'),Mapping)
                            or not isinstance(output.get('completed_report_baseline'),Mapping)
                            or output.get('result_mode')!='SHADOW_REVIEW_ONLY'
                            or output.get('database_persistence_allowed') is not False
                            or output.get('canonical_promotion_allowed') is not False
                            or 'private_staging' in output):
                        raise PrivateOneJobConfigurationError('ONE_JOB_BOUND_SEALED_OUTPUT_REQUIRED')
                output.update(_base_output(claim))
                # Processor output is not proof of a committed staging receipt.
                output.pop("knowledge_staging", None)
                output.pop("private_staging", None)
            except Exception as exc:
                if private_handoff and isinstance(exc, PrivateOneJobConfigurationError):
                    raise
                outcome, error_code = _failure(exc)
                output["error_type"] = type(exc).__name__

            if heartbeat.error is not None:
                raise NeonVideoWorkerError("VIDEO_QUEUE_HEARTBEAT_LOST") from heartbeat.error

            if outcome == "REVIEW_READY":
                try:
                    verify_terminal_output_live(claim, output, access_token())
                    final_source = verify_claimed_source(claim, access_token())
                    if initial_source is None or final_source != initial_source:
                        raise NeonVideoWorkerError("VIDEO_QUEUE_SOURCE_CHANGED_DURING_PROCESSING")
                    if private_handoff and private_approval_waiter is not None:
                        # External approver acts on this exact live lease. This
                        # callback only waits; the SQL staging gate verifies
                        # the approval independently on the queue connection.
                        approval_signal = private_approval_waiter(
                            dict(claim), worker_key,
                            staging_handoff.admission_id,
                            staging_handoff.source_fingerprint)
                        expected_signal = dict(
                            admission_id=staging_handoff.admission_id,
                            job_id=str(claim['job_id']),
                            lease_token=str(claim['lease_token']),
                            worker_key=worker_key,
                            attempt_count=claim['attempt_count'],
                            source_fingerprint=staging_handoff.source_fingerprint)
                        try:
                            signal_exact = (
                                isinstance(approval_signal, Mapping)
                                and set(approval_signal) ==
                                set(expected_signal) | {'lease_approval_id'}
                                and all(approval_signal[key] == value
                                        for key, value in expected_signal.items())
                                and type(approval_signal['attempt_count']) is int
                                and str(UUID(str(approval_signal['lease_approval_id'])))
                                == approval_signal['lease_approval_id'])
                        except (TypeError, ValueError, AttributeError):
                            signal_exact = False
                        if not signal_exact:
                            raise PrivateOneJobConfigurationError(
                                'ONE_JOB_APPROVAL_SIGNAL_NOT_EXACT')
                        if heartbeat.error is not None:
                            raise NeonVideoWorkerError("VIDEO_QUEUE_HEARTBEAT_LOST") from heartbeat.error
                    if staging_handoff is not None:
                        staging_token = access_token()
                        with terminal_effect(claim):
                            if private_handoff and (staging_handoff.assignment is not None
                                                    or 'saved_frame_observations' in output):
                                if not callable(getattr(subset_adapter,'finish_private_job_v2',None)):
                                    raise PrivateOneJobConfigurationError('ONE_JOB_POST_FINISH_API_UNBOUND')
                                import copy
                                selected_for_finish = copy.deepcopy(output)
                                staged = staging_handoff.stage_selected_terminal(
                                    claim,worker_key,staging_token,selected_for_finish)
                            else:
                                staged = staging_handoff(claim, worker_key, staging_token)
                        if private_handoff:
                            output["private_staging"] = staged
                            output["result_mode"] = "PRIVATE_RESULT_STAGING_ONLY"
                            output["database_persistence_allowed"] = True
                        else:
                            output["knowledge_staging"] = staged
                        post_staging_source = verify_claimed_source(claim, access_token())
                        if post_staging_source != initial_source:
                            raise NeonVideoWorkerError("VIDEO_QUEUE_SOURCE_CHANGED_DURING_STAGING")
                except Exception as exc:
                    if private_handoff and isinstance(exc, PrivateOneJobConfigurationError):
                        raise
                    outcome, error_code = _failure(exc)
                    output["error_type"] = type(exc).__name__

            if heartbeat.error is not None:
                raise NeonVideoWorkerError("VIDEO_QUEUE_HEARTBEAT_LOST") from heartbeat.error

            if outcome == "FAILED":
                # An owning one-job parent can prohibit retry before its SQL
                # effect, including failures after the processor has returned.
                if private_failure_handler is not None:
                    private_failure_handler(dict(claim), error_code or "UV_ITEM_FAILED")
                    raise PrivateOneJobConfigurationError('ONE_JOB_FAILURE_HANDLER_RETURNED')
                retry = (subset_adapter.retry_job if subset_adapter is not None
                         else retry_job)
                with terminal_effect(claim):
                    return retry(
                        database_url,
                        job_id=str(claim["job_id"]),
                        lease_token=str(claim["lease_token"]),
                        worker_key=worker_key,
                        error_code=error_code or "UV_ITEM_FAILED",
                        max_attempts=3,
                        base_delay_seconds=60,
                    )
            if subset_adapter is not None:
                if prework_observation_id is None:
                    raise NeonVideoWorkerError("VIDEO_SUBSET_PREWORK_OBSERVATION_MISSING")
                if private_handoff and outcome == "REVIEW_READY":
                    with terminal_effect(claim):
                        if staging_handoff.assignment is not None or 'saved_frame_observations' in output:
                            staging_handoff.before_terminal_finish()
                            return subset_adapter.finish_private_job_v2(
                                database_url,claim,worker_key,
                                before_work_observation_id=prework_observation_id,
                                admission_id=staging_handoff.admission_id,
                                source_fingerprint=staging_handoff.source_fingerprint,
                                receipt_id=output['private_staging']['receipt_id'],
                                outcome=outcome,output=output,
                                assignment=staging_handoff.assignment,selected_output=selected_for_finish)
                        return subset_adapter.finish_private_job(
                            database_url, claim, worker_key,
                            before_work_observation_id=prework_observation_id,
                            admission_id=staging_handoff.admission_id,
                            source_fingerprint=staging_handoff.source_fingerprint,
                            receipt_id=output["private_staging"]["receipt_id"],
                            outcome=outcome, output=output)
                with terminal_effect(claim):
                    return subset_adapter.finish_job(
                        database_url, claim, worker_key,
                        before_work_observation_id=prework_observation_id,
                        outcome=outcome, output=output, error_code=error_code)
            with terminal_effect(claim):
                return finish_job(database_url, job_id=str(claim["job_id"]),
                                  lease_token=str(claim["lease_token"]),
                                  worker_key=worker_key, outcome=outcome,
                                  output=output, error_code=error_code)


def process_one_neon(
    *,
    database_url: str | None = None,
    worker_key: str | None = None,
    processor: Callable[[Mapping[str, Any]], Mapping[str, Any]] = stable_review_processor,
) -> bool:
    with shared_workload_lock():
        validate_video_runtime()
        dsn = database_url or database_url_from_env()
        key = worker_key or worker_key_from_env()
        subset_adapter = None
        subset_config = None
        from .subset_drive_observation import (
            DriveSubsetObservationProvider, SubsetDriveConfig,
            restricted_identity, subset_sync_enabled,
        )
        if subset_sync_enabled():
            from .subset_worker import SubsetWorkerAdapter

            subset_config = SubsetDriveConfig.from_env()
            subset_config.validate_target(dsn)
            worker_login = restricted_identity(dsn, "video_subset_worker_v2")
            collector_login = restricted_identity(
                subset_config.collector_dsn, "video_subset_collector")
            if worker_login == collector_login:
                raise NeonVideoWorkerError("VIDEO_SUBSET_SEPARATE_IDENTITIES_REQUIRED")
            subset_adapter = SubsetWorkerAdapter(
                DriveSubsetObservationProvider(subset_config, key))
        claim_options = dict(
            lease_seconds=900,
            processing_profile=APPROVED_PROFILE,
            algorithm_revision=APPROVED_REVISION,
        )
        if subset_config is not None:
            claim_options["batch_id"] = subset_config.batch_id
        claim = claim_job(dsn, key, **claim_options)
        if claim is None:
            return False
        if subset_config is not None and claim["batch_id"] != subset_config.batch_id:
            raise NeonVideoWorkerError("VIDEO_SUBSET_BATCH_MISMATCH")
        process_claim(dsn, claim, key, processor=processor,
                      subset_adapter=subset_adapter)
        return True


def process_one_private_neon(
    *, queue_dsn: str, expected_database: str, expected_login: str,
    expected_batch_id: str, expected_job_id: str,
    expected_source_file_id: str, expected_attempt_count: int,
    admission_id: str, source_fingerprint: str,
    approval_waiter: Callable[[Mapping[str, Any], str, str, str], Mapping[str, Any]],
    worker_key: str,
    claim_reserver: Callable[[], None],
    claim_publisher: Callable[[Mapping[str, Any], str, str, str], None],
    claim_committer: Callable[[Mapping[str, Any]], None],
    processor: Callable[[Mapping[str, Any]], Mapping[str, Any]] = stable_review_processor,
    private_failure_handler: Callable[[Mapping[str, Any], str], None] | None = None,
    terminal_recovery=None,
    native_budget=None,
) -> dict[str, Any]:
    """One explicit resident-runtime claim and private finish, never polling.

    The waiter coordinates an independently issued exact-lease approval just
    before staging; it does not grant approval. The 0061/0062 SQL remains the
    authority. Ordinary resident polling never invokes this callable.
    private_failure_handler optionally stops failures before retry SQL; the
    default None retains the existing worker's failure policy.
    """
    from .native_video_lease import NATIVE_WORKER_KEY, require_native_budget
    if worker_key == NATIVE_WORKER_KEY:
        # This resident adapter still owns a LEGACY claim below. A genuine
        # native caller must first use NativeVideoWorkerClaim and the original
        # post-COMMIT owner enrollment, then pass that exact claim+budget to
        # run_private_one_job. Never claim twice while awaiting native wiring.
        raise NeonVideoWorkerError('NATIVE_ORIGINAL_CLAIM_BINDING_REQUIRED')
    require_native_budget({}, worker_key, native_budget)
    from .private_one_job_entrypoint import (
        PrivateOneJobConfigurationError, _open_checked_queue, claim_exact_private_job,
        run_private_one_job,
    )
    from .subset_drive_observation import (
        DriveSubsetObservationProvider, SubsetDriveConfig, restricted_identity,
    )
    from .subset_worker import SubsetWorkerAdapter

    # The original TerminalAckQueue owns arm/COMMIT-bind/finish/ACK. This
    # parameter shares its exact prepared handoff, never creates authority.
    from .diana20_terminal_queue import TerminalRecoveryAdapter
    post_finish = terminal_recovery is not None
    if post_finish and (type(terminal_recovery) is not TerminalRecoveryAdapter
                        or not callable(private_failure_handler)):
        raise PrivateOneJobConfigurationError('ONE_JOB_POST_FINISH_RECOVERY_REQUIRED')

    try:
        exact_admission_id = str(UUID(str(admission_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise PrivateOneJobConfigurationError('ONE_JOB_PRIVATE_CONFIG_INVALID') from exc
    if (exact_admission_id != admission_id
            or not isinstance(source_fingerprint, str)
            or not re.fullmatch(r'[0-9a-f]{64}', source_fingerprint)
            or not callable(processor)
            or not callable(approval_waiter)
            or not callable(claim_reserver)
            or not callable(claim_publisher)
            or not callable(claim_committer)
            or (private_failure_handler is not None and not callable(private_failure_handler))
            or os.getenv('BRIDGE_WORKER_DATABASE_URL', '').strip()):
        raise PrivateOneJobConfigurationError('ONE_JOB_PRIVATE_CONFIG_INVALID')
    # An exclusive fence conflicts with the normal resident's shared fence.
    # Operators must still quiesce later polling before this lock is released.
    with shared_workload_lock(exclusive=True):
        validate_video_runtime()
        key = worker_key
        config = SubsetDriveConfig.from_env()
        config.validate_target(queue_dsn)
        worker_login = restricted_identity(queue_dsn, 'video_subset_worker_v2')
        collector_login = restricted_identity(config.collector_dsn,
                                              'video_subset_collector')
        if (worker_login != expected_login or worker_login == collector_login
                or config.expected_database != expected_database
                or config.batch_id != expected_batch_id):
            raise PrivateOneJobConfigurationError('ONE_JOB_PRIVATE_CONFIG_INVALID')
        # Read-only runtime, target, and identity checks precede the durable
        # journal; the claim transaction repeats its queue ACL check.
        with _open_checked_queue(queue_dsn, expected_login=expected_login,
                                 expected_database=expected_database,
                                 **({'post_finish': True} if post_finish else {})):
            pass
        adapter = SubsetWorkerAdapter(DriveSubsetObservationProvider(
            config, key, expected_login=collector_login))
        if post_finish:
            terminal_recovery.bind_private_worker_inputs(dict(
                queue_dsn=queue_dsn, expected_database=expected_database, expected_login=expected_login,
                expected_batch_id=expected_batch_id, expected_job_id=expected_job_id,
                expected_attempt_count=expected_attempt_count, expected_source_file_id=expected_source_file_id,
                worker_key=key, admission_id=admission_id, source_fingerprint=source_fingerprint,
                subset_adapter=adapter, approval_waiter=approval_waiter, processor=processor,
                private_failure_handler=private_failure_handler))
        claim_reserver()  # Durable before the first potentially mutating SQL.
        claim = claim_exact_private_job(
            queue_dsn=queue_dsn, expected_database=expected_database,
            expected_login=expected_login,
            expected_batch_id=expected_batch_id,
            expected_job_id=expected_job_id,
            expected_source_file_id=expected_source_file_id,
            expected_attempt_count=expected_attempt_count,
            worker_key=key, processing_profile=APPROVED_PROFILE,
            algorithm_revision=APPROVED_REVISION,
            claim_publisher=lambda selected: claim_publisher(
                selected, key, admission_id, source_fingerprint),
            **({'post_finish': True} if post_finish else {}))
        claim_committer(dict(claim))
        bound = {}
        if post_finish:
            # The original outer queue's committer returned only after its
            # genuine owner bind and independent worker enrollment readback.
            # Reuse THAT prepared object; never create a second handoff/reader.
            from .private_one_job_entrypoint import PreparedPrivateOneJob
            from .receipt_encoding import canonical_bytes
            prepared = terminal_recovery.prepared_job
            if (type(prepared) is not PreparedPrivateOneJob
                    or terminal_recovery.reader is None
                    or terminal_recovery.reader.handoff is not prepared.handoff
                    or prepared._claim_raw != canonical_bytes(dict(claim), max_bytes=16384)):
                raise PrivateOneJobConfigurationError('ONE_JOB_POST_COMMIT_BINDING_REQUIRED')
            terminal_recovery.assert_bound(claim, terminal_recovery.reader)
            bound = dict(assignment=prepared.handoff.assignment, prepared=prepared)
        return run_private_one_job(
            queue_dsn=queue_dsn, expected_database=expected_database,
            expected_login=expected_login, claim=claim,
            expected_batch_id=expected_batch_id,
            expected_job_id=expected_job_id,
            expected_lease_token=claim['lease_token'],
            expected_attempt_count=expected_attempt_count,
            expected_source_file_id=expected_source_file_id,
            worker_key=key, admission_id=admission_id,
            source_fingerprint=source_fingerprint,
            subset_adapter=adapter, approval_waiter=approval_waiter,
            processor=processor, private_failure_handler=private_failure_handler,
            **({'native_budget': native_budget} if native_budget is not None else {}), **bound)


__all__ = [
    "APPROVED_PROFILE",
    "APPROVED_REVISION",
    "CONTENT_AMBIGUITY_PREFIXES",
    "NeonVideoWorkerError",
    "process_claim",
    "process_one_neon",
    "process_one_private_neon",
    "stable_review_processor",
    "verify_claimed_source",
    "verify_terminal_output_live",
    "worker_key_from_env",
]
