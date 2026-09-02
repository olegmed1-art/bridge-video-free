#!/usr/bin/env python3
from __future__ import annotations

from database.runtime_app_http_preflight import EXPECTED_URL, validate_health_response


def expect_reject(status: int, body: bytes) -> None:
    try:
        validate_health_response(status, body)
    except RuntimeError:
        return
    raise AssertionError("expected health response rejection")


def main() -> None:
    assert EXPECTED_URL == "https://bridge-video-free.vercel.app/healthz"
    validate_health_response(200, b'{"status":"ok"}')

    expect_reject(503, b'{"detail":"service unavailable"}')
    expect_reject(200, b'{"status":"degraded"}')
    expect_reject(200, b'not-json')
    expect_reject(200, b'{"status":"ok","extra":true}')
    print("APP_HTTP_HEALTH_CONTRACT: PASS")


if __name__ == "__main__":
    main()
