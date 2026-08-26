#!/usr/bin/env python3
"""Exact read-only discriminator for the UV003 READY_BEFORE gate.

The command accepts no arguments, performs no writes, and emits exactly one
allowlisted classification marker.  It never prints the readiness document,
service output, exception text, environment values, media paths, or secrets.
"""
from __future__ import annotations

import http.client
import json
import socket
import subprocess
import sys
from typing import Any

ASSISTANT_SERVICE = "assistant-lab.service"
VIDEO_SERVICE = "universal-video.service"
READY_HOST = "127.0.0.1"
READY_PORT = 8080
READY_PATH = "/readyz"
MAX_BODY_BYTES = 65_536

ALLOWED_CODES = frozenset(
    {
        "ASSISTANT_SERVICE_INACTIVE",
        "VIDEO_SERVICE_INACTIVE",
        "SYSTEMCTL_UNAVAILABLE",
        "CONNECT_REFUSED",
        "FETCH_TIMEOUT",
        "FETCH_FAILED",
        "HTTP_NOT_2XX",
        "BODY_TOO_LARGE",
        "UTF8_INVALID",
        "JSON_INVALID",
        "JSON_NOT_OBJECT",
        "STATUS_MISSING",
        "STATUS_NOT_READY",
        "ENGINE_MISSING",
        "ENGINE_NOT_DDS3",
        "FALLBACK_MISSING",
        "FALLBACK_NOT_FALSE",
        "POSITION_SOLVER_MISSING",
        "POSITION_SOLVER_NOT_READY",
        "INTERNAL_FAILURE",
        "PASS",
    }
)


def service_active(name: str) -> bool | None:
    """Return active state, or None when systemctl itself is unavailable."""
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", "--quiet", name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    return proc.returncode == 0


def classify_document(status_code: int, body: bytes) -> str:
    """Classify a bounded response without exposing any response value."""
    if not 200 <= status_code < 300:
        return "HTTP_NOT_2XX"
    if len(body) > MAX_BODY_BYTES:
        return "BODY_TOO_LARGE"
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return "UTF8_INVALID"
    try:
        document: Any = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return "JSON_INVALID"
    if not isinstance(document, dict):
        return "JSON_NOT_OBJECT"

    if "status" not in document:
        return "STATUS_MISSING"
    if document["status"] != "ready":
        return "STATUS_NOT_READY"

    if "engine" not in document:
        return "ENGINE_MISSING"
    if document["engine"] != "DDS3":
        return "ENGINE_NOT_DDS3"

    if "fallback_used" not in document:
        return "FALLBACK_MISSING"
    if document["fallback_used"] is not False:
        return "FALLBACK_NOT_FALSE"

    if "position_solver" not in document:
        return "POSITION_SOLVER_MISSING"
    if document["position_solver"] != "ready":
        return "POSITION_SOLVER_NOT_READY"

    return "PASS"


def fetch_local_ready() -> tuple[int, bytes] | str:
    connection = http.client.HTTPConnection(READY_HOST, READY_PORT, timeout=10)
    try:
        connection.request("GET", READY_PATH, headers={"Accept": "application/json"})
        response = connection.getresponse()
        body = response.read(MAX_BODY_BYTES + 1)
        return response.status, body
    except ConnectionRefusedError:
        return "CONNECT_REFUSED"
    except (socket.timeout, TimeoutError):
        return "FETCH_TIMEOUT"
    except (http.client.HTTPException, OSError):
        return "FETCH_FAILED"
    finally:
        connection.close()


def probe() -> str:
    assistant = service_active(ASSISTANT_SERVICE)
    if assistant is None:
        return "SYSTEMCTL_UNAVAILABLE"
    if not assistant:
        return "ASSISTANT_SERVICE_INACTIVE"

    video = service_active(VIDEO_SERVICE)
    if video is None:
        return "SYSTEMCTL_UNAVAILABLE"
    if not video:
        return "VIDEO_SERVICE_INACTIVE"

    fetched = fetch_local_ready()
    if isinstance(fetched, str):
        return fetched
    status_code, body = fetched
    return classify_document(status_code, body)


def main() -> int:
    if len(sys.argv) != 1:
        return 2
    try:
        code = probe()
    except Exception:  # The external surface remains fixed and non-sensitive.
        code = "INTERNAL_FAILURE"
    if code not in ALLOWED_CODES:
        code = "INTERNAL_FAILURE"
    print(f"UV003_READY_BEFORE_CODE={code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
