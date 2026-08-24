"""One bounded live Oracle canary for world -> BEN auction -> DDS3 score."""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from typing import Any

from assistant_lab.ben_runtime import compute_ben_policy
from assistant_lab.contract import verify_dds3_result
from assistant_lab.worker import load_config

from .ai_auction_rollout import rollout_worlds
from .ai_auction_scoring import score_rollout_with_dds3
from .ai_worlds import generate_worlds


def _post_dds3(url: str, token: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("BEN_DDS3_CANARY_TRANSPORT_FAILED") from exc
    if len(raw) > 16 * 1024 * 1024:
        raise RuntimeError("BEN_DDS3_CANARY_RESPONSE_TOO_LARGE")
    try:
        result = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("BEN_DDS3_CANARY_INVALID_JSON") from exc
    return verify_dds3_result(result, expected_operation="dd_table")


def run_canary() -> dict[str, Any]:
    config = load_config()
    dealer = "S"
    vulnerability = "NONE"
    ben_vulnerability = ""
    dds3_vulnerability = "None"
    generated = generate_worlds(
        known_seat="S",
        known_hand_pbn="AK97543.K.T3.AK7",
        constraints=None,
        count=1,
        seed=260824,
    )
    if generated.get("complete") is not True or generated.get("accepted") != 1:
        raise RuntimeError("BEN_DDS3_CANARY_WORLD_GENERATION_FAILED")
    worlds = generated["worlds"]

    def ben_bidder(seat: str, hand: str, auction: tuple[str, ...]) -> dict[str, Any]:
        return compute_ben_policy(
            config.ben_url,
            {
                "hand": hand,
                "seat": seat,
                "dealer": dealer,
                "vul": ben_vulnerability,
                "auction": list(auction),
            },
            timeout=config.ben_timeout_seconds,
        )

    rollout = rollout_worlds(
        worlds=worlds,
        dealer=dealer,
        auction=[],
        decision_seat=dealer,
        candidate_call="1S",
        ben_bidder=ben_bidder,
        vulnerability=vulnerability,
        max_worlds=1,
        max_calls_per_world=24,
    )

    dds3_results: dict[str, dict[str, Any]] = {}
    for source_world, auction_world in zip(worlds, rollout["worlds"], strict=True):
        if auction_world.get("passed_out"):
            continue
        fingerprint = str(source_world["fingerprint"])
        deal_pbn_sha256 = hashlib.sha256(source_world["pbn"].encode("utf-8")).hexdigest()
        if deal_pbn_sha256 != auction_world["deal_pbn_sha256"]:
            raise RuntimeError("BEN_DDS3_CANARY_DEAL_BINDING_FAILED")
        dds3_results[fingerprint] = {
            "world_fingerprint": fingerprint,
            "deal_pbn_sha256": deal_pbn_sha256,
            "result": _post_dds3(
                config.dds3_url,
                config.dds3_token,
                {
                    "operation": "dd_table",
                    "pbn": source_world["pbn"],
                    "dealer": dealer,
                    "vulnerability": dds3_vulnerability,
                },
                config.dds3_timeout_seconds,
            ),
        }

    scored = score_rollout_with_dds3(
        rollout=rollout,
        dds3_results=dds3_results,
        vulnerability=vulnerability,
    )
    if not (
        scored.get("complete") is True
        and scored.get("fallback_used") is False
        and scored.get("completed_worlds") == 1
        and scored.get("evidence_class") == "BEN_AUCTION_ROLLOUT_WITH_DDS3_SCORING"
    ):
        raise RuntimeError("BEN_DDS3_CANARY_RESULT_CONTRACT_FAILED")
    return {
        "status": "PASS",
        "engine": scored["engine"],
        "evidence_class": scored["evidence_class"],
        "worlds": scored["completed_worlds"],
        "dds_required_worlds": scored["dds_required_worlds"],
        "fallback_used": scored["fallback_used"],
    }


def main() -> None:
    print(json.dumps(run_canary(), sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()


__all__ = ["run_canary"]
