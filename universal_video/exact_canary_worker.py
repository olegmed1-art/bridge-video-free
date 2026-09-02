"""One-shot worker for the isolated exact-single-canary queue profile."""
from __future__ import annotations

import json
import os
import re
from typing import Any, Mapping

from .drive_adapter import access_token
from .drive_result_readback import verify_routed_result_contract
from .neon_worker import (
    APPROVED_REVISION,
    NeonVideoWorkerError,
    _stable_environment,
    process_claim,
    worker_key_from_env,
)
from .runtime_preflight import validate_video_runtime
from .single_canary import EXACT_CANARY_PROFILE
from .source_identity import verify_claimed_source_identity
from .video_queue import claim_job, database_url_from_env

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_IMAGE_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ExactCanaryWorkerError(NeonVideoWorkerError):
    error_code = "UV_EXACT_CANARY_WORKER_FAILED"


def runtime_identity() -> tuple[str, str]:
    runtime_sha = os.getenv("UNIVERSAL_VIDEO_SOURCE_COMMIT", "").strip().lower()
    image_digest = os.getenv("UNIVERSAL_VIDEO_IMAGE_DIGEST", "").strip().lower()
    if not _COMMIT_RE.fullmatch(runtime_sha) or not _IMAGE_RE.fullmatch(image_digest):
        raise ExactCanaryWorkerError("EXACT_CANARY_RUNTIME_IDENTITY_INVALID")
    return runtime_sha, image_digest


def strict_review_processor(claim: Mapping[str, Any]) -> dict[str, Any]:
    """Process only after a fresh source check, then require actual result readback."""

    if claim.get("processing_profile") != EXACT_CANARY_PROFILE:
        raise ExactCanaryWorkerError("EXACT_CANARY_PROFILE_MISMATCH")
    if claim.get("algorithm_revision") != APPROVED_REVISION:
        raise ExactCanaryWorkerError("EXACT_CANARY_REVISION_MISMATCH")
    runtime_identity()

    # Imports happen before the source gate. After the metadata readback below,
    # the very next external operation is the processor call itself.
    import bridge_runtime_hardening_r25_16 as hardening
    import route_drive_job_outputs

    token = access_token()
    source_identity = verify_claimed_source_identity(claim, token)
    with _stable_environment(claim):
        done = hardening.run(access_token)
        if (
            not isinstance(done, dict)
            or done.get("status") != "AI_DONE"
            or done.get("job_id") != claim["stable_job_key"]
            or done.get("algorithmRevision") != APPROVED_REVISION
            or (done.get("original") or {}).get("driveId") != claim["source_file_id"]
        ):
            raise ExactCanaryWorkerError("EXACT_CANARY_AI_DONE_MISMATCH")
        if route_drive_job_outputs.main() != 0:
            raise ExactCanaryWorkerError("EXACT_CANARY_OUTPUT_ROUTE_FAILED")
    result = verify_routed_result_contract(claim, done, token, source_identity)
    result["source_recheck_stage"] = "IMMEDIATELY_BEFORE_PROCESSING"
    result["exactly_one_canary_profile"] = EXACT_CANARY_PROFILE
    return result


def process_exactly_one(
    *,
    database_url: str | None = None,
    worker_key: str | None = None,
) -> dict[str, Any]:
    """Claim one isolated-profile job once; never enter a resident loop."""

    validate_video_runtime()
    runtime_sha, image_digest = runtime_identity()
    dsn = database_url or database_url_from_env()
    key = worker_key or worker_key_from_env()
    claim = claim_job(
        dsn,
        key,
        lease_seconds=900,
        processing_profile=EXACT_CANARY_PROFILE,
        algorithm_revision=APPROVED_REVISION,
    )
    if claim is None:
        raise ExactCanaryWorkerError("EXACT_CANARY_JOB_NOT_FOUND")
    if claim.get("is_canary") is not True:
        raise ExactCanaryWorkerError("EXACT_CANARY_FLAG_MISSING")
    receipt = process_claim(dsn, claim, key, processor=strict_review_processor)
    result: dict[str, Any] = {
        "schema": "universal-video-exact-canary-worker-receipt-v1",
        "runtime_sha": runtime_sha,
        "image_digest": image_digest,
        "job_id": str(claim["job_id"]),
        "batch_id": str(claim["batch_id"]),
        "source_file_id": claim["source_file_id"],
        "queue_receipt": receipt,
        "claims_processed": 1,
        "resident_loop_entered": False,
        "canonical_promotion_allowed": False,
        "publication_state": "NOT_PUBLISHED",
    }
    if receipt.get("job_status") != "REVIEW_READY":
        result["status"] = "BLOCKED_OR_RETRY"
        return result
    if int(receipt.get("released_jobs") or 0) != 0 or receipt.get("batch_status") != "REVIEW":
        raise ExactCanaryWorkerError("EXACT_CANARY_RELEASED_ADDITIONAL_JOBS")
    result["status"] = "REVIEW_READY"
    result["exactly_one_gate"] = "PASS"
    return result


def main() -> int:
    try:
        receipt = process_exactly_one()
    except Exception as exc:
        print(json.dumps({
            "schema": "universal-video-exact-canary-worker-receipt-v1",
            "status": "BLOCKED",
            "error_code": getattr(exc, "error_code", "UV_EXACT_CANARY_WORKER_FAILED"),
            "error_type": type(exc).__name__,
            "canonical_promotion_allowed": False,
            "publication_state": "NOT_PUBLISHED",
        }, sort_keys=True))
        return 1
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0 if receipt.get("status") == "REVIEW_READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
