#!/usr/bin/env python3
"""Fail-closed selector for the Issue #881 temporary paid acceptance VM."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path


PAID_SHAPE = "VM.Standard.E5.Flex"
PAID_OCPUS = Decimal("1")
PAID_MEMORY_GB = Decimal("1")
MAX_OCPUS = Decimal("2")
MAX_MEMORY_GB = Decimal("12")
MAX_RUNTIME_SECONDS = 1800
MAX_COMPUTE_USD = Decimal("1.00")
MAX_HOURLY_RATE_USD = Decimal("0.0265")
PRICE_BASIS = "https://www.oracle.com/cloud/price-list/#compute;reviewed-2026-09-05"
PRICE_BASIS_VALID_UNTIL = datetime(2026, 10, 5, tzinfo=timezone.utc)


class Rejected(ValueError):
    pass


def _load(path: str) -> dict:
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise Rejected("INVALID_JSON_ROOT")
    return value


def _decimal(value: object, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise Rejected(f"INVALID_{label}") from exc
    if not result.is_finite():
        raise Rejected(f"INVALID_{label}")
    return result


def select(
    shapes: dict,
    ocpu_availability: dict,
    memory_availability: dict,
    source_shape: str,
    now_utc: datetime,
) -> dict:
    if now_utc.tzinfo is None or now_utc.astimezone(timezone.utc) >= PRICE_BASIS_VALID_UNTIL:
        raise Rejected("PRICE_BASIS_EXPIRED")
    data = shapes.get("data")
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise Rejected("INVALID_SHAPE_INVENTORY")

    # A restored boot volume must never be moved across processor architecture.
    # This operation's source is E5; requiring the exact shape is stronger than
    # trusting mutable or missing free-form processor descriptions.
    if source_shape != PAID_SHAPE:
        raise Rejected("ARCHITECTURE_OR_SOURCE_SHAPE_MISMATCH")
    matches = [item for item in data if item.get("shape") == PAID_SHAPE]
    if len(matches) != 1:
        raise Rejected("PAID_SHAPE_NOT_UNIQUELY_AVAILABLE")
    shape = matches[0]
    ocpu_options = shape.get("ocpu-options") or {}
    memory_options = shape.get("memory-options") or {}
    if not isinstance(ocpu_options, dict) or not isinstance(memory_options, dict):
        raise Rejected("INVALID_FLEX_OPTIONS")
    min_ocpus = _decimal(ocpu_options.get("min"), "MIN_OCPUS")
    max_ocpus = _decimal(ocpu_options.get("max"), "MAX_OCPUS")
    min_memory = _decimal(memory_options.get("min-in-gbs"), "MIN_MEMORY")
    max_memory = _decimal(memory_options.get("max-in-gbs"), "MAX_MEMORY")
    if not (min_ocpus <= PAID_OCPUS <= max_ocpus <= Decimal("1024")):
        raise Rejected("OCPU_CONFIGURATION_UNAVAILABLE")
    if not (min_memory <= PAID_MEMORY_GB <= max_memory <= Decimal("65536")):
        raise Rejected("MEMORY_CONFIGURATION_UNAVAILABLE")
    if PAID_OCPUS > MAX_OCPUS or PAID_MEMORY_GB > MAX_MEMORY_GB:
        raise Rejected("OWNER_RESOURCE_CAP_EXCEEDED")

    ocpu_limit = ocpu_availability.get("data")
    memory_limit = memory_availability.get("data")
    if not isinstance(ocpu_limit, dict) or not isinstance(memory_limit, dict):
        raise Rejected("INVALID_LIMIT_RESPONSE")
    available_ocpus = _decimal(ocpu_limit.get("available"), "AVAILABLE_OCPUS")
    available_memory = _decimal(memory_limit.get("available"), "AVAILABLE_MEMORY")
    if available_ocpus < PAID_OCPUS:
        raise Rejected("INSUFFICIENT_SERVICE_LIMIT_HEADROOM")
    if available_memory < PAID_MEMORY_GB:
        raise Rejected("INSUFFICIENT_MEMORY_LIMIT_HEADROOM")

    estimated = MAX_HOURLY_RATE_USD * Decimal(MAX_RUNTIME_SECONDS) / Decimal(3600)
    if MAX_RUNTIME_SECONDS > 7200 or estimated > MAX_COMPUTE_USD or MAX_COMPUTE_USD > Decimal("5.00"):
        raise Rejected("COMPUTE_BUDGET_EXCEEDED")

    return {
        "billing_class": "PAID_BOUNDED",
        "compute_budget_usd": str(MAX_COMPUTE_USD),
        "estimated_compute_usd": str(estimated.quantize(Decimal("0.000001"))),
        "hourly_rate_max_usd": str(MAX_HOURLY_RATE_USD),
        "limit_name": "standard-e4-core-count",
        "memory_gb": int(PAID_MEMORY_GB),
        "ocpus": int(PAID_OCPUS),
        "price_basis": PRICE_BASIS,
        "price_basis_valid_until": PRICE_BASIS_VALID_UNTIL.isoformat().replace("+00:00", "Z"),
        "runtime_limit_seconds": MAX_RUNTIME_SECONDS,
        "shape": PAID_SHAPE,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shapes-json", required=True)
    parser.add_argument("--availability-json", required=True)
    parser.add_argument("--memory-availability-json", required=True)
    parser.add_argument("--source-shape", required=True)
    parser.add_argument("--now-utc", required=True)
    args = parser.parse_args()
    try:
        now_utc = datetime.fromisoformat(args.now_utc.replace("Z", "+00:00"))
        result = select(
            _load(args.shapes_json),
            _load(args.availability_json),
            _load(args.memory_availability_json),
            args.source_shape,
            now_utc,
        )
    except (OSError, json.JSONDecodeError, Rejected, ValueError) as exc:
        reason = str(exc) if isinstance(exc, Rejected) else "INVALID_PREFLIGHT_INPUT"
        print(json.dumps({"status": "REJECTED", "reason": reason}, sort_keys=True))
        return 2
    print(json.dumps({"status": "APPROVED", **result}, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
