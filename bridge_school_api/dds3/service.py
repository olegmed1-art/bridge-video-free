"""Universal fail-closed Bridge School DDS3 computation service."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from typing import Any

from .model import BridgeDeal
from .position_runtime import PositionWorkerUnavailable, solve_position_all_moves, solve_position_trajectory
from .screenshot import ObservedField, ScreenshotDealObservation

DDS_UPSTREAM = "v3.0.0+cdd13cf5b700788ac8c1391501b42445b3129b45"
DDS3_HAND_ORDER = ("N", "E", "S", "W")
DDS3_STRAIN_ORDER = ("S", "H", "D", "C", "NT")


class DDSUnavailable(RuntimeError):
    pass


def canonical_dds3_table_request(
    *, pbn: str, dealer: str = "N", vulnerability: str = "None"
) -> dict[str, str]:
    normalized_pbn = str(pbn or "").strip()
    if not normalized_pbn:
        raise ValueError("pbn is required")
    normalized_dealer = str(dealer or "").strip().upper()
    if normalized_dealer not in DDS3_HAND_ORDER:
        raise ValueError("dealer is invalid")
    raw_vulnerability = str(vulnerability or "").strip().upper().replace("-", "").replace("_", "")
    vulnerability_aliases = {
        "": "None",
        "NONE": "None",
        "LOVE": "None",
        "NS": "NS",
        "EW": "EW",
        "BOTH": "Both",
        "ALL": "Both",
    }
    try:
        normalized_vulnerability = vulnerability_aliases[raw_vulnerability]
    except KeyError as exc:
        raise ValueError("vulnerability is invalid") from exc
    return {
        "operation": "dd_table",
        "pbn": normalized_pbn,
        "dealer": normalized_dealer,
        "vulnerability": normalized_vulnerability,
    }


def dds3_table_request_sha256(request: dict[str, Any]) -> str:
    canonical = canonical_dds3_table_request(
        pbn=request.get("pbn", ""),
        dealer=request.get("dealer", "N"),
        vulnerability=request.get("vulnerability", "None"),
    )
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class DDS3Config:
    executable: str = os.getenv("DDS3_CLI", "/opt/bridge-school-dds3/dds_pbn_cli")
    timeout_seconds: float = float(os.getenv("DDS3_TIMEOUT_SECONDS", "15"))


def solve_table(*, pbn: str, dealer: str = "N", vulnerability: str = "None", config: DDS3Config | None = None) -> dict[str, Any]:
    cfg = config or DDS3Config()
    request = canonical_dds3_table_request(pbn=pbn, dealer=dealer, vulnerability=vulnerability)
    try:
        proc = subprocess.run(
            [cfg.executable, request["dealer"], request["vulnerability"], request["pbn"]],
            check=False,
            capture_output=True,
            text=True,
            timeout=cfg.timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DDSUnavailable("DDS_UNAVAILABLE") from exc
    if proc.returncode != 0:
        raise DDSUnavailable("DDS_UNAVAILABLE")
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise DDSUnavailable("DDS_UNAVAILABLE") from exc
    result.update(
        {
            "engine": "DDS3",
            "engine_version": DDS_UPSTREAM,
            "operation": "dd_table",
            "input_validated": True,
            "fallback_used": False,
            "deal_pbn_sha256": hashlib.sha256(request["pbn"].encode("utf-8")).hexdigest(),
            "request_sha256": dds3_table_request_sha256(request),
        }
    )
    return result


def solve_deal(deal: BridgeDeal, *, config: DDS3Config | None = None) -> dict[str, Any]:
    deal.validate()
    return solve_table(pbn=deal.to_pbn(), dealer=deal.dealer, vulnerability=deal.vulnerability, config=config)


def solve_screenshot_observation(
    observation: ScreenshotDealObservation, *, config: DDS3Config | None = None
) -> dict[str, Any]:
    """Vision-adapter output -> canonical deal -> DDS3, preserving metadata provenance."""
    deal, provenance = observation.canonicalize()
    result = solve_deal(deal, config=config)
    result["board"] = provenance
    return result


def _field(v: Any) -> ObservedField | None:
    if v is None:
        return None
    if isinstance(v, dict):
        return ObservedField(v.get("value"), v.get("confidence"), v.get("source", "screenshot"))
    return ObservedField(v)


def compute(request: dict[str, Any], *, config: DDS3Config | None = None) -> dict[str, Any]:
    operation = request.get("operation", "dd_table")
    if operation == "position_all_moves":
        position = request.get("position")
        if not isinstance(position, dict):
            raise ValueError("position is required")
        try:
            result = solve_position_all_moves(position)
        except PositionWorkerUnavailable as exc:
            raise DDSUnavailable("DDS_UNAVAILABLE") from exc
        result["engine_version"] = DDS_UPSTREAM
        return result
    if operation == "position_trajectory":
        positions = request.get("positions")
        if not isinstance(positions, list):
            raise ValueError("positions are required")
        perspective = str(request.get("perspective") or "")
        try:
            result = solve_position_trajectory(positions, perspective=perspective)
        except PositionWorkerUnavailable as exc:
            raise DDSUnavailable("DDS_UNAVAILABLE") from exc
        result["engine_version"] = DDS_UPSTREAM
        return result
    if operation != "dd_table":
        raise ValueError(f"unsupported DDS3 operation: {operation}")
    if "screenshot_observation" in request:
        raw = request["screenshot_observation"]
        extra = {k: _field(v) for k, v in raw.get("extra_metadata", {}).items()}
        obs = ScreenshotDealObservation(
            hands=raw["hands"],
            board_number=_field(raw.get("board_number")),
            dealer=_field(raw.get("dealer")),
            vulnerability=_field(raw.get("vulnerability")),
            hand_confidence=raw.get("hand_confidence", {}),
            extra_metadata={k: v for k, v in extra.items() if v is not None},
        )
        return solve_screenshot_observation(obs, config=config)
    if "deal" in request:
        raw = request["deal"]
        return solve_deal(
            BridgeDeal(raw["hands"], raw.get("dealer", "N"), raw.get("vulnerability", "None")),
            config=config,
        )
    return solve_table(
        pbn=request["pbn"],
        dealer=request.get("dealer", "N"),
        vulnerability=request.get("vulnerability", "None"),
        config=config,
    )


__all__ = [
    "DDS3Config", "DDS3_HAND_ORDER", "DDS3_STRAIN_ORDER", "DDSUnavailable", "DDS_UPSTREAM",
    "canonical_dds3_table_request", "compute", "dds3_table_request_sha256", "solve_deal",
    "solve_screenshot_observation", "solve_table",
]
