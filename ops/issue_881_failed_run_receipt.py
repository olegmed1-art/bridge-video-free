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

CLEANUP_TYPED_PROOF_REQUIREMENTS = {
    "instance": {
        "prior_instance_proof": "DIRECT_GET_TERMINAL_AND_REPEATED_STAMP_INVENTORY_NO_ACTIVE",
    },
    "restored_volume": {
        "prior_boot_volume_proof": "DIRECT_GET_TERMINAL_AND_REPEATED_STAMP_INVENTORY_NO_ACTIVE",
    },
    "vcn": {
        "prior_vcn_proof": "DIRECT_GET_TERMINAL_AND_REPEATED_STAMP_INVENTORY_NO_ACTIVE",
    },
    "internet_gateway": {
        "prior_internet_gateway_proof": "DIRECT_GET_TERMINAL_AND_REPEATED_STAMP_INVENTORY_NO_ACTIVE",
    },
    "route_table": {
        "prior_route_table_proof": "DIRECT_GET_TERMINAL_AND_REPEATED_STAMP_INVENTORY_NO_ACTIVE",
    },
    "security_list": {
        "prior_security_list_proof": "DIRECT_GET_TERMINAL_AND_REPEATED_STAMP_INVENTORY_NO_ACTIVE",
    },
    "subnet": {
        "prior_subnet_proof": "DIRECT_GET_TERMINAL_AND_REPEATED_STAMP_INVENTORY_NO_ACTIVE",
    },
}


def cleanup_typed_proof_verdicts(state: dict[str, object]) -> dict[str, str]:
    """Return literal per-type verdicts only for a complete cleanup proof."""
    if state.get("prior_cleanup_status") != "RECONCILED_PROVEN_ABSENT":
        raise ValueError("aggregate cleanup proof is not reconciled")
    verdicts: dict[str, str] = {}
    for resource, requirements in CLEANUP_TYPED_PROOF_REQUIREMENTS.items():
        for key, expected in requirements.items():
            if state.get(key) != expected:
                raise ValueError(f"missing typed cleanup proof for {resource}: {key}")
        verdicts[resource] = "RECONCILED_PROVEN_ABSENT"
    create_status = state.get("prior_instance_create_status")
    uncertain_proof = state.get("prior_uncertain_instance_proof")
    if create_status == "REQUEST_UNCERTAIN":
        if uncertain_proof != "REPEATED_EXACT_STAMP_INVENTORY_NO_ACTIVE":
            raise ValueError("missing typed cleanup proof for uncertain_instance")
        verdicts["uncertain_instance"] = "RECONCILED_PROVEN_ABSENT"
    elif create_status == "CAPTURED":
        if uncertain_proof != "NOT_APPLICABLE":
            raise ValueError("invalid uncertainty proof for captured instance")
        verdicts["uncertain_instance"] = "NOT_APPLICABLE"
    else:
        raise ValueError("missing authoritative instance create status")
    return verdicts


def _field(body: str, label: str) -> str:
    matches = re.findall(rf"^- {re.escape(label)}: `([^`]+)`\s*$", body, re.MULTILINE)
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {label!r} field")
    return matches[0]


def _field_with_rc(body: str, label: str) -> tuple[str, int]:
    matches = re.findall(
        rf"^- {re.escape(label)}: `([^`]+)`; rc: `([0-9]+)`\s*$",
        body,
        re.MULTILINE,
    )
    if len(matches) != 1:
        raise ValueError(f"expected exactly one complete {label!r} field with rc")
    value, rc = matches[0]
    return value, int(rc)


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

    create_status, create_rc = _field_with_rc(body, "isolated instance create request")
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
        "instance_create_rc": create_rc,
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
