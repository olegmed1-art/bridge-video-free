#!/usr/bin/env python3
"""Fail closed unless Universal Video has exactly one declared production route."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

if __package__:
    from .validate_universal_video_feature_parity import (
        FeatureParityError,
        require_feature_parity_pass,
    )
else:
    from validate_universal_video_feature_parity import (
        FeatureParityError,
        require_feature_parity_pass,
    )


ROOT = Path(__file__).resolve().parents[1]
ROUTING_FILE = ROOT / "ops/universal-video-runtime-routing.json"
EXPECTED_SCHEMA = "universal-video-runtime-routing-v1"


class RoutingContractError(RuntimeError):
    pass


def load_and_validate() -> dict[str, object]:
    try:
        routing = json.loads(ROUTING_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RoutingContractError("UV_RUNTIME_ROUTING_UNREADABLE") from exc

    if routing.get("schema") != EXPECTED_SCHEMA:
        raise RoutingContractError("UV_RUNTIME_ROUTING_SCHEMA_INVALID")
    routes = routing.get("routes")
    active = routing.get("active_production_route")
    if not isinstance(routes, dict) or active not in routes:
        raise RoutingContractError("UV_RUNTIME_ROUTING_ACTIVE_INVALID")

    defaults = [
        name
        for name, route in routes.items()
        if isinstance(route, dict) and route.get("production_default") is True
    ]
    if defaults != [active]:
        raise RoutingContractError("UV_RUNTIME_ROUTING_NOT_SINGLE_ACTIVE")

    for name, route in routes.items():
        if not isinstance(name, str) or not isinstance(route, dict):
            raise RoutingContractError("UV_RUNTIME_ROUTING_ROUTE_INVALID")
        entrypoint = route.get("entrypoint")
        if not isinstance(entrypoint, str) or not (ROOT / entrypoint).is_file():
            raise RoutingContractError("UV_RUNTIME_ROUTING_ENTRYPOINT_MISSING")
    if active == routing.get("policy_target_route"):
        try:
            require_feature_parity_pass()
        except FeatureParityError as exc:
            raise RoutingContractError(str(exc)) from exc
    return routing


def require_active(routing: dict[str, object], required: str) -> None:
    if routing["active_production_route"] != required:
        raise RoutingContractError("UV_RUNTIME_ROUTE_RETIRED")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-active", required=True)
    args = parser.parse_args()
    try:
        routing = load_and_validate()
        require_active(routing, args.require_active)
    except RoutingContractError as exc:
        print(str(exc))
        return 78
    print(f"UNIVERSAL_VIDEO_RUNTIME_ROUTING_PASS active={args.require_active}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
