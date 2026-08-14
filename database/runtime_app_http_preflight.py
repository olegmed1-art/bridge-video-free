#!/usr/bin/env python3
"""Verify that the public production API reaches its configured database."""
from __future__ import annotations

import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

EXPECTED_URL = "https://bridge-video-free.vercel.app/healthz"
ATTEMPTS = 3
TIMEOUT_SECONDS = 30


def validate_health_response(status: int, body: bytes) -> None:
    if status != 200:
        raise RuntimeError(f"unexpected HTTP status: {status}")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("health response is not valid JSON") from exc
    if payload != {"status": "ok"}:
        raise RuntimeError("health response does not match the production contract")


def main() -> None:
    url = os.environ.get("BRIDGE_APP_HEALTH_URL", EXPECTED_URL).strip()
    if url != EXPECTED_URL:
        raise SystemExit("RUNTIME_APP_HTTP: FAIL: unexpected health target")

    last_error = "unknown"
    for attempt in range(1, ATTEMPTS + 1):
        try:
            request = Request(url, headers={"User-Agent": "bridge-school-health-monitor/1.0"})
            with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                final_url = response.geturl()
                status = response.status
                body = response.read(4096)
            if final_url != EXPECTED_URL:
                raise RuntimeError("health request was redirected")
            validate_health_response(status, body)
            print("RUNTIME_APP_HTTP: PASS status=200 database=reachable")
            return
        except (HTTPError, URLError, TimeoutError, OSError, RuntimeError) as exc:
            last_error = type(exc).__name__
            if attempt < ATTEMPTS:
                time.sleep(2 ** (attempt - 1))

    raise SystemExit(f"RUNTIME_APP_HTTP: FAIL after {ATTEMPTS} attempts: {last_error}")


if __name__ == "__main__":
    main()
