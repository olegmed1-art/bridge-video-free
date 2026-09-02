#!/usr/bin/env python3
"""Bounded localhost-only DDS3 concurrency probe.

This is an operational measurement tool, not a production worker-pool switch.
It intentionally refuses remote endpoints and caps concurrency at 8 so we can
measure the existing Oracle DDS3 runtime before changing resident worker count.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
MAX_CONCURRENCY = 8
MAX_REQUESTS = 200


@dataclass(frozen=True)
class Sample:
    ok: bool
    latency_ms: float
    engine: str | None
    fallback_used: bool | None
    operation: str | None
    error: str | None = None


def validate_url(raw: str) -> str:
    value = raw.strip()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "http" or (parsed.hostname or "").lower() not in LOCAL_HOSTS:
        raise ValueError("probe endpoint must be localhost HTTP")
    if parsed.port != 8080 or parsed.path != "/v1/compute":
        raise ValueError("probe endpoint must be localhost:8080/v1/compute")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("probe endpoint contains forbidden URL components")
    return value


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * p)))
    return ordered[index]


def post_one(url: str, token: str, payload: dict[str, Any], timeout: float) -> Sample:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read(2 * 1024 * 1024 + 1).decode("utf-8"))
        latency = (time.perf_counter() - started) * 1000.0
        ok = (
            data.get("engine") == "DDS3"
            and data.get("fallback_used") is False
            and data.get("operation") == payload.get("operation", "dd_table")
        )
        return Sample(
            ok=ok,
            latency_ms=latency,
            engine=data.get("engine"),
            fallback_used=data.get("fallback_used"),
            operation=data.get("operation"),
            error=None if ok else "PROVENANCE_MISMATCH",
        )
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return Sample(
            ok=False,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            engine=None,
            fallback_used=None,
            operation=None,
            error=f"{type(exc).__name__}: {exc}"[:500],
        )


def run_level(
    *, url: str, token: str, payload: dict[str, Any], concurrency: int, requests: int, timeout: float
) -> dict[str, Any]:
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        samples = list(
            pool.map(
                lambda _: post_one(url, token, payload, timeout),
                range(requests),
            )
        )
    elapsed = time.perf_counter() - started
    latencies = [sample.latency_ms for sample in samples]
    successes = sum(sample.ok for sample in samples)
    return {
        "concurrency": concurrency,
        "requests": requests,
        "successes": successes,
        "failures": requests - successes,
        "success_rate": successes / requests if requests else 0.0,
        "elapsed_seconds": round(elapsed, 6),
        "throughput_rps": round(requests / elapsed, 3) if elapsed > 0 else 0.0,
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 3) if latencies else 0.0,
            "p50": round(percentile(latencies, 0.50), 3),
            "p95": round(percentile(latencies, 0.95), 3),
            "max": round(max(latencies), 3) if latencies else 0.0,
        },
        "errors": [asdict(sample) for sample in samples if not sample.ok][:10],
    }


def parse_levels(raw: str) -> list[int]:
    levels = sorted({int(value.strip()) for value in raw.split(",") if value.strip()})
    if not levels or levels[0] < 1 or levels[-1] > MAX_CONCURRENCY:
        raise ValueError(f"concurrency levels must be between 1 and {MAX_CONCURRENCY}")
    return levels


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080/v1/compute")
    parser.add_argument("--operation", choices=("dd_table", "position_all_moves"), default="dd_table")
    parser.add_argument("--pbn", default=os.getenv("DDS3_PROBE_PBN", ""))
    parser.add_argument("--position-json", default=os.getenv("DDS3_PROBE_POSITION_JSON", ""))
    parser.add_argument("--levels", default="1,2,4")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=25.0)
    args = parser.parse_args()

    url = validate_url(args.url)
    token = os.getenv("DDS3_RUNTIME_TOKEN", "").strip()
    if not token:
        raise SystemExit("DDS3_RUNTIME_TOKEN is required")
    if not (1 <= args.requests <= MAX_REQUESTS):
        raise SystemExit(f"--requests must be between 1 and {MAX_REQUESTS}")
    levels = parse_levels(args.levels)

    if args.operation == "dd_table":
        if not args.pbn.strip():
            raise SystemExit("--pbn or DDS3_PROBE_PBN is required")
        payload: dict[str, Any] = {"operation": "dd_table", "pbn": args.pbn.strip()}
    else:
        if not args.position_json.strip():
            raise SystemExit("--position-json or DDS3_PROBE_POSITION_JSON is required")
        position = json.loads(args.position_json)
        if not isinstance(position, dict):
            raise SystemExit("position JSON must be an object")
        payload = {"operation": "position_all_moves", "position": position}

    report = {
        "probe": "dds3-local-concurrency-v1",
        "endpoint": url,
        "operation": args.operation,
        "max_allowed_concurrency": MAX_CONCURRENCY,
        "levels": [],
    }
    for level in levels:
        report["levels"].append(
            run_level(
                url=url,
                token=token,
                payload=payload,
                concurrency=level,
                requests=args.requests,
                timeout=args.timeout,
            )
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
