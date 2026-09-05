#!/usr/bin/env python3
"""Strictly bind issue #881 cleanup to an authoritative failed-run receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys


RESOURCE_FIELDS = {
    "instance": "temporary instance ID",
    "boot-volume": "temporary restored-volume ID",
    "vcn": "temporary VCN ID",
    "internet-gateway": "temporary internet-gateway ID",
    "route-table": "temporary route-table ID",
    "security-list": "temporary security-list ID",
    "subnet": "temporary subnet ID",
}

OCID_TYPES = {
    "instance": "instance",
    "boot-volume": "bootvolume",
    "vcn": "vcn",
    "internet-gateway": "internetgateway",
    "route-table": "routetable",
    "security-list": "securitylist",
    "subnet": "subnet",
}

OCID_RE = re.compile(r"^ocid1\.([a-z][a-z0-9]*)\.oc1\.[a-z0-9.-]*\.[a-z0-9]+$")


def _field(body: str, label: str) -> str:
    matches = re.findall(rf"^- {re.escape(label)}: `([^`]+)`\s*$", body, re.MULTILINE)
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {label!r} field")
    return matches[0]


def parse_receipt(body: str, run_id: str, operation_prefix: str) -> dict[str, object]:
    if _field(body, "operation") != "EXPAND_AND_RECOVER":
        raise ValueError("receipt is not an expand-and-recover operation")
    if _field(body, "workflow outcome") != "failure":
        raise ValueError("receipt is not a failed workflow")

    stamp = _field(body, "current-run stamp")
    expected_stamp = f"{operation_prefix}{run_id}-a1"
    if stamp != expected_stamp:
        raise ValueError("receipt stamp does not match the requested failed run")
    run_urls = re.findall(r"^- run: (https://github\.com/[^\s]+/actions/runs/(\d+))\s*$", body, re.MULTILINE)
    if len(run_urls) != 1 or run_urls[0][1] != run_id:
        raise ValueError("receipt run URL does not match the requested failed run")

    create_status = _field(body, "isolated instance create request")
    resources: dict[str, list[str]] = {}
    for kind, label in RESOURCE_FIELDS.items():
        raw = _field(body, label)
        if raw == "none":
            resources[kind] = []
            continue
        match = OCID_RE.fullmatch(raw)
        if match is None or match.group(1) != OCID_TYPES[kind]:
            raise ValueError(f"invalid {kind} OCID")
        resources[kind] = [raw]

    if not resources["instance"] and create_status != "REQUEST_UNCERTAIN":
        raise ValueError("missing instance ID is allowed only for an uncertain launch")
    required = set(RESOURCE_FIELDS) - {"instance"}
    missing = sorted(kind for kind in required if len(resources[kind]) != 1)
    if missing:
        raise ValueError("failed receipt is missing authoritative resources: " + ",".join(missing))

    return {
        "run_id": run_id,
        "stamp": stamp,
        "instance_create_status": create_status,
        "resources": resources,
        "receipt_sha256": hashlib.sha256(body.encode()).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--operation-prefix", required=True)
    args = parser.parse_args()
    try:
        result = parse_receipt(sys.stdin.read(), args.run_id, args.operation_prefix)
    except (ValueError, TypeError) as exc:
        print(f"invalid authoritative receipt: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
