"""Stage knowledge from the actual completed video result in the same job."""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any


def stage_completed_result(
    result: dict[str, Any],
    *,
    postprocess: Callable[..., int] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Pass the actual result in memory; a job retry repeats idempotent writes.

    No staging acknowledgement is synthesized from an ID, file, or checkpoint.
    The existing candidate writer's replay receipt and output link are durable.
    """
    env = os.environ if environment is None else environment
    if not isinstance(result, dict) or result.get("persisted") is not True:
        raise RuntimeError("DATABASE_PERSIST_NOT_COMPLETED")
    if (env.get("BRIDGE_PERSIST_DATABASE", "").strip().lower() == "true"
            and env.get("BRIDGE_OUTPUT_FOLDER_ID") and env.get("BRIDGE_LESSON_NUMBER")):
        if postprocess is None:
            from diana_longitudinal_postprocess_v3 import main
            postprocess = main
        receipt: dict[str, Any] = {}
        if postprocess(persisted_result=result, staging_receipt=receipt) != 0:
            raise RuntimeError("VIDEO_STAGING_POSTPROCESS_FAILED")
        if receipt.get("status") == "BLOCKED_NO_REPLAY":
            result["knowledge_staging"] = {"status": "BLOCKED_NO_REPLAY"}
            return result
        if receipt.get("status") != "PERSISTED" or not receipt.get("replay_receipt_id"):
            raise RuntimeError("VIDEO_STAGING_RECEIPT_MISSING")
        result["knowledge_staging"] = {
            "status": "PERSISTED",
            "replay_receipt_id": receipt["replay_receipt_id"],
            "candidate_records": receipt["candidate_records"],
        }
    else:
        result["knowledge_staging"] = {"status": "NOT_REQUESTED"}
    return result


__all__ = ["stage_completed_result"]
