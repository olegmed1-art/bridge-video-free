#!/usr/bin/env python3
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request

from bridge_school_api.dds3.remote import (
    GOLDEN_TABLE,
    RemoteDDS3Config,
    compute_remote,
    remote_engine_readiness,
)
from bridge_school_api.dds3.service import DDSUnavailable


class _Response:
    def __init__(self, payload, status=200):
        self.status = status
        self._raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, _limit=-1):
        return self._raw


def expect_unavailable(fn, reason: str) -> None:
    try:
        fn()
    except DDSUnavailable as exc:
        assert reason in str(exc), (reason, str(exc))
        return
    raise AssertionError(f"expected DDSUnavailable containing {reason}")


def main() -> None:
    original = urllib.request.urlopen
    try:
        seen = {}

        def fake_compute(request, timeout, context):
            seen["url"] = request.full_url
            seen["authorization"] = request.headers.get("Authorization")
            seen["timeout"] = timeout
            return _Response({"engine": "DDS3", "fallback_used": False, "dd_table": GOLDEN_TABLE})

        urllib.request.urlopen = fake_compute
        result = compute_remote(
            {"operation": "dd_table", "pbn": "N:A... ... ... ..."},
            bearer_token="short-lived-vercel-token",
            config=RemoteDDS3Config(base_url="https://203.0.113.10", timeout_seconds=3),
        )
        assert result["engine"] == "DDS3"
        assert seen["url"] == "https://203.0.113.10/v1/compute"
        assert seen["authorization"] == "Bearer short-lived-vercel-token"
        assert seen["timeout"] == 3

        expect_unavailable(
            lambda: compute_remote(
                {"operation": "dd_table", "pbn": "N:A... ... ... ..."},
                bearer_token="token",
                config=RemoteDDS3Config(base_url="http://203.0.113.10", timeout_seconds=1),
            ),
            "DDS3_REMOTE_URL_INVALID",
        )

        urllib.request.urlopen = lambda *args, **kwargs: _Response({"engine": "other", "fallback_used": True})
        expect_unavailable(
            lambda: compute_remote(
                {"operation": "dd_table", "pbn": "N:A... ... ... ..."},
                bearer_token="token",
                config=RemoteDDS3Config(base_url="https://203.0.113.10", timeout_seconds=1),
            ),
            "DDS3_REMOTE_ENGINE_MISMATCH",
        )

        def refused(*args, **kwargs):
            raise urllib.error.URLError(ConnectionRefusedError(111, "refused"))

        urllib.request.urlopen = refused
        expect_unavailable(
            lambda: compute_remote(
                {"operation": "dd_table", "pbn": "N:A... ... ... ..."},
                bearer_token="token",
                config=RemoteDDS3Config(base_url="https://203.0.113.10", timeout_seconds=1),
            ),
            "DDS3_REMOTE_CONNECTION_REFUSED",
        )

        cert_error = ssl.SSLCertVerificationError(1, "certificate verify failed")

        def bad_cert(*args, **kwargs):
            raise urllib.error.URLError(cert_error)

        urllib.request.urlopen = bad_cert
        expect_unavailable(
            lambda: compute_remote(
                {"operation": "dd_table", "pbn": "N:A... ... ... ..."},
                bearer_token="token",
                config=RemoteDDS3Config(base_url="https://203.0.113.10", timeout_seconds=1),
            ),
            "DDS3_REMOTE_TLS_CERTIFICATE_ERROR",
        )

        missing = remote_engine_readiness(
            bearer_token="",
            config=RemoteDDS3Config(base_url="https://203.0.113.10", timeout_seconds=1),
        )
        assert missing["status"] == "unavailable"
        assert missing["reason"] == "VERCEL_OIDC_TOKEN_MISSING"
        assert missing["fallback_used"] is False

        calls = []

        def fake_ready(request, timeout, context):
            calls.append((request.method, request.full_url, request.headers.get("Authorization")))
            if request.full_url.endswith("/readyz"):
                return _Response({
                    "status": "ready",
                    "engine": "DDS3",
                    "engine_version": "test",
                    "position_solver": "ready",
                    "fallback_used": False,
                })
            return _Response({
                "engine": "DDS3",
                "fallback_used": False,
                "dd_table": GOLDEN_TABLE,
                "par_score_ns": -110,
                "par_contracts": ["2S-EW"],
            })

        urllib.request.urlopen = fake_ready
        ready = remote_engine_readiness(
            bearer_token="oidc",
            config=RemoteDDS3Config(base_url="https://203.0.113.10", timeout_seconds=2),
        )
        assert ready["status"] == "ready"
        assert ready["authenticated_compute"] == "ready"
        assert calls[0][0:2] == ("GET", "https://203.0.113.10/readyz")
        assert calls[1][2] == "Bearer oidc"
    finally:
        urllib.request.urlopen = original

    print("DDS3_REMOTE_CONTRACT: PASS")


if __name__ == "__main__":
    main()
