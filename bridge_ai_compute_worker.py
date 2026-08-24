"""Standalone Hybrid Cloud compute worker for Bridge Decision Engine.

Provider-neutral worker. It claims queued search jobs from the Bridge School API,
invokes configured external engines, stores policy/teacher evidence separately from
search evidence, and posts only explicit simulation metrics as search evaluation.

The worker never manufactures bridge EV. Policy scores may produce POLICY_ONLY
finalization through the finalizer, but are never relabeled as search EV.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


BEN_POLICY_ONLY_STATUS = "NO_SEARCH_EVIDENCE"


@dataclass(frozen=True)
class Config:
    api_base: str
    api_token: str
    ben_url: str | None
    pons_url: str | None
    poll_seconds: float
    ben_attempts: int = 3
    ben_retry_seconds: float = 1.0


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
        ben_attempts=max(1, int(os.environ.get("BEN_MAX_ATTEMPTS", "3"))),
        ben_retry_seconds=max(0.0, float(os.environ.get("BEN_RETRY_SECONDS", "1"))),
    )


def request_json(url: str, *, token: str | None = None, method: str = "GET", payload: Any = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _ben_context(auction: Any) -> str:
    """Normalize stored auction data to BEN's compact 2-character ctx encoding."""
    if isinstance(auction, (list, tuple)):
        tokens = [str(item).strip().upper() for item in auction if str(item).strip()]
    else:
        text = str(auction or "").replace("–", " ").replace("—", " ")
        tokens = [t.strip().upper() for t in text.split() if t.strip()]
    mapping = {
        "PASS": "--", "P": "--", "--": "--",
        "X": "Db", "DBL": "Db", "DOUBLE": "Db",
        "XX": "Rd", "RDBL": "Rd", "REDOUBLE": "Rd",
    }
    return "".join(mapping.get(token, token.replace("NT", "N")) for token in tokens)


def _ben_hand(hand: Any) -> str:
    """Normalize a school PBN hand to BEN's PBN parser contract.

    The school corpus uses '-' for a void suit. BEN expects the standard empty PBN
    suit segment, e.g. ``K987.J875.AJ987.`` rather than ``K987.J875.AJ987.-``.
    """
    text = str(hand or "").strip().replace("_", ".")
    parts = text.split(".")
    if len(parts) != 4:
        return text
    return ".".join("" if part.strip() in {"-", "—"} else part.strip() for part in parts)


def _validate_ben_result(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise RuntimeError("BEN returned a non-object response")
    bid = result.get("bid") or result.get("call")
    if not isinstance(bid, str) or not bid.strip():
        raise RuntimeError("BEN response contains no bid")
    candidates = result.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise RuntimeError("BEN response contains no candidates")
    scored = 0
    actions: set[str] = set()
    for item in candidates:
        if not isinstance(item, dict):
            raise RuntimeError("BEN candidate is not an object")
        action = item.get("call") or item.get("bid") or item.get("action")
        if not isinstance(action, str) or not action.strip():
            raise RuntimeError("BEN candidate contains no action")
        actions.add(action.strip())
        score = item.get("insta_score")
        if score is None:
            score = item.get("score")
        if score is not None:
            if isinstance(score, bool):
                raise RuntimeError("BEN candidate score is not numeric")
            try:
                numeric = float(score)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("BEN candidate score is not numeric") from exc
            if not math.isfinite(numeric):
                raise RuntimeError("BEN candidate score is not finite")
            scored += 1
    if scored == 0:
        raise RuntimeError("BEN response contains no policy scores")
    if bid.strip() not in actions:
        raise RuntimeError("BEN selected bid is absent from candidates")
    return result


def _ben_request(config: Config, url: str) -> dict[str, Any]:
    for attempt in range(1, config.ben_attempts + 1):
        try:
            return _validate_ben_result(request_json(url))
        except urllib.error.HTTPError as exc:
            retryable = exc.code == 429 or exc.code >= 500
            if not retryable or attempt == config.ben_attempts:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == config.ben_attempts:
                raise
        if config.ben_retry_seconds:
            time.sleep(config.ben_retry_seconds * attempt)
    raise RuntimeError("BEN retry loop exhausted")


def ben_bid(config: Config, position: dict[str, Any]) -> dict[str, Any] | None:
    if not config.ben_url:
        return None
    hand = position.get("hand_pbn")
    auction = position.get("auction_json")
    if auction is None:
        auction = position.get("auction") or []
    seat = position.get("seat")
    dealer = position.get("dealer")
    vul = position.get("vulnerability")
    scoring = position.get("scoring")
    if not all((hand, seat, dealer)):
        return None
    from urllib.parse import urlencode

    params = {
        "hand": _ben_hand(hand),
        "seat": seat,
        "dealer": dealer,
        "vul": vul or "",
        "ctx": _ben_context(auction),
        "details": "true",
    }
    if scoring:
        normalized = str(scoring).lower()
        if normalized in {"mp", "matchpoint", "matchpoints"}:
            params["tournament"] = "mp"
        elif normalized in {"imp", "imps"}:
            params["tournament"] = "imps"
    return _ben_request(config, f"{config.ben_url}/bid?{urlencode(params)}")


def choose_engine(config: Config, job: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    ben = ben_bid(config, job.get("position") or {})
    if ben is not None:
        return "ben", ben
    raise RuntimeError("no configured decision engine could evaluate this job")


def teacher_payload(engine_key: str, engine_result: dict[str, Any]) -> dict[str, Any]:
    scores: dict[str, Any] = {}
    for item in engine_result.get("candidates") or []:
        action = str(item.get("call") or item.get("bid") or item.get("action") or "")
        if not action:
            continue
        score = item.get("insta_score")
        if score is None:
            score = item.get("score")
        if score is not None:
            scores[action] = score
    return {
        "teacher_key": engine_key,
        "teacher_version": str(engine_result.get("version") or engine_result.get("model_version") or "") or None,
        "teacher_system": str(engine_result.get("system") or "") or None,
        "action": engine_result.get("bid") or engine_result.get("call"),
        "confidence": None,
        "candidate_scores": scores,
        "explanation": None,
        "raw_output": engine_result,
    }


def policy_payload(engine_key: str, teacher: dict[str, Any]) -> dict[str, Any] | None:
    scores = teacher.get("candidate_scores") or {}
    if not scores:
        return None
    return {
        "model_key": engine_key,
        "model_version": teacher.get("teacher_version") or "NOT_SPECIFIED",
        "distribution": scores,
        "top_action": teacher.get("action"),
        "entropy": None,
    }


def search_evaluations(job: dict[str, Any], engine_key: str, engine_result: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = job.get("candidates") or []
    by_action = {str(c.get("action")): c for c in candidates}
    out: list[dict[str, Any]] = []
    for item in engine_result.get("candidates") or []:
        action = str(item.get("call") or item.get("bid") or item.get("action") or "")
        candidate = by_action.get(action)
        if not candidate:
            continue
        expected_score = item.get("expected_score_sd")
        expected_tricks = item.get("expected_tricks_sd")
        p_make = item.get("p_make_contract")
        if expected_score is None and expected_tricks is None and p_make is None:
            continue
        out.append({
            "candidate_id": candidate["candidate_id"],
            "rollout_policy": engine_key,
            "raw_score_ev": expected_score,
            "make_probability": p_make,
            "metrics_json": {
                "expected_tricks_sd": expected_tricks,
                "expected_score_sd": expected_score,
                "p_make_contract": p_make,
                "insta_score": item.get("insta_score"),
                "engine_raw": item,
                "evidence_class": "BEN_SIMULATION",
            },
        })
    return out


def process_one(config: Config) -> bool:
    claim = request_json(
        f"{config.api_base}/v1/ai/search-runs/claim",
        token=config.api_token,
        method="POST",
        payload={},
    )
    if not claim.get("claimed"):
        return False
    run = claim["search_run"]
    run_id = run["search_run_id"]
    position_id = run["position_id"]
    try:
        engine_key, result = choose_engine(config, claim)
        teacher = teacher_payload(engine_key, result)
        request_json(
            f"{config.api_base}/v1/ai/positions/{position_id}/teacher-evidence",
            token=config.api_token,
            method="POST",
            payload=teacher,
        )

        eval_job = dict(claim)
        policy = policy_payload(engine_key, teacher)
        if policy is not None:
            policy_result = request_json(
                f"{config.api_base}/v1/ai/positions/{position_id}/policy-evidence",
                token=config.api_token,
                method="POST",
                payload=policy,
            )
            eval_job["candidates"] = policy_result.get("candidates") or claim.get("candidates") or []

        evaluations = search_evaluations(eval_job, engine_key, result)
        if evaluations:
            samples = result.get("samples") or []
            completion = {
                "status": "COMPLETED",
                "samples_generated": len(samples) or None,
                "samples_accepted": len(samples) or None,
                "evaluations": evaluations,
            }
        else:
            print(
                f"search_run {run_id}: BEN policy evidence recorded; "
                "no explicit simulation metrics",
                file=sys.stderr,
            )
            completion = {"status": BEN_POLICY_ONLY_STATUS, "evaluations": []}
    except Exception as exc:
        print(f"search_run {run_id} failed: {exc}", file=sys.stderr)
        completion = {"status": "FAILED", "evaluations": []}

    request_json(
        f"{config.api_base}/v1/ai/search-runs/{run_id}/complete",
        token=config.api_token,
        method="POST",
        payload=completion,
    )

    try:
        finalized = request_json(
            f"{config.api_base}/v1/ai/positions/{position_id}/finalize",
            token=config.api_token,
            method="POST",
            payload={},
        )
        print(json.dumps({"position_id": position_id, "finalizer": finalized.get("status")}, ensure_ascii=False))
    except Exception as exc:
        print(f"position {position_id} finalization failed: {exc}", file=sys.stderr)
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
