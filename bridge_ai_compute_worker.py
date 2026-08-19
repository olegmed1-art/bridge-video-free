"""Standalone Hybrid Cloud compute worker for Bridge Decision Engine.

This process is intentionally provider-neutral. It claims queued search jobs from the
Bridge School API, invokes configured external engines, and posts structured results
back. It can run in any container/VM/serverless environment with outbound HTTPS.

The worker never invents bridge results. If no engine endpoint is configured, it
reports the job as FAILED with diagnostics rather than fabricating evaluations.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Config:
    api_base: str
    api_token: str
    ben_url: str | None
    pons_url: str | None
    poll_seconds: float


def load_config() -> Config:
    base = os.environ.get("BRIDGE_API_BASE_URL", "").rstrip("/")
    token = os.environ.get("BRIDGE_API_TOKEN", "")
    if not base or not token:
        raise RuntimeError("BRIDGE_API_BASE_URL and BRIDGE_API_TOKEN are required")
    return Config(
        api_base=base,
        api_token=token,
        ben_url=(os.environ.get("BEN_API_URL") or "").rstrip("/") or None,
        pons_url=(os.environ.get("PONS_API_URL") or "").rstrip("/") or None,
        poll_seconds=float(os.environ.get("BRIDGE_WORKER_POLL_SECONDS", "5")),
    )


def request_json(url: str, *, token: str | None = None, method: str = "GET", payload: Any = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ben_bid(config: Config, position: dict[str, Any]) -> dict[str, Any] | None:
    """Ask a BEN REST service for a bidding recommendation when configured."""
    if not config.ben_url:
        return None
    hand = position.get("hand_pbn")
    auction = position.get("auction") or ""
    seat = position.get("seat")
    dealer = position.get("dealer")
    vul = position.get("vulnerability")
    if not all((hand, seat, dealer)):
        return None
    from urllib.parse import urlencode

    query = urlencode({"hand": hand, "seat": seat, "dealer": dealer, "vul": vul or "", "ctx": auction})
    return request_json(f"{config.ben_url}/bid?{query}")


def choose_engine(config: Config, job: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    position = job.get("position") or {}
    ben = ben_bid(config, position)
    if ben is not None:
        return "ben", ben
    raise RuntimeError("no configured decision engine could evaluate this job")


def evaluation_from_teacher(job: dict[str, Any], engine_key: str, engine_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Map only explicit engine candidate scores; do not manufacture search EV."""
    candidates = job.get("candidates") or []
    by_action = {str(c.get("action")): c for c in candidates}
    raw_candidates = engine_result.get("candidates") or []
    out: list[dict[str, Any]] = []
    for item in raw_candidates:
        action = str(item.get("bid") or item.get("call") or item.get("action") or "")
        candidate = by_action.get(action)
        if not candidate:
            continue
        score = item.get("score")
        if score is None:
            score = item.get("insta_score")
        out.append({
            "candidate_id": candidate["candidate_id"],
            "rollout_policy": engine_key,
            "metrics_json": {"engine_candidate_score": score, "engine_raw": item},
        })
    return out


def process_one(config: Config) -> bool:
    claim = request_json(f"{config.api_base}/v1/ai/search-runs/claim", token=config.api_token, method="POST", payload={})
    if not claim.get("claimed"):
        return False
    run = claim["search_run"]
    run_id = run["search_run_id"]
    try:
        engine_key, result = choose_engine(config, claim)
        evaluations = evaluation_from_teacher(claim, engine_key, result)
        if not evaluations:
            raise RuntimeError("engine returned no candidate results matching the queued position")
        completion = {
            "status": "COMPLETED",
            "evaluations": evaluations,
        }
    except Exception as exc:  # worker must always close the claimed job
        print(f"search_run {run_id} failed: {exc}", file=sys.stderr)
        completion = {"status": "FAILED", "evaluations": []}
    request_json(
        f"{config.api_base}/v1/ai/search-runs/{run_id}/complete",
        token=config.api_token,
        method="POST",
        payload=completion,
    )
    return True


def main() -> int:
    config = load_config()
    once = os.environ.get("BRIDGE_WORKER_ONCE", "").lower() in {"1", "true", "yes"}
    while True:
        try:
            had_job = process_one(config)
        except urllib.error.HTTPError as exc:
            print(f"API HTTP error {exc.code}: {exc.read().decode('utf-8', errors='replace')}", file=sys.stderr)
            had_job = False
        except Exception as exc:
            print(f"worker loop error: {exc}", file=sys.stderr)
            had_job = False
        if once:
            return 0
        if not had_job:
            time.sleep(config.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
