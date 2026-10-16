"""Book dispatch v4: durable compute ledger and bounded shared finalizer recovery.

Source authored for independent review. No activation, DB write or runtime
qualification is implied by this implementation.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import signal
import time

from .book_contract import BookJobError, require, validate_book_job
from .book_runner import (
    file_digest, json_file, private_directory, qualified_runtime, resource_boundaries,
    reproduce_book_job, stage_book_job, verify_book_result,
)
from .drive_cleanup import queue_cleanup, retry_cleanup
from .durable_drive import (
    FINAL_RECEIPT, atomic_json, cleanup_proof_matches, configured_binding, finalize_drive_job,
)

MAX_FINALIZATION_ATTEMPTS = 3
MAX_COMPLETION_ATTEMPTS = 3


@contextmanager
def deadline_guard(seconds):
    require(os.name == "posix", "Linux timer required")
    require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0),
            "resident already owns a wall timer")
    previous = signal.getsignal(signal.SIGALRM)

    def expired(signum, frame):
        raise TimeoutError("book wall limit reached; evidence retained")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield time.monotonic() + seconds
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _binding(job):
    binding = configured_binding(
        job.job_id, job.payload["source"]["drive_file_id"], job.job_hash, kind="book",
    )
    require(binding is not None, "operator binding missing")
    qualified_runtime(job, binding)
    return binding


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _move_claim(claimed, target):
    require(not target.exists() and not target.is_symlink(), "claim destination conflict")
    # Existing exclusive spool fence is held by caller for this whole function.
    os.rename(claimed, target)
    _sync_dir(claimed.parent)
    _sync_dir(target.parent)


def _transient(exc):
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    # requests has its own ConnectionError/Timeout hierarchy.
    from requests.exceptions import ConnectionError as RequestConnectionError, Timeout
    if isinstance(exc, (RequestConnectionError, Timeout)):
        return True
    code = getattr(getattr(exc, "response", None), "status_code", None)
    return type(code) is int and (code == 429 or 500 <= code <= 599)


def _ledger(paths, job):
    path = paths["progress"] / (job.job_id + ".book-ledger.json")
    value = json_file(path) if path.exists() or path.is_symlink() else {
        "schema": "book-compute-ledger-v1", "job_id": job.job_id,
        "job_hash": job.job_hash, "compute_status": "NOT_STARTED",
        "phase": "PENDING", "finalization_attempts": 0,
    }
    require(value.get("schema") == "book-compute-ledger-v1"
            and value.get("job_id") == job.job_id and value.get("job_hash") == job.job_hash,
            "durable ledger identity changed")
    attempts = value.get("finalization_attempts")
    require(type(attempts) is int and 0 <= attempts <= MAX_FINALIZATION_ATTEMPTS,
            "invalid durable finalization budget")
    require(value.get("compute_status") in {"NOT_STARTED", "STARTED", "COMPLETED"}
            and value.get("phase") in {
                "PENDING", "DOWNLOADING", "COMPUTING", "FINALIZING",
                "REMOTE_VERIFIED", "COMPLETED", "STOP"},
            "invalid durable ledger state")
    attempts = value.get("completion_attempts", 0)
    retry_after = value.get("completion_retry_after_unix", 0)
    require(type(attempts) is int and 0 <= attempts <= MAX_COMPLETION_ATTEMPTS
            and type(retry_after) in (int, float) and 0 <= retry_after < float("inf"),
            "invalid local completion budget")
    return path, value


def _terminal_replay(job, ledger, claimed, done):
    receipt = ledger.get("completion")
    require(type(receipt) is dict and receipt.get("job_hash") == job.job_hash
            and receipt.get("job_id") == job.job_id
            and receipt.get("compute_status") == "COMPLETED"
            and receipt.get("publication_state") == "NOT_PUBLISHED",
            "durable completion receipt missing")
    proof = receipt.get("drive_finalization", {})
    require(proof.get("job_hash") == job.job_hash and proof.get("job_id") == job.job_id
            and proof.get("status") == "PUBLISHED_VERIFIED"
            and proof.get("remote_verification") == "CONTENT_READBACK_SHA256"
            and proof.get("canonical_promotion_allowed") is False
            and proof.get("remote_receipt", {}).get("verification") == "CONTENT_READBACK_SHA256",
            "durable completion proof missing")
    if done.exists() or done.is_symlink():
        require(json_file(done) == receipt, "terminal replay differs")
    else:
        atomic_json(done, receipt)
    # Claim release and proof-bound cleanup are handled by _complete_local.
    # Historical completion never triggers credentials, download or upload.





def _terminal_cleanup_intent(job, done, paths, spool_root, media_root):
    source = media_root / "drive-ready" / job.job_id
    result = paths["results"] / job.job_id
    private_directory(media_root)
    if (media_root / "drive-ready").exists():
        private_directory(media_root / "drive-ready")
    pending = spool_root / "cleanup_pending" / (job.job_id + ".json")
    if pending.exists() or pending.is_symlink():
        private_directory(pending.parent)
        # Existing intent may outlive a partial deletion of the live source pin.
        # Validate saved receipt/done/final bindings without reconstructing it.
        intent = json_file(pending)
        require(intent.get("schema") == "universal-video-cleanup-pending-v1"
                and intent.get("job_id") == job.job_id and intent.get("job_hash") == job.job_hash
                and intent.get("done_name") == done.name
                and intent.get("done_sha256") == file_digest(done)
                and intent.get("final_sha256") == file_digest(result / FINAL_RECEIPT)
                and cleanup_proof_matches(result, None, job_id=job.job_id),
                "retained cleanup intent proof changed")
        return pending
    require(not source.is_symlink() and not result.is_symlink(),
            "terminal cleanup path unsafe")
    if not source.exists():
        return None  # already cleaned, including historical result/done TTL replay
    require(cleanup_proof_matches(result, source, job_id=job.job_id),
            "terminal inputs retained without cleanup proof")
    return queue_cleanup(spool_root, media_root, job.job_id, done)


def _complete_local(job, ledger_path, ledger, claimed, done, paths, spool_root, media_root):
    attempts = ledger.get("completion_attempts", 0)
    require(attempts < MAX_COMPLETION_ATTEMPTS, "local completion budget exhausted")
    require(ledger.get("completion_retry_after_unix", 0) <= time.time(),
            "local completion backoff active")
    ledger = {**ledger, "completion_attempts": attempts + 1,
              "completion_retry_after_unix": time.time() + 60}
    atomic_json(ledger_path, ledger)  # reserve bounded local attempt before any step
    _terminal_replay(job, ledger, claimed, done)
    pending = _terminal_cleanup_intent(job, done, paths, spool_root, media_root)
    # Terminal state and any necessary cleanup intent precede claim release.
    atomic_json(ledger_path, {**ledger, "phase": "COMPLETED"})
    claimed.unlink()
    _sync_dir(claimed.parent)
    if pending is not None:
        try:
            retry_cleanup(spool_root, media_root, pending)
        except (OSError, RuntimeError, BookJobError):
            pass  # existing bounded maintenance retry owns the committed intent


def resume_pending_completion(paths, spool_root, media_root, *, progress):
    """Called by every real process_one poll under its existing exclusive fence."""
    if os.getenv("UNIVERSAL_VIDEO_BOOK_DISPATCH") != "qualified":
        return False
    private_directory(paths["running"])
    private_directory(paths["progress"], persistent=True)
    for claimed in sorted(paths["running"].glob("*.json"))[:100]:
        try:
            payload = json_file(claimed)
            if payload.get("schema") != "book-single-atom-job-v1":
                continue
            job = validate_book_job(payload)
            if claimed.name != job.job_id + ".json":
                continue
            _, ledger = _ledger(paths, job)
            if (ledger["phase"] not in {"REMOTE_VERIFIED", "COMPLETED"}
                    or ledger.get("completion_attempts", 0) >= MAX_COMPLETION_ATTEMPTS
                    or ledger.get("completion_retry_after_unix", 0) > time.time()):
                continue
            process_claimed_book(payload, claimed, claimed.name, paths, spool_root, media_root,
                                 progress=progress)
            return True
        except (OSError, ValueError, RuntimeError):
            continue  # retain unprovable claim; never enter generic video FAILED
    return False


def _recover_verified_completion(paths, job, media_root, ledger_path, ledger):
    result_dir = paths["results"] / job.job_id
    staged = media_root / "drive-ready" / job.job_id
    if (ledger["compute_status"] == "COMPLETED"
            and ledger["phase"] not in {"STOP", "REMOTE_VERIFIED", "COMPLETED"}
            and cleanup_proof_matches(result_dir, staged, job_id=job.job_id)):
        manifest = json_file(result_dir / "manifest.json")
        proof = json_file(result_dir / FINAL_RECEIPT)
        require(manifest.get("job_hash") == job.job_hash
                and proof.get("job_hash") == job.job_hash, "retained completion identity differs")
        completion = {**manifest, "compute_status": "COMPLETED",
                      "result_dir": str(result_dir), "drive_finalization": proof,
                      "publication_state": "NOT_PUBLISHED"}
        ledger = {**ledger, "phase": "REMOTE_VERIFIED", "completion": completion}
        atomic_json(ledger_path, ledger)
    return ledger

def _reserve_finalization(paths, job, ledger_path, ledger):
    value = {**ledger, "compute_status": "COMPLETED", "phase": "FINALIZING",
             "finalization_attempts": ledger["finalization_attempts"] + 1}
    require(value["finalization_attempts"] <= MAX_FINALIZATION_ATTEMPTS,
            "finalization retry budget exhausted")
    atomic_json(ledger_path, value)
    atomic_json(paths["progress"] / (job.job_id + ".recovery.json"), {
        "job_hash": job.job_hash, "attempts": value["finalization_attempts"],
        "retry_after_unix": time.time() + 60,
    })
    return value

def _process(job, claimed, job_filename, paths, spool_root, media_root, progress):
    for name in ("running", "inbox", "done", "failed", "results", "progress"):
        if name != "inbox":
            private_directory(paths[name], persistent=name == "progress")
    private_directory(spool_root)
    ledger_path, ledger = _ledger(paths, job)
    done = paths["done"] / job_filename
    ledger = _recover_verified_completion(paths, job, media_root, ledger_path, ledger)
    if ledger["phase"] in {"REMOTE_VERIFIED", "COMPLETED"}:
        _complete_local(job, ledger_path, ledger, claimed, done, paths, spool_root, media_root)
        return
    require(ledger["phase"] != "STOP", "job has a durable STOP; operator reconciliation required")
    require(ledger["finalization_attempts"] < MAX_FINALIZATION_ATTEMPTS,
            "finalization retry budget exhausted before staging/compute")
    binding = _binding(job)
    inbox = paths["inbox"].lstat()
    require(not paths["inbox"].is_symlink() and inbox.st_uid != os.geteuid()
            and inbox.st_uid != 0 and inbox.st_uid == binding.get("inbox_uid"),
            "inbox writer namespace separation missing")
    resource_boundaries(job, media_root, paths["results"], paths["progress"])
    result_dir = paths["results"] / job.job_id
    if ledger["compute_status"] in {"STARTED", "COMPLETED"} and not result_dir.exists():
        raise BookJobError("unknown compute outcome: durable STARTED; no repeat")
    with deadline_guard(job.payload["limits"]["wall_seconds"]) as deadline:
        # Ledger/budget/terminal gates have all run before any transfer.
        if ledger["compute_status"] == "COMPLETED":
            ledger = _reserve_finalization(paths, job, ledger_path, ledger)
        else:
            atomic_json(ledger_path, {**ledger, "phase": "DOWNLOADING"})
        progress(paths, job.job_id, "BOOK_DOWNLOADING")
        staged = stage_book_job(job, media_root)
        if result_dir.exists() or result_dir.is_symlink():
            verify_book_result(
                result_dir, expected_job_id=job.job_id, expected_job_hash=job.job_hash,
                expected_source_file_id=job.payload["source"]["drive_file_id"],
            )
            result = json_file(result_dir / "manifest.json")
            require(result["runtime_binary_sha256"] == binding["runtime_binary_sha256"],
                    "retained runtime pins changed")
        else:
            require(ledger["compute_status"] == "NOT_STARTED", "compute must never be repeated")
            ledger = {**ledger, "phase": "COMPUTING", "compute_status": "STARTED"}
            atomic_json(ledger_path, ledger)  # fsync before first producer invocation
            progress(paths, job.job_id, "BOOK_REPRODUCING")
            result = reproduce_book_job(
                job, staged, paths["results"], binding,
                Path(__file__).resolve().parent.parent, deadline,
            )
        if ledger["compute_status"] != "COMPLETED":
            ledger = _reserve_finalization(paths, job, ledger_path, ledger)
        progress(paths, job.job_id, "BOOK_DRIVE_FINALIZING")
        require(_binding(job) == binding, "operator binding changed during compute")
        resource_boundaries(job, media_root, paths["results"], paths["progress"])
        proof = finalize_drive_job(
            result_dir, staged, binding, job_id=job.job_id,
            profile=job.profile, job_hash=job.job_hash,
        )
        require(cleanup_proof_matches(result_dir, staged, job_id=job.job_id),
                "durable readback proof unavailable")
        completion = {
            **result, "compute_status": "COMPLETED", "result_dir": str(result_dir),
            "drive_finalization": proof, "publication_state": "NOT_PUBLISHED",
        }
        # Survives a crash before done creation and housekeeping's done TTL.
        ledger = {**ledger, "phase": "REMOTE_VERIFIED", "completion": completion}
        atomic_json(ledger_path, ledger)
        _complete_local(job, ledger_path, ledger, claimed, done, paths, spool_root, media_root)
    try:
        progress(paths, job.job_id, "BOOK_PRIVATE_CANDIDATE_READY")
    except OSError:
        pass


def process_claimed_book(payload, claimed, job_filename, paths,
                         spool_root, media_root, *, progress):
    require(os.getenv("UNIVERSAL_VIDEO_BOOK_DISPATCH") == "qualified",
            "book dispatch disabled")
    job = validate_book_job(payload)
    require(job_filename == job.job_id + ".json", "canonical book payload filename required")
    require(claimed == paths["running"] / job_filename and not claimed.is_symlink(),
            "claim path mismatch")
    try:
        _process(job, claimed, job_filename, paths, spool_root, media_root, progress)
    except Exception as exc:
        # Own book recovery before returning to the video outer exception path.
        # Payload is moved, never deleted by generic FAILED handling.
        try:
            private_directory(paths["progress"], persistent=True)
            ledger_path, ledger = _ledger(paths, job)
            ledger = _recover_verified_completion(paths, job, media_root, ledger_path, ledger)
            if ledger["phase"] in {"REMOTE_VERIFIED", "COMPLETED"}:
                # A durable success cannot become FAILED due to done, cleanup
                # intent, claim deletion or progress failures. Retain the claim
                # for local completion-only scheduler recovery; never reupload/recompute.
                return {"status": "COMPLETION_PENDING", "job_id": job.job_id}
            recoverable = (
                ledger["phase"] == "FINALIZING" and _transient(exc)
                and ledger["finalization_attempts"] < MAX_FINALIZATION_ATTEMPTS
            )
            if recoverable:
                atomic_json(paths["progress"] / (job.job_id + ".recovery.json"), {
                    "job_hash": job.job_hash, "attempts": ledger["finalization_attempts"],
                    "retry_after_unix": time.time() + 60,
                })
                _move_claim(claimed, paths["inbox"] / job_filename)
            else:
                # Preserve verified terminal evidence if local done/cleanup failed.
                if ledger["phase"] not in {"REMOTE_VERIFIED", "COMPLETED"}:
                    atomic_json(ledger_path, {**ledger, "phase": "STOP"})
                _move_claim(claimed, paths["failed"] / (job.job_id + ".book-stop.payload.json"))
            return {"status": "RETRY_PENDING" if recoverable else "STOP", "job_id": job.job_id}
        except Exception:
            # No safe mutation is possible: retain running payload and stop this
            # iteration without allowing the outer video handler to remove it.
            return {"status": "STOP_RETAINED_CLAIM", "job_id": job.job_id}
    return {"status": "COMPLETED", "job_id": job.job_id}
