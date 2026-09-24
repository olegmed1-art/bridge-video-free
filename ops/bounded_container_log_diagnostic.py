#!/usr/bin/env python3
"""Emit only allow-listed, non-secret container failure diagnostics."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Iterable


ALLOWED_ERRORS = {
    "ERROR: docker is unavailable; install and attest Docker separately",
    "ERROR: docker daemon is unavailable",
    "ERROR: universal-video Unix user missing",
    "ERROR: a video job is running; refusing container rollout",
    "ERROR: protected Google Drive OAuth file missing",
    "ERROR: container image digest unavailable",
    "ERROR: container image build failed",
    "ERROR: container disk capacity unavailable",
    "ERROR: assistant-lab.service is not active",
    "ERROR: DDS3 readyz failed before activation",
    "ERROR: universal-video has a running job; refusing source upgrade",
    "ERROR: universal-video failed to stop",
    "ERROR: universal-video accepted a job while stopping; leaving sidecar stopped",
    "ERROR: fresh staged source checkout is unexpectedly dirty",
    "ERROR: bounded Universal Video operator rejection smoke returned an unexpected code",
    "ERROR: bounded Universal Video operator rejection state missing",
    "ERROR: bounded Universal Video operator rejection reason missing",
    "ERROR: assistant-lab state changed",
    "ERROR: DDS3 readyz failed after activation",
}
MOUNT_ERROR_RE = re.compile(r"ERROR: unsafe or missing mount: [A-Za-z0-9._/-]+")
ERROR_CODE_RE = re.compile(r"UV_CONTAINER_[A-Z0-9_]+")
RESOURCE_RE = re.compile(r"UNIVERSAL_VIDEO_CONTAINER_RESOURCE disk_available_kb=[0-9]+ disk_required_kb=[0-9]+")
PREPARE_STAGE_RE = re.compile(r"UNIVERSAL_VIDEO_PREPARE_STAGE stage=(?:protected-preflight|service-quiesce|source-checkout|legacy-install|operator-install|protected-postflight|complete)")
CLEANUP_RE = re.compile(r"UNIVERSAL_VIDEO_CONTAINER_CLEANUP area=root-cache age_days=14 files=[0-9]+ freed_kb=[0-9]+")
STORAGE_RE = re.compile(r"UNIVERSAL_VIDEO_CONTAINER_STORAGE area=(?:spool|output|media|model-cache|docker|source|bridge-school|var-lib|var-log|home|tmp|root|video-venv|var-bridge|containerd|snapd|apt|postgresql|root-cache|root-local|root-npm|root-cargo|root-rustup|pip-cache|hf-cache|uv-cache|torch-cache|whisper-cache|playwright-cache|containerd-content|containerd-snapshots|rootfs) used_kb=[0-9]+")


def _docker_error_code(line: str) -> str | None:
    text = line.lower()
    if "error response from daemon" not in text and "oci runtime" not in text:
        return None
    if "no space left on device" in text:
        return "UV_CONTAINER_DOCKER_DISK_FULL"
    if "permission denied" in text or "operation not permitted" in text:
        return "UV_CONTAINER_DOCKER_PERMISSION_DENIED"
    if "mount" in text:
        fixed_mounts = {
            "/universal-video/spool": "SPOOL",
            "/universal-video/output": "OUTPUT",
            "/universal-video/media": "MEDIA",
            "/universal-video/model-cache": "MODEL_CACHE",
            "/run/bridge-school": "STATUS",
            "/run/secrets": "SECRETS",
        }
        for marker, area in fixed_mounts.items():
            if marker in text:
                return f"UV_CONTAINER_DOCKER_MOUNT_{area}_FAILED"
        if "bind source path does not exist" in text:
            return "UV_CONTAINER_DOCKER_MOUNT_SOURCE_MISSING"
        return "UV_CONTAINER_DOCKER_MOUNT_FAILED"
    if "oci runtime" in text:
        return "UV_CONTAINER_OCI_RUNTIME_FAILED"
    if "network" in text or "iptables" in text:
        return "UV_CONTAINER_DOCKER_NETWORK_FAILED"
    if "already in use" in text:
        return "UV_CONTAINER_DOCKER_NAME_CONFLICT"
    return "UV_CONTAINER_DOCKER_RUN_FAILED"


def bounded_diagnostics(lines: Iterable[str]) -> list[str]:
    """Return canonical safe diagnostics and discard all other log content."""

    output: list[str] = []
    for raw in lines:
        line = raw.strip()
        docker_code = _docker_error_code(line)
        if docker_code:
            output.append(json.dumps({"error_code": docker_code, "status": "FAILED"}, separators=(",", ":"), sort_keys=True))
            continue
        if line in ALLOWED_ERRORS or MOUNT_ERROR_RE.fullmatch(line) or RESOURCE_RE.fullmatch(line) or PREPARE_STAGE_RE.fullmatch(line) or CLEANUP_RE.fullmatch(line) or STORAGE_RE.fullmatch(line):
            output.append(line)
            continue
        try:
            value = json.loads(line)
        except (TypeError, ValueError):
            continue
        if (
            isinstance(value, dict)
            and set(value) == {"error_code", "status"}
            and value.get("status") == "FAILED"
            and ERROR_CODE_RE.fullmatch(str(value.get("error_code", "")))
        ):
            output.append(json.dumps(value, separators=(",", ":"), sort_keys=True))
    return output


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        return 2
    lines = Path(args[0]).read_text(encoding="utf-8", errors="replace").splitlines()
    output = bounded_diagnostics(lines)
    if not output:
        return 1
    print("\n".join(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
