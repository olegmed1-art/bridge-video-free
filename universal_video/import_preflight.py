"""Import-only proof for the exact Universal Video container image."""
from __future__ import annotations

import importlib
import json
import os
import re

MODULES = (
    "universal_video.neon_worker",
    "universal_video.single_canary",
    "universal_video.exact_canary_worker",
    "universal_video.drive_result_readback",
    "universal_video.drive_readback_probe",
    "bridge_worker_3_1_free",
    "bridge_runtime_hardening_r25_16",
    "route_drive_job_outputs",
    "bridge_vision",
    "bridge_contracts",
    "tools.bridge_video_positions",
    "database.runtime_worker_preflight",
    "psycopg",
)
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_IMAGE_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def run() -> dict[str, object]:
    runtime_sha = os.getenv("UNIVERSAL_VIDEO_SOURCE_COMMIT", "").strip().lower()
    image_digest = os.getenv("UNIVERSAL_VIDEO_IMAGE_DIGEST", "").strip().lower()
    if not _COMMIT_RE.fullmatch(runtime_sha) or not _IMAGE_RE.fullmatch(image_digest):
        raise RuntimeError("IMPORT_PREFLIGHT_RUNTIME_IDENTITY_INVALID")
    imported = []
    for name in MODULES:
        importlib.import_module(name)
        imported.append(name)
    return {
        "schema": "universal-video-import-preflight-v1",
        "status": "PASS",
        "runtime_sha": runtime_sha,
        "image_digest": image_digest,
        "modules": imported,
        "model_loaded": False,
        "source_media_read": False,
        "video_processed": False,
        "asr_started": False,
        "ocr_started": False,
        "training_started": False,
        "canonical_promotion_allowed": False,
        "publication_state": "NOT_PUBLISHED",
    }


def main() -> int:
    try:
        receipt = run()
    except Exception as exc:
        print(json.dumps({
            "schema": "universal-video-import-preflight-v1",
            "status": "BLOCKED",
            "error_type": type(exc).__name__,
        }, sort_keys=True))
        return 1
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
