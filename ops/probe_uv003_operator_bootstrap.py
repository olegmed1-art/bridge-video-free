#!/usr/bin/env python3
"""Read-only discriminator for the stopped UV003 operator bootstrap.

The command accepts no arguments, performs no writes, and emits exactly one
allowlisted status code. It never prints paths, environment values, git output,
service output, spool contents, exceptions, media, transcript text, or secrets.
"""
from __future__ import annotations

import hashlib
import json
import os
import pwd
import stat
import subprocess
import sys
from pathlib import Path

BASE = Path("/opt/bridge-school/universal-video")
SOURCE = Path("/opt/bridge-school/universal-video-src")
ENV_FILE = BASE / "universal-video.env"
SPOOL = BASE / "spool"
ROOT_STAGING = Path("/opt/bridge-school/.universal-video-diana11-003-staging")
PUBLISHED = Path("/opt/bridge-school/.universal-video-diana11-003-published")
JOB_ID = "diana11-shadow-20260826-001"
EXPECTED_COMMIT = "6a4e8248eedd00f849fcefd1bf41a51b26f5e7c6"
EXPECTED_MODEL = "small"
EXPECTED_PROCESSING_FINGERPRINT = "371661d2a1858e576e2f618ddf504da724edc30089a9af88f9dd3a140ca30951"
MAX_ENV_BYTES = 1_048_576

ALLOWED_CODES = frozenset(
    {
        "ROOT_REQUIRED",
        "SOURCE_LAYOUT",
        "SOURCE_HEAD_READ_FAILED",
        "SOURCE_HEAD_MISMATCH",
        "SOURCE_STATUS_FAILED",
        "SOURCE_DIRTY",
        "ENV_MISSING_OR_UNSAFE",
        "ENV_TOO_LARGE",
        "ENV_READ_FAILED",
        "ENV_ENCODING_INVALID",
        "ENV_STRUCTURE_INVALID",
        "ENV_SOURCE_PIN_MISSING",
        "ENV_SOURCE_PIN_MULTIPLE",
        "ENV_SOURCE_PIN_MISMATCH",
        "ENV_MODEL_MISMATCH",
        "PROCESSING_FINGERPRINT_MISMATCH",
        "RUNTIME_PYTHON_MISSING",
        "RECEIPT_READER_MISSING",
        "WORKER_USER_MISSING",
        "SERVICE_INACTIVE",
        "SPOOL_LAYOUT",
        "SPOOL_GUARD_FAILED",
        "JOB_ID_CONFLICT",
        "ROOT_CONTROL_UNSAFE",
        "PASS",
        "INTERNAL_FAILURE",
    }
)


def run_quiet(args: list[str], *, timeout: int = 10) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            args,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None


def safe_regular(path: Path, *, maximum: int) -> tuple[bool, int]:
    try:
        info = path.lstat()
    except OSError:
        return False, 0
    return stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode), int(info.st_size)


def safe_executable_regular_target(path: Path, *, maximum: int) -> bool:
    try:
        original = path.lstat()
        if not (stat.S_ISREG(original.st_mode) or stat.S_ISLNK(original.st_mode)):
            return False
        target = path.resolve(strict=True)
        info = target.stat()
    except (OSError, RuntimeError):
        return False
    size = int(info.st_size)
    return stat.S_ISREG(info.st_mode) and 0 < size <= maximum and os.access(target, os.X_OK)


def safe_directory(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)


def parse_runtime_env(raw: bytes) -> tuple[str, str] | str:
    if not raw or b"\x00" in raw:
        return "ENV_STRUCTURE_INVALID"
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return "ENV_ENCODING_INVALID"
    if text.startswith("\ufeff"):
        return "ENV_ENCODING_INVALID"
    revisions: list[str] = []
    models: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in line:
            return "ENV_STRUCTURE_INVALID"
        key, value = line.split("=", 1)
        if key == "UNIVERSAL_VIDEO_SOURCE_COMMIT":
            revisions.append(value.strip())
        elif key in {"UNIVERSAL_VIDEO_WHISPER_MODEL", "WHISPER_MODEL"}:
            models[key] = value.strip()
    if not revisions:
        return "ENV_SOURCE_PIN_MISSING"
    if len(revisions) != 1:
        return "ENV_SOURCE_PIN_MULTIPLE"
    model = (
        models.get("UNIVERSAL_VIDEO_WHISPER_MODEL", "").strip()
        or models.get("WHISPER_MODEL", "").strip()
        or "small"
    )
    return revisions[0], model


def root_control_safe(path: Path) -> bool:
    if not path.exists() and not path.is_symlink():
        return True
    if not safe_directory(path):
        return False
    try:
        info = path.stat()
    except OSError:
        return False
    return info.st_uid == 0 and info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o700


def probe() -> str:
    if os.geteuid() != 0:
        return "ROOT_REQUIRED"
    if not safe_directory(SOURCE) or not safe_directory(SOURCE / ".git"):
        return "SOURCE_LAYOUT"

    head = run_quiet(["git", "-C", str(SOURCE), "rev-parse", "HEAD"])
    if head is None or head.returncode != 0:
        return "SOURCE_HEAD_READ_FAILED"
    if head.stdout.strip() != EXPECTED_COMMIT:
        return "SOURCE_HEAD_MISMATCH"
    status_result = run_quiet(
        ["git", "-C", str(SOURCE), "status", "--porcelain=v1", "--untracked-files=all"]
    )
    if status_result is None or status_result.returncode != 0:
        return "SOURCE_STATUS_FAILED"
    if status_result.stdout:
        return "SOURCE_DIRTY"

    regular, size = safe_regular(ENV_FILE, maximum=MAX_ENV_BYTES)
    if not regular:
        return "ENV_MISSING_OR_UNSAFE"
    if size <= 0 or size > MAX_ENV_BYTES:
        return "ENV_TOO_LARGE"
    try:
        fd = os.open(ENV_FILE, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            raw = os.read(fd, MAX_ENV_BYTES + 1)
        finally:
            os.close(fd)
    except OSError:
        return "ENV_READ_FAILED"
    if len(raw) > MAX_ENV_BYTES:
        return "ENV_TOO_LARGE"
    parsed = parse_runtime_env(raw)
    if isinstance(parsed, str):
        return parsed
    revision, model = parsed
    if revision != EXPECTED_COMMIT:
        return "ENV_SOURCE_PIN_MISMATCH"
    if model != EXPECTED_MODEL:
        return "ENV_MODEL_MISMATCH"
    processing = {
        "contract": "universal-video-v1",
        "source_revision": revision,
        "whisper_model": model,
    }
    encoded = json.dumps(processing, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if hashlib.sha256(encoded).hexdigest() != EXPECTED_PROCESSING_FINGERPRINT:
        return "PROCESSING_FINGERPRINT_MISMATCH"

    runtime_python = BASE / ".venv/bin/python"
    if not safe_executable_regular_target(runtime_python, maximum=128 * 1024**2):
        return "RUNTIME_PYTHON_MISSING"
    if not safe_regular(SOURCE / "ops/universal_video_receipt_reader.py", maximum=4 * 1024**2)[0]:
        return "RECEIPT_READER_MISSING"
    try:
        pwd.getpwnam("universal-video")
    except KeyError:
        return "WORKER_USER_MISSING"

    service = run_quiet(["systemctl", "is-active", "--quiet", "universal-video.service"])
    if service is None or service.returncode != 0:
        return "SERVICE_INACTIVE"
    for state in ("inbox", "running", "done", "failed", "results"):
        if not safe_directory(SPOOL / state):
            return "SPOOL_LAYOUT"
    guard = run_quiet(
        [
            "bash",
            str(SOURCE / "ops/oracle_universal_video_spool_guard.sh"),
            "verify",
            str(BASE),
            "root",
            "universal-video",
            "universal-video",
        ],
        timeout=20,
    )
    if guard is None or guard.returncode != 0:
        return "SPOOL_GUARD_FAILED"

    job_file = f"{JOB_ID}.json"
    for state in ("inbox", "running", "done", "failed"):
        candidate = SPOOL / state / job_file
        if candidate.exists() or candidate.is_symlink():
            return "JOB_ID_CONFLICT"
    result = SPOOL / "results" / JOB_ID
    if result.exists() or result.is_symlink():
        return "JOB_ID_CONFLICT"
    receipt = PUBLISHED / f"{JOB_ID}.json"
    if receipt.exists() or receipt.is_symlink():
        return "JOB_ID_CONFLICT"

    if not root_control_safe(ROOT_STAGING) or not root_control_safe(PUBLISHED):
        return "ROOT_CONTROL_UNSAFE"
    return "PASS"


def main() -> int:
    if len(sys.argv) != 1:
        return 2
    try:
        code = probe()
    except Exception:
        code = "INTERNAL_FAILURE"
    if code not in ALLOWED_CODES:
        code = "INTERNAL_FAILURE"
    print(f"UV003_BOOTSTRAP_DIAGNOSTIC={code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
