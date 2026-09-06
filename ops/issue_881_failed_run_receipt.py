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
    """Return publishable, fail-closed per-type cleanup verdicts.

    This helper deliberately never raises for missing reconciliation evidence: the
    always-run receipt must survive an earlier fetch, inventory, or direct-GET
    failure.  Only complete evidence receives the GO-granting literal.
    """
    reconciled = state.get("prior_cleanup_status") == "RECONCILED_PROVEN_ABSENT"
    typed_complete = all(
        all(
            state.get(key) == expected for key, expected in requirements.items()
        )
        for requirements in CLEANUP_TYPED_PROOF_REQUIREMENTS.values()
    )
    create_status = state.get("prior_instance_create_status")
    uncertain_proof = state.get("prior_uncertain_instance_proof")
    uncertain_complete = (
        create_status == "REQUEST_UNCERTAIN"
        and uncertain_proof == "REPEATED_EXACT_STAMP_INVENTORY_NO_ACTIVE"
    ) or (
        create_status in {"CAPTURED", "NOT_RECORDED"}
        and uncertain_proof == "NOT_APPLICABLE"
    )
    all_complete = reconciled and typed_complete and uncertain_complete
    typed_verdict = (
        "RECONCILED_PROVEN_ABSENT" if all_complete else "RECONCILIATION_INCOMPLETE"
    )
    verdicts = {
        resource: typed_verdict for resource in CLEANUP_TYPED_PROOF_REQUIREMENTS
    }
    if all_complete and create_status == "REQUEST_UNCERTAIN":
        verdicts["uncertain_instance"] = "RECONCILED_PROVEN_ABSENT"
    elif create_status in {"CAPTURED", "NOT_RECORDED"} and uncertain_proof == "NOT_APPLICABLE":
        verdicts["uncertain_instance"] = "NOT_APPLICABLE"
    else:
        verdicts["uncertain_instance"] = "RECONCILIATION_INCOMPLETE"
    return verdicts


def _field(body: str, label: str) -> str:
    matches = re.findall(rf"^- {re.escape(label)}: `([^`]+)`\s*$", body, re.MULTILINE)
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {label!r} field")
    return matches[0]


def _field_with_rc_matches(body: str, label: str) -> list[tuple[str, int | None]]:
    matches = re.findall(
        rf"^- {re.escape(label)}: `([^`]+)`; rc: `([0-9]+|none)`\s*$",
        body,
        re.MULTILINE,
    )
    return [(value, None if rc == "none" else int(rc)) for value, rc in matches]


def _one_instance_create_field(body: str) -> tuple[str, int | None]:
    matches: list[tuple[str, int | None]] = []
    for label in (
        "isolated instance create request",
        "isolated paid instance create request",
    ):
        matches.extend(_field_with_rc_matches(body, label))
    if len(matches) != 1:
        raise ValueError("expected exactly one complete isolated instance create field with rc")
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

    create_status, create_rc = _one_instance_create_field(body)
    resources: dict[str, list[str]] = {}
    redacted_resources: set[str] = set()
    for kind, label in RESOURCE_FIELDS.items():
        raw = _field(body, label)
        if raw == "none":
            resources[kind] = []
            continue
        if raw == "present_redacted":
            resources[kind] = []
            redacted_resources.add(kind)
            continue
        match = OCID_RE.fullmatch(raw)
        if match is None or match.group(1) != OCID_TYPES[kind]:
            raise ValueError(f"invalid {kind} OCID")
        resources[kind] = [raw]

    failure_phase = _field(body, "failing phase before cleanup") if redacted_resources or create_status == "NOT_RECORDED" else None
    cleanup_status = _field(body, "current-run temporary cleanup") if redacted_resources else None
    no_launch_preflight = (
        create_status == "NOT_RECORDED"
        and create_rc is None
        and failure_phase == "paid_capacity_preflight"
    )
    if not resources["instance"] and create_status != "REQUEST_UNCERTAIN" and not no_launch_preflight:
        raise ValueError("missing instance ID is allowed only for an uncertain launch or a rejected paid preflight")
    required = set(RESOURCE_FIELDS) - {"instance"}
    if redacted_resources and (
        redacted_resources != required
        or cleanup_status != "INCOMPLETE"
        or not no_launch_preflight
    ):
        raise ValueError("redacted resources require the exact rejected-preflight cleanup receipt")
    missing = sorted(
        kind for kind in required
        if len(resources[kind]) != 1 and kind not in redacted_resources
    )
    if missing:
        raise ValueError("failed receipt is missing authoritative resources: " + ",".join(missing))

    return {
        "run_id": run_id,
        "stamp": stamp,
        "instance_create_status": create_status,
        "instance_create_rc": create_rc,
        "resources": resources,
        "redacted_resources": sorted(redacted_resources),
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
