#!/usr/bin/env python3
"""Validate and sanitize read-only Oracle fleet status evidence."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


SERVERS = {
    "light": {
        "name": "bridge-school-autopilot-lite",
        "shape": "VM.Standard.A1.Flex",
    },
    "heavy": {
        "name": "bridge-school-dds3-frankfurt",
        "shape": "VM.Standard.E5.Flex",
    },
}

LIFECYCLE_STATES = {
    "MOVING",
    "PROVISIONING",
    "RUNNING",
    "STARTING",
    "STOPPED",
    "STOPPING",
}
SERVICE_STATES = {
    "active",
    "activating",
    "deactivating",
    "failed",
    "inactive",
    "not-found",
    "unknown",
}
SERVICE_KEYS = {
    "assistant_lab",
    "autopilot",
    "dds3",
    "universal_video",
    "universal_video_container",
}
PROCESS_KEYS = {"autonomous_video", "book_material", "ffmpeg", "tesseract"}


class EvidenceError(ValueError):
    """Raised when external status evidence violates the bounded contract."""


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("invalid JSON evidence") from exc


def _bounded_text(value: Any, label: str, *, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise EvidenceError(f"invalid {label}")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise EvidenceError(f"invalid {label}")
    return value


def _bounded_number(
    value: Any,
    label: str,
    *,
    minimum: float = 0,
    maximum: float = 1_000_000_000_000,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceError(f"invalid {label}")
    result = float(value)
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise EvidenceError(f"invalid {label}")
    return result


def parse_inventory(payload: Any, target: str) -> list[dict[str, Any]]:
    if target not in {"all", *SERVERS}:
        raise EvidenceError("invalid target")
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise EvidenceError("invalid OCI inventory envelope")
    if len(payload["data"]) > 500:
        raise EvidenceError("OCI inventory is unexpectedly large")
    if not all(isinstance(item, dict) for item in payload["data"]):
        raise EvidenceError("invalid OCI inventory row")

    roles = list(SERVERS) if target == "all" else [target]
    result: list[dict[str, Any]] = []
    for role in roles:
        expected = SERVERS[role]
        matches = [
            item
            for item in payload["data"]
            if item.get("display-name") == expected["name"]
            and item.get("lifecycle-state") not in {"TERMINATED", "TERMINATING"}
        ]
        if len(matches) != 1:
            raise EvidenceError(f"expected exactly one active {role} Oracle instance")
        item = matches[0]
        instance_id = _bounded_text(item.get("id"), f"{role} instance id", maximum=256)
        if not instance_id.startswith("ocid1.instance."):
            raise EvidenceError(f"invalid {role} instance id")
        state = _bounded_text(item.get("lifecycle-state"), f"{role} lifecycle")
        if state not in LIFECYCLE_STATES:
            raise EvidenceError(f"unexpected {role} lifecycle")
        shape = _bounded_text(item.get("shape"), f"{role} shape")
        if shape != expected["shape"]:
            raise EvidenceError(f"unexpected {role} shape")
        compartment_id = _bounded_text(
            item.get("compartment-id"), f"{role} compartment id", maximum=256
        )
        if not compartment_id.startswith(("ocid1.compartment.", "ocid1.tenancy.")):
            raise EvidenceError(f"invalid {role} compartment id")
        shape_config = item.get("shape-config")
        if not isinstance(shape_config, dict):
            raise EvidenceError(f"invalid {role} shape config")
        ocpus = _bounded_number(shape_config.get("ocpus"), f"{role} OCPU", maximum=64)
        memory_gb = _bounded_number(
            shape_config.get("memory-in-gbs"), f"{role} memory", maximum=256
        )
        result.append(
            {
                "role": role,
                "name": expected["name"],
                "instance_id": instance_id,
                "compartment_id": compartment_id,
                "lifecycle": state,
                "shape": shape,
                "ocpus": ocpus,
                "memory_gb": memory_gb,
            }
        )
    return result


def parse_host_execution(payload: Any, expected_role: str) -> dict[str, Any]:
    if expected_role not in SERVERS:
        raise EvidenceError("invalid role")
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise EvidenceError("invalid Run Command envelope")
    data = payload["data"]
    if data.get("lifecycle-state") != "SUCCEEDED":
        raise EvidenceError("Run Command did not succeed")
    content = data.get("content")
    if not isinstance(content, dict) or content.get("exit-code") != 0:
        raise EvidenceError("host probe did not exit successfully")
    text = content.get("text")
    if not isinstance(text, str) or len(text.encode("utf-8")) > 16_384:
        raise EvidenceError("invalid host probe payload")
    try:
        host = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EvidenceError("host probe is not JSON") from exc
    if not isinstance(host, dict) or host.get("schema") != "oracle-fleet-host-status/v1":
        raise EvidenceError("invalid host probe schema")
    if host.get("role") != expected_role:
        raise EvidenceError("host probe role mismatch")

    hostname = _bounded_text(host.get("hostname"), "hostname")
    if not all(char.islower() or char.isdigit() or char in ".-" for char in hostname):
        raise EvidenceError("invalid hostname")
    observed_at_epoch = int(
        _bounded_number(host.get("observed_at_epoch"), "observation time", maximum=4_102_444_800)
    )
    uptime_seconds = int(
        _bounded_number(host.get("uptime_seconds"), "uptime", maximum=20 * 365 * 86_400)
    )
    cpu_logical = int(_bounded_number(host.get("cpu_logical"), "logical CPUs", maximum=256))
    memory_total_kib = int(
        _bounded_number(host.get("memory_total_kib"), "total memory", maximum=2**31)
    )
    memory_available_kib = int(
        _bounded_number(host.get("memory_available_kib"), "available memory", maximum=2**31)
    )
    root_total_kib = int(
        _bounded_number(host.get("root_total_kib"), "root total", maximum=2**40)
    )
    root_available_kib = int(
        _bounded_number(host.get("root_available_kib"), "root available", maximum=2**40)
    )
    if memory_available_kib > memory_total_kib or root_available_kib > root_total_kib:
        raise EvidenceError("available resource exceeds total")

    load = host.get("load")
    if not isinstance(load, list) or len(load) != 3:
        raise EvidenceError("invalid load average")
    load = [_bounded_number(value, "load average", maximum=100_000) for value in load]

    services = host.get("services")
    if not isinstance(services, dict) or set(services) != SERVICE_KEYS:
        raise EvidenceError("invalid service status set")
    if not all(value in SERVICE_STATES for value in services.values()):
        raise EvidenceError("invalid service status")

    processes = host.get("processes")
    if not isinstance(processes, dict) or set(processes) != PROCESS_KEYS:
        raise EvidenceError("invalid process status set")
    sanitized_processes = {
        key: int(_bounded_number(value, f"{key} count", maximum=10_000))
        for key, value in processes.items()
    }

    return {
        "role": expected_role,
        "hostname": hostname,
        "observed_at_epoch": observed_at_epoch,
        "uptime_seconds": uptime_seconds,
        "cpu_logical": cpu_logical,
        "load": load,
        "memory_total_kib": memory_total_kib,
        "memory_available_kib": memory_available_kib,
        "root_total_kib": root_total_kib,
        "root_available_kib": root_available_kib,
        "services": services,
        "processes": sanitized_processes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory = subparsers.add_parser("inventory")
    inventory.add_argument("--input", type=Path, required=True)
    inventory.add_argument("--output", type=Path, required=True)
    inventory.add_argument("--target", choices=("all", "light", "heavy"), required=True)

    host = subparsers.add_parser("host")
    host.add_argument("--input", type=Path, required=True)
    host.add_argument("--output", type=Path, required=True)
    host.add_argument("--role", choices=tuple(SERVERS), required=True)

    args = parser.parse_args()
    try:
        if args.command == "inventory":
            result: Any = parse_inventory(_load(args.input), args.target)
        else:
            result = parse_host_execution(_load(args.input), args.role)
        args.output.write_text(
            json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        return 0
    except EvidenceError as exc:
        parser.exit(2, f"oracle fleet evidence rejected: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
