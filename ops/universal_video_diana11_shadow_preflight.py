#!/usr/bin/env python3
"""Read-only preflight contract for UV-DIANA11-DURABLE-003.

This helper deliberately has no enqueue, submit, publication, retry, or media
processing surface. The installed command accepts no arguments. It derives the
exact fresh job hash and processing identity from the fixed resident runtime,
then fails closed if that job identity already exists anywhere in the spool.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

CONTRACT_VERSION = "universal-video-v1"
EXPERIMENT_ID = "UV-DIANA11-DURABLE-003"
JOB_ID = "diana11-shadow-20260826-001"
PROFILE = "bridge_lesson"
SOURCE_FILE_ID = "1PGRLozLJKG8tl-JYGPTCcS_lT-nn-T7C"
SOURCE_SIZE_BYTES = 740_292_560
DESTINATION_FOLDER_ID = "1I8cSuA-p0MpaZIbA33slks19KyvfJDMK"
EXPECTED_JOB_HASH = "a43e11beb0765aa91551d4c4a69767f02c4dcb3b5e485cd5bb0f2996e734d73d"
RUNTIME_ENV = Path("/opt/bridge-school/universal-video/universal-video.env")
SOURCE_DIR = Path("/opt/bridge-school/universal-video-src")
SPOOL_ROOT = Path("/opt/bridge-school/universal-video/spool")
HEX40 = re.compile(r"^[0-9a-f]{40}$")

JOB_PAYLOAD: dict[str, Any] = {
    "job_id": JOB_ID,
    "profile": PROFILE,
    "source": {"kind": "google_drive", "file_id": SOURCE_FILE_ID, "name": "Диана 11"},
    "project": "Школа спортивного бриджа",
    "metadata": {"purpose": f"{EXPERIMENT_ID} fresh provenance shadow", "human_requested": True},
    "options": {
        "chunk_seconds": 600,
        "max_source_bytes": 2_147_483_648,
        "max_duration_seconds": 43_200.0,
    },
}


def fingerprint(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def canonical_job_hash() -> str:
    payload = {"contract": CONTRACT_VERSION, **JOB_PAYLOAD}
    return fingerprint(payload)


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RuntimeError("invalid runtime environment line")
        key, value = line.split("=", 1)
        if key in {"UNIVERSAL_VIDEO_SOURCE_COMMIT", "UNIVERSAL_VIDEO_WHISPER_MODEL"}:
            values[key] = value.strip()
    return values


def git_text(source_dir: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(source_dir), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return proc.stdout.strip()


def spool_conflicts(spool_root: Path) -> list[str]:
    matches: list[str] = []
    job_file = f"{JOB_ID}.json"
    for state in ("inbox", "running", "done", "failed"):
        path = spool_root / state / job_file
        if path.exists() or path.is_symlink():
            matches.append(state)
    result = spool_root / "results" / JOB_ID
    if result.exists() or result.is_symlink():
        matches.append("results")
    return matches


def run_preflight(runtime_env: Path, source_dir: Path, spool_root: Path) -> list[str]:
    actual_job_hash = canonical_job_hash()
    if actual_job_hash != EXPECTED_JOB_HASH:
        raise RuntimeError("fresh job hash drift")

    env = parse_env(runtime_env)
    revision = env.get("UNIVERSAL_VIDEO_SOURCE_COMMIT", "")
    model = env.get("UNIVERSAL_VIDEO_WHISPER_MODEL", "")
    if not HEX40.fullmatch(revision):
        raise RuntimeError("runtime processing revision is not pinned")
    if not model or len(model) > 80 or any(ch.isspace() for ch in model):
        raise RuntimeError("runtime Whisper model is not pinned")

    head = git_text(source_dir, "rev-parse", "HEAD")
    if head != revision:
        raise RuntimeError("runtime environment/source checkout revision mismatch")
    if git_text(source_dir, "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("runtime source checkout is dirty")

    conflicts = spool_conflicts(spool_root)
    if conflicts:
        raise RuntimeError("fresh job identity already exists in spool")

    processing = {
        "contract": CONTRACT_VERSION,
        "source_revision": revision,
        "whisper_model": model,
    }
    processing_fingerprint = fingerprint(processing)

    return [
        f"UV003_EXPERIMENT_ID={EXPERIMENT_ID}",
        f"UV003_JOB_ID={JOB_ID}",
        f"UV003_JOB_HASH={actual_job_hash}",
        f"UV003_PROFILE={PROFILE}",
        f"UV003_SOURCE_FILE_ID={SOURCE_FILE_ID}",
        f"UV003_SOURCE_SIZE_BYTES={SOURCE_SIZE_BYTES}",
        f"UV003_DESTINATION_FOLDER_ID={DESTINATION_FOLDER_ID}",
        f"UV003_PROCESSING_REVISION={revision}",
        f"UV003_PROCESSING_WHISPER_MODEL={model}",
        f"UV003_PROCESSING_FINGERPRINT={processing_fingerprint}",
        "UV003_JOB_GUARD=ABSENT",
        "UV003_AUTOMATIC_RETRIES=0",
        "UV003_EXECUTION_AUTHORIZED=NO",
        "UV003_PUBLICATION_AUTHORIZED=NO",
        "UV003_PRODUCTION_PROMOTION=BLOCKED",
        "UV003_PREFLIGHT_PASS",
    ]


def main() -> int:
    if len(sys.argv) != 1:
        raise RuntimeError("arguments are forbidden for the exact preflight operator")
    for line in run_preflight(RUNTIME_ENV, SOURCE_DIR, SPOOL_ROOT):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
