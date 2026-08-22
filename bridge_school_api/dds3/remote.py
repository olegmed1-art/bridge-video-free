from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .service import DDSUnavailable, DDS_UPSTREAM

GOLDEN_DEAL = "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3"
GOLDEN_TABLE = {
    "S": [5, 8, 5, 8],
    "H": [6, 6, 6, 6],
    "D": [5, 7, 5, 7],
    "C": [7, 5, 7, 5],
    "NT": [6, 6, 6, 6],
}


@dataclass(frozen=True)
class RemoteDDS3Config:
    base_url: str = field(default_factory=lambda: os.getenv("DDS3_REMOTE_URL", "").strip().rstrip("/"))
    timeout_seconds: float = field(default_factory=lambda: float(os.getenv("DDS3_REMOTE_TIMEOUT_SECONDS", "25")))


def _validate_base_url(base_url: str) -> str:
    if not base_url:
        raise DDSUnavailable("DDS3_REMOTE_URL_MISSING")
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise DDSUnavailable("DDS3_REMOTE_URL_INVALID")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise DDSUnavailable("DDS3_REMOTE_URL_INVALID")
    return base_url.rstrip("/")


def _request_json(
    *,
    url: str,
    timeout_seconds: float,
    method: str = "GET",
    payload: dict | None = None,
    bearer_token: str | None = None,
) -> dict:
    body = None
    headers = {"Accept": "application/json", "User-Agent": "bridge-school-dds3-remote/1"}
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout_seconds,
            context=ssl.create_default_context(),
        ) as response:
            if response.status != 200:
                raise DDSUnavailable(f"DDS3_REMOTE_HTTP_{response.status}")
            raw = response.read(4_000_000)
    except urllib.error.HTTPError as exc:
        raise DDSUnavailable(f"DDS3_REMOTE_HTTP_{exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DDSUnavailable("DDS3_REMOTE_UNREACHABLE") from exc
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
        raise DDSUnavailable("DDS3_REMOTE_INVALID_JSON") from exc
    if not isinstance(data, dict):
        raise DDSUnavailable("DDS3_REMOTE_INVALID_RESPONSE")
    return data


def compute_remote(
    payload: dict,
    *,
    bearer_token: str,
    config: RemoteDDS3Config | None = None,
) -> dict:
    cfg = config or RemoteDDS3Config()
    base_url = _validate_base_url(cfg.base_url)
    if not bearer_token:
        raise DDSUnavailable("DDS3_REMOTE_OIDC_MISSING")
    data = _request_json(
        url=f"{base_url}/v1/compute",
        timeout_seconds=cfg.timeout_seconds,
        method="POST",
        payload=payload,
        bearer_token=bearer_token,
    )
    if data.get("engine") != "DDS3" or data.get("fallback_used") is not False:
        raise DDSUnavailable("DDS3_REMOTE_ENGINE_MISMATCH")
    return data


def remote_engine_readiness(
    *,
    bearer_token: str,
    config: RemoteDDS3Config | None = None,
) -> dict:
    cfg = config or RemoteDDS3Config()
    base_url = _validate_base_url(cfg.base_url)
    if not bearer_token:
        return {
            "status": "unavailable",
            "engine": "DDS3",
            "engine_version": DDS_UPSTREAM,
            "reason": "VERCEL_OIDC_TOKEN_MISSING",
            "transport": "remote_https",
            "fallback_used": False,
        }
    try:
        ready = _request_json(
            url=f"{base_url}/readyz",
            timeout_seconds=min(cfg.timeout_seconds, 8.0),
        )
        if ready.get("status") != "ready" or ready.get("engine") != "DDS3" or ready.get("fallback_used") is not False:
            raise DDSUnavailable("DDS3_REMOTE_READINESS_MISMATCH")
        golden = compute_remote(
            {
                "operation": "dd_table",
                "pbn": GOLDEN_DEAL,
                "dealer": "N",
                "vulnerability": "None",
            },
            bearer_token=bearer_token,
            config=cfg,
        )
        if (
            golden.get("dd_table") != GOLDEN_TABLE
            or golden.get("par_score_ns") != -110
            or golden.get("par_contracts") != ["2S-EW"]
        ):
            raise DDSUnavailable("DDS3_REMOTE_SELFTEST_MISMATCH")
    except DDSUnavailable as exc:
        return {
            "status": "unavailable",
            "engine": "DDS3",
            "engine_version": DDS_UPSTREAM,
            "reason": str(exc) or "DDS3_REMOTE_UNAVAILABLE",
            "transport": "remote_https",
            "fallback_used": False,
        }
    return {
        "status": "ready",
        "engine": "DDS3",
        "engine_version": ready.get("engine_version", DDS_UPSTREAM),
        "position_solver": ready.get("position_solver", "ready"),
        "transport": "remote_https",
        "authenticated_compute": "ready",
        "fallback_used": False,
    }
