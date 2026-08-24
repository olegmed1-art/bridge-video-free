"""Bounded localhost BEN policy adapter for Assistant Lab.

BEN output is tagged POLICY_ONLY and is never promoted to DDS/search evidence.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .contract import LabContractError, verify_ben_result

LOCAL_BEN_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class RetryableBenError(RuntimeError):
    pass


def validate_local_ben_url(raw: str) -> str:
    value = raw.strip()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "http" or (parsed.hostname or "").lower() not in LOCAL_BEN_HOSTS:
        raise RuntimeError("assistant-lab BEN endpoint must be localhost HTTP")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("assistant-lab BEN endpoint contains forbidden URL components")
    if parsed.port != 8085 or parsed.path not in {"", "/"}:
        raise RuntimeError("assistant-lab BEN endpoint must be localhost:8085")
    return value.rstrip("/")


def _auction_context(auction: list[str]) -> str:
    return "-".join(call.strip() for call in auction) if auction else "----"


def compute_ben_policy(base_url: str, payload: dict[str, Any], *, timeout: float = 25.0) -> dict[str, Any]:
    params = {
        "hand": payload["hand"],
        "seat": payload["seat"],
        "dealer": payload["dealer"],
        "vul": payload.get("vul", ""),
        "ctx": _auction_context(payload.get("auction", [])),
        "details": "true",
    }
    if payload.get("scoring"):
        params["tournament"] = str(payload["scoring"])
    url = f"{base_url}/bid?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise LabContractError("BEN response exceeds assistant-lab limit")
            result = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429 or exc.code >= 500:
            raise RetryableBenError(f"BEN_HTTP_{exc.code}") from exc
        raise LabContractError(f"BEN_HTTP_{exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RetryableBenError("BEN_LOCAL_TRANSPORT_FAILED") from exc
    except json.JSONDecodeError as exc:
        raise RetryableBenError("BEN_LOCAL_INVALID_JSON") from exc
    return verify_ben_result(result)
