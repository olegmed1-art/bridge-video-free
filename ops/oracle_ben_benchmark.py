#!/usr/bin/env python3
"""Bounded localhost BEN Pilot-100/500 with DDS3 and memory guardrails."""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

BEN_URL = "http://127.0.0.1:8085"
DDS3_URL = "http://127.0.0.1:8080/v1/compute"
DDS3_ENV = "/opt/bridge-school/dds3-runtime.env"
MAX_STAGE = 500
GOLDEN_DEAL = "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3"
GOLDEN_TABLE = {
    "S": [5, 8, 5, 8],
    "H": [6, 6, 6, 6],
    "D": [5, 7, 5, 7],
    "C": [7, 5, 7, 5],
    "NT": [6, 6, 6, 6],
}


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def latency_summary(values: list[float]) -> dict[str, float]:
    return {
        "mean": round(statistics.fmean(values), 3) if values else 0.0,
        "p50": round(percentile(values, 0.50), 3),
        "p95": round(percentile(values, 0.95), 3),
        "max": round(max(values), 3) if values else 0.0,
    }


def parse_bytes(value: str) -> int:
    text = value.strip()
    units = {
        "B": 1,
        "kB": 1000,
        "KB": 1000,
        "KiB": 1024,
        "MB": 1000**2,
        "MiB": 1024**2,
        "GB": 1000**3,
        "GiB": 1024**3,
    }
    for unit in sorted(units, key=len, reverse=True):
        if text.endswith(unit):
            return int(float(text[: -len(unit)].strip()) * units[unit])
    raise ValueError(f"unsupported memory value: {value!r}")


def deterministic_cases() -> list[dict[str, Any]]:
    # The localhost BEN adapter has one production-certified query contract.
    # Pilot-100/500 measures reliability, latency, throughput and memory for
    # that exact contract; broader auction/deal coverage belongs to the
    # separately bounded functional suites, not this capacity acceptance.
    return [
        {"hand": "AK97543.K.T3.AK7", "seat": "S", "dealer": "N", "vul": "", "auction": []},
    ]


def verify_ben_payload(data: Any) -> str:
    if not isinstance(data, dict):
        raise ValueError("BEN response must be an object")
    selected = data.get("bid") or data.get("call")
    candidates = data.get("candidates")
    if not isinstance(selected, str) or not selected.strip() or not isinstance(candidates, list) or not candidates:
        raise ValueError("BEN selection/candidates contract mismatch")
    selected = selected.strip()
    selected_scored = False
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("BEN candidate must be an object")
        action = candidate.get("call") or candidate.get("bid") or candidate.get("action")
        score = candidate.get("insta_score", candidate.get("score"))
        if not isinstance(action, str) or not action.strip():
            raise ValueError("BEN candidate action missing")
        if score is not None:
            numeric = float(score)
            if not math.isfinite(numeric):
                raise ValueError("BEN candidate score is not finite")
            if action.strip() == selected:
                selected_scored = True
    if not selected_scored:
        raise ValueError("BEN selected action has no finite policy score")
    return selected


def request_json(url: str, *, payload: dict[str, Any] | None = None, token: str = "", timeout: float = 30.0) -> tuple[dict[str, Any], float]:
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(4_000_001)
            if response.status != 200 or len(raw) > 4_000_000:
                raise RuntimeError(f"unexpected HTTP response: {response.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace").replace("\n", " ").strip()
        raise RuntimeError(f"HTTP {exc.code}: {detail[:500]}") from exc
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("response must be a JSON object")
    return data, elapsed_ms


def ben_url(case: dict[str, Any]) -> str:
    context = "-".join(case["auction"]) if case["auction"] else "----"
    query = urllib.parse.urlencode({
        "hand": case["hand"],
        "seat": case["seat"],
        "dealer": case["dealer"],
        "vul": case["vul"],
        "ctx": context,
        "details": "true",
    })
    return f"{BEN_URL}/bid?{query}"


def run_ben_stage(count: int, *, p95_limit_ms: float) -> dict[str, Any]:
    if not 1 <= count <= MAX_STAGE:
        raise ValueError(f"BEN stage must be between 1 and {MAX_STAGE}")
    cases = deterministic_cases()
    latencies: list[float] = []
    bids: dict[str, int] = {}
    failures: list[str] = []
    started = time.perf_counter()
    for index in range(count):
        try:
            data, elapsed_ms = request_json(ben_url(cases[index % len(cases)]), timeout=30.0)
            selected = verify_ben_payload(data)
            latencies.append(elapsed_ms)
            bids[selected] = bids.get(selected, 0) + 1
        except Exception as exc:  # operational report must preserve bounded diagnostics
            failures.append(f"{type(exc).__name__}:{str(exc)[:180]}")
            if len(failures) >= 10:
                break
    elapsed = time.perf_counter() - started
    summary = latency_summary(latencies)
    passed = len(latencies) == count and not failures and summary["p95"] <= p95_limit_ms
    return {
        "requests": count,
        "successes": len(latencies),
        "failures": count - len(latencies),
        "quality_contract": len(latencies) == count and not failures,
        "passed": passed,
        "elapsed_seconds": round(elapsed, 3),
        "throughput_rps": round(len(latencies) / elapsed, 3) if elapsed else 0.0,
        "latency_ms": summary,
        "p95_limit_ms": p95_limit_ms,
        "selected_bids": dict(sorted(bids.items())),
        "errors": failures,
    }


def load_dds3_token(path: str) -> str:
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        if raw.startswith("DDS3_RUNTIME_TOKEN="):
            token = raw.split("=", 1)[1].strip()
            if token:
                return token
    raise RuntimeError("DDS3 runtime token is unavailable")


def run_dds3_probe(token: str, count: int = 10) -> dict[str, Any]:
    payload = {"operation": "dd_table", "pbn": GOLDEN_DEAL, "dealer": "N", "vulnerability": "None"}
    latencies: list[float] = []
    failures: list[str] = []
    for _ in range(count):
        try:
            data, elapsed_ms = request_json(DDS3_URL, payload=payload, token=token, timeout=30.0)
            if data.get("engine") != "DDS3" or data.get("fallback_used") is not False or data.get("dd_table") != GOLDEN_TABLE:
                raise RuntimeError("DDS3 golden/provenance mismatch")
            latencies.append(elapsed_ms)
        except Exception as exc:
            failures.append(f"{type(exc).__name__}:{str(exc)[:180]}")
    return {
        "requests": count,
        "successes": len(latencies),
        "failures": len(failures),
        "passed": len(latencies) == count and not failures,
        "latency_ms": latency_summary(latencies),
        "errors": failures[:10],
    }


def docker_memory() -> dict[str, Any]:
    completed = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", "bridge-ben"],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    data = json.loads(completed.stdout.strip())
    usage_text, limit_text = (part.strip() for part in str(data["MemUsage"]).split("/", 1))
    return {
        "usage": usage_text,
        "usage_bytes": parse_bytes(usage_text),
        "limit": limit_text,
        "limit_bytes": parse_bytes(limit_text),
        "percent": data.get("MemPerc"),
    }


def require_services() -> dict[str, str]:
    states: dict[str, str] = {}
    for unit in (
        "bridge-ben.service",
        "bridge-ben-healthcheck.timer",
        "assistant-lab.service",
        "dds3-healthcheck.timer",
    ):
        completed = subprocess.run(
            ["systemctl", "is-active", unit],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        state = completed.stdout.strip() or completed.stderr.strip() or f"exit-{completed.returncode}"
        states[unit] = state
        if completed.returncode != 0 or state != "active":
            raise RuntimeError(f"required systemd unit is not active: {unit}={state}")
    return states


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stages", default="100,500")
    parser.add_argument("--ben-p95-limit-ms", type=float, default=float(os.getenv("BEN_P95_LIMIT_MS", "5000")))
    parser.add_argument("--ben-memory-limit-bytes", type=int, default=6 * 1024**3)
    parser.add_argument("--ben-memory-delta-limit-bytes", type=int, default=512 * 1024**2)
    parser.add_argument("--dds3-impact-floor-ms", type=float, default=500.0)
    args = parser.parse_args()

    stages = [int(value) for value in args.stages.split(",") if value.strip()]
    if stages != [100, 500]:
        raise SystemExit("operational acceptance requires exact stages 100,500")

    report: dict[str, Any] = {
        "benchmark": "oracle-ben-pilot-100-500-v1",
        "passed": False,
        "ben": {},
        "memory": {},
        "dds3": {},
        "services": {},
    }
    try:
        report["services"]["before"] = require_services()
        token = load_dds3_token(DDS3_ENV)
        memory_before = docker_memory()
        dds3_before = run_dds3_probe(token)
        ben_stages: dict[str, Any] = {}
        for stage in stages:
            ben_stages[str(stage)] = run_ben_stage(stage, p95_limit_ms=args.ben_p95_limit_ms)
            if not ben_stages[str(stage)]["passed"]:
                break
        dds3_after = run_dds3_probe(token)
        memory_after = docker_memory()
        report["services"]["after"] = require_services()

        memory_delta = memory_after["usage_bytes"] - memory_before["usage_bytes"]
        dds3_impact_limit = max(args.dds3_impact_floor_ms, dds3_before["latency_ms"]["p95"] * 2.0)
        memory_passed = (
            memory_after["usage_bytes"] <= args.ben_memory_limit_bytes
            and memory_delta <= args.ben_memory_delta_limit_bytes
        )
        dds3_passed = (
            dds3_before["passed"]
            and dds3_after["passed"]
            and dds3_after["latency_ms"]["p95"] <= dds3_impact_limit
        )
        report["ben"] = ben_stages
        report["memory"] = {
            "before": memory_before,
            "after": memory_after,
            "delta_bytes": memory_delta,
            "usage_limit_bytes": args.ben_memory_limit_bytes,
            "delta_limit_bytes": args.ben_memory_delta_limit_bytes,
            "passed": memory_passed,
        }
        report["dds3"] = {
            "before": dds3_before,
            "after": dds3_after,
            "after_p95_limit_ms": round(dds3_impact_limit, 3),
            "passed": dds3_passed,
        }
        report["passed"] = (
            set(ben_stages) == {"100", "500"}
            and all(stage["passed"] for stage in ben_stages.values())
            and memory_passed
            and dds3_passed
        )
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc)[:500]}
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
