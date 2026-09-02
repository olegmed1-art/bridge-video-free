"""Run exactly one isolated Universal Video canary after explicit Director GO.

The module is deliberately not used by the resident service. It refuses to
run unless a single eligible canary job is isolated in a one-job batch and the
runtime/image/source identity are exact. Merely importing this module or using
``--preflight-only`` never processes media.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from typing import Any, Mapping

from universal_video.canary_gate import (
    CANONICAL_PROMOTION_ALLOWED,
    PUBLICATION_STATE,
    CanaryGateError,
    apply_result_contract,
    canonical_json_bytes,
    source_identity_from_job,
    validate_image_digest,
    validate_runtime_sha,
)


@dataclass(frozen=True)
class OneCanaryTarget:
    job_id: str
    batch_id: str
    source_file_id: str
    processing_profile: str
    algorithm_revision: str


def _dsn() -> str:
    from universal_video.video_queue import database_url_from_env

    return database_url_from_env()


def _connect():
    import psycopg

    return psycopg.connect(_dsn(), autocommit=True)


def _row_dict(cursor, row: Any) -> dict[str, Any]:
    return {column.name: value for column, value in zip(cursor.description, row, strict=True)}


def _load_isolated_target(
    *,
    expected_job_id: str,
    expected_source_file_id: str,
    processing_profile: str,
    algorithm_revision: str,
) -> dict[str, Any]:
    """Prove that claim_job can select only the requested one-job canary."""

    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                j.*,
                b.status AS batch_status,
                b.expected_count,
                b.processing_profile AS batch_processing_profile,
                b.algorithm_revision AS batch_algorithm_revision,
                (SELECT count(*) FROM video_queue.job bx WHERE bx.batch_id = b.batch_id) AS batch_job_count,
                (
                    SELECT count(*)
                    FROM video_queue.job q
                    JOIN video_queue.batch qb ON qb.batch_id = q.batch_id
                    WHERE qb.processing_profile = %s
                      AND qb.algorithm_revision = %s
                      AND q.status IN ('QUEUED', 'RETRY')
                ) AS eligible_job_count
            FROM video_queue.job j
            JOIN video_queue.batch b ON b.batch_id = j.batch_id
            WHERE j.job_id = %s::uuid
            """,
            (processing_profile, algorithm_revision, expected_job_id),
        )
        row = cur.fetchone()
        if row is None:
            raise CanaryGateError("expected canary job does not exist")
        job = _row_dict(cur, row)

    failures: list[str] = []
    if str(job.get("source_file_id") or "") != expected_source_file_id:
        failures.append("source_file_id mismatch")
    if job.get("status") != "QUEUED":
        failures.append(f"job status must be QUEUED, observed={job.get('status')!r}")
    if job.get("batch_status") != "QUEUED_CANARY":
        failures.append(f"batch status must be QUEUED_CANARY, observed={job.get('batch_status')!r}")
    if job.get("is_canary") is not True:
        failures.append("job is not marked canary")
    if int(job.get("expected_count") or 0) != 1:
        failures.append("batch expected_count is not 1")
    if int(job.get("batch_job_count") or 0) != 1:
        failures.append("batch contains more or fewer than exactly one job")
    if int(job.get("eligible_job_count") or 0) != 1:
        failures.append("claim scope contains another QUEUED/RETRY job")
    if job.get("batch_processing_profile") != processing_profile:
        failures.append("processing profile mismatch")
    if job.get("batch_algorithm_revision") != algorithm_revision:
        failures.append("algorithm revision mismatch")
    source_identity_from_job(job)
    if failures:
        raise CanaryGateError("; ".join(failures))
    return job


def _strict_processor(job: Mapping[str, Any]) -> dict[str, Any]:
    """Processor wrapper used only by the explicit one-canary command."""

    runtime_sha = validate_runtime_sha(os.environ.get("BRIDGE_CANARY_RUNTIME_SHA", ""))
    image_digest = validate_image_digest(os.environ.get("BRIDGE_CANARY_IMAGE_DIGEST", ""))
    source_identity_from_job(job)

    # stable_review_processor performs Drive metadata+checksum verification after
    # source download and immediately before invoking the stable processor.
    from universal_video.neon_worker import stable_review_processor

    processed = dict(stable_review_processor(dict(job)))
    # The resident worker normally supplies these queue-envelope fields before
    # merging a processor result.  This wrapper invokes the result contract
    # directly, so build the same fail-closed envelope before validation.
    processed.update(
        {
            "result_mode": "SHADOW_REVIEW_ONLY",
            "canonical_promotion_allowed": CANONICAL_PROMOTION_ALLOWED,
            "database_persistence_allowed": False,
            "publication_state": PUBLICATION_STATE,
            "source_file_id": str(job.get("source_file_id") or ""),
            "stable_job_key": str(job.get("stable_job_key") or ""),
            "algorithm_revision": str(job.get("algorithm_revision") or ""),
        }
    )
    return apply_result_contract(
        job=job,
        processor_result=processed,
        runtime_sha=runtime_sha,
        image_digest=image_digest,
    )


def _postflight(target: OneCanaryTarget, runtime_sha: str, image_digest: str) -> dict[str, Any]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                j.job_id::text AS job_id,
                j.status,
                j.output AS result_manifest,
                j.error_code AS error,
                b.status AS batch_status,
                (SELECT count(*) FROM video_queue.job bx WHERE bx.batch_id = b.batch_id) AS batch_job_count,
                (SELECT count(*) FROM video_queue.job bx WHERE bx.batch_id = b.batch_id AND bx.status IN ('PENDING_CANARY','QUEUED','LEASED')) AS unfinished_count
            FROM video_queue.job j
            JOIN video_queue.batch b ON b.batch_id = j.batch_id
            WHERE j.job_id = %s::uuid
            """,
            (target.job_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise CanaryGateError("canary job disappeared after processing")
        state = _row_dict(cur, row)

    if state.get("status") != "REVIEW_READY":
        raise CanaryGateError(
            f"canary did not reach REVIEW_READY: status={state.get('status')!r}, error={state.get('error')!r}"
        )
    if state.get("batch_status") != "REVIEW":
        raise CanaryGateError("one-job canary batch did not reach REVIEW")
    if int(state.get("batch_job_count") or 0) != 1 or int(state.get("unfinished_count") or 0) != 0:
        raise CanaryGateError("canary completion released or left another batch job")

    manifest = state.get("result_manifest")
    if isinstance(manifest, str):
        manifest = json.loads(manifest)
    if not isinstance(manifest, dict):
        raise CanaryGateError("terminal result_manifest is not an object")
    receipt = manifest.get("terminal_receipt")
    if not isinstance(receipt, dict):
        raise CanaryGateError("terminal receipt is missing")
    if receipt.get("runtime_sha") != runtime_sha or receipt.get("image_digest") != image_digest:
        raise CanaryGateError("terminal receipt is not bound to exact runtime/image")
    if receipt.get("drive_readback_verified") is not True:
        raise CanaryGateError("terminal receipt has no verified Drive readback")
    if manifest.get("publication_state") != PUBLICATION_STATE:
        raise CanaryGateError("terminal publication state changed")
    if manifest.get("canonical_promotion_allowed") is not CANONICAL_PROMOTION_ALLOWED:
        raise CanaryGateError("terminal result allowed canonical promotion")
    return {
        "job_id": target.job_id,
        "batch_id": target.batch_id,
        "status": state["status"],
        "batch_status": state["batch_status"],
        "runtime_sha": runtime_sha,
        "image_digest": image_digest,
        "artifact_manifest": manifest.get("artifact_manifest"),
        "terminal_receipt": receipt,
        "publication_state": PUBLICATION_STATE,
        "canonical_promotion_allowed": CANONICAL_PROMOTION_ALLOWED,
        "automatic_batch_release": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--director-go", action="store_true", help="required to process media")
    parser.add_argument("--preflight-only", action="store_true", help="validate isolation; do not process media")
    parser.add_argument("--expected-job-id", required=True)
    parser.add_argument("--expected-source-file-id", required=True)
    parser.add_argument("--processing-profile", required=True)
    parser.add_argument("--algorithm-revision", required=True)
    parser.add_argument("--runtime-sha", required=True)
    parser.add_argument("--image-digest", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runtime_sha = validate_runtime_sha(args.runtime_sha)
    image_digest = validate_image_digest(args.image_digest)
    job = _load_isolated_target(
        expected_job_id=args.expected_job_id,
        expected_source_file_id=args.expected_source_file_id,
        processing_profile=args.processing_profile,
        algorithm_revision=args.algorithm_revision,
    )
    target = OneCanaryTarget(
        job_id=str(job["job_id"]),
        batch_id=str(job["batch_id"]),
        source_file_id=str(job["source_file_id"]),
        processing_profile=args.processing_profile,
        algorithm_revision=args.algorithm_revision,
    )
    preflight = {
        "job_id": target.job_id,
        "batch_id": target.batch_id,
        "source_identity": source_identity_from_job(job),
        "runtime_sha": runtime_sha,
        "image_digest": image_digest,
        "isolated_one_job_batch": True,
        "eligible_job_count": 1,
        "media_processing_started": False,
        "publication_state": PUBLICATION_STATE,
        "canonical_promotion_allowed": CANONICAL_PROMOTION_ALLOWED,
    }
    if args.preflight_only:
        print(canonical_json_bytes(preflight).decode("utf-8"), end="")
        return 0
    if not args.director_go:
        raise CanaryGateError("media processing requires explicit --director-go")

    os.environ["BRIDGE_CANARY_RUNTIME_SHA"] = runtime_sha
    os.environ["BRIDGE_CANARY_IMAGE_DIGEST"] = image_digest
    from universal_video.neon_worker import process_one_neon

    outcome = process_one_neon(
        database_url=_dsn(),
        worker_key=f"director-one-canary-{runtime_sha[:12]}",
        processor=_strict_processor,
    )
    if not outcome:
        raise CanaryGateError("the exact canary was not claimed")
    receipt = _postflight(target, runtime_sha, image_digest)
    print(canonical_json_bytes(receipt).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
