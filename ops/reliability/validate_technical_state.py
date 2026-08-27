#!/usr/bin/env python3
"""Fail-closed structural validation for the technical reliability registry.

This validates policy invariants only. It never upgrades runtime evidence by inference.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = ROOT / "ops" / "reliability" / "technical-state.yml"

REQUIRED_COMPONENTS = {
    "github",
    "neon",
    "oracle_frankfurt",
    "vercel",
    "google_drive",
    "dds3",
    "ben",
    "video_worker",
}
REQUIRED_INVARIANTS = {
    "no_unique_durable_evidence_only_on_oracle",
    "no_backup_marked_proven_without_restore_test",
    "no_dds3_marked_proven_without_real_no_fallback_request",
    "actual_state_overrides_documentation",
    "owner_only_actions_are_minimized",
}
RPO_RTO_COMPONENTS = ("neon", "oracle_frankfurt")
OWNER_ACTION_STATUSES = {"owner_action_required"}


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    state = yaml.safe_load(STATE_PATH.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        fail("registry root must be a mapping")
    if state.get("schema_version") != 1:
        fail("schema_version must be 1")
    if state.get("policy") != "docs/TECHNICAL_GOVERNANCE.md":
        fail("canonical policy path is missing or changed")

    components = state.get("components")
    if not isinstance(components, dict):
        fail("components must be a mapping")
    missing = sorted(REQUIRED_COMPONENTS - set(components))
    if missing:
        fail(f"missing required components: {', '.join(missing)}")

    invariants = set(state.get("safety_invariants") or [])
    missing_invariants = sorted(REQUIRED_INVARIANTS - invariants)
    if missing_invariants:
        fail(f"missing safety invariants: {', '.join(missing_invariants)}")

    for component_name in RPO_RTO_COMPONENTS:
        component = components[component_name]
        for key in ("rpo_target_hours", "rto_target_hours"):
            value = component.get(key)
            if not isinstance(value, int) or value <= 0:
                fail(f"{component_name}.{key} must be a positive integer")

    neon = components["neon"]
    if neon.get("backup_required") is not True:
        fail("Neon backup must remain required")
    if neon.get("independent_backup") != "required":
        fail("Neon independent backup must remain required")
    if neon.get("backup_status") == "proven" and neon.get("restore_status") != "proven":
        fail("Neon backup cannot be PROVEN before restore is PROVEN")

    oracle = components["oracle_frankfurt"]
    if oracle.get("unique_durable_data_allowed") is not False:
        fail("Oracle must not be the sole durable store")

    dds3 = components["dds3"]
    req = dds3.get("acceptance_requirements") or {}
    if req.get("engine") != "DDS3" or req.get("fallback_used") is not False:
        fail("DDS3 acceptance must require real DDS3 with fallback_used=false")
    if req.get("expected_result_required") is not True or req.get("evidence_retained") is not True:
        fail("DDS3 acceptance must require expected result and retained evidence")

    recovery = state.get("whole_school_recovery") or {}
    if recovery.get("target_status") != "RECOVERY_PROVEN_V1":
        fail("whole-school target status must remain RECOVERY_PROVEN_V1")
    blockers = recovery.get("blockers")
    if recovery.get("current_status") != "RECOVERY_PROVEN_V1" and not blockers:
        fail("non-proven recovery state must list blockers")
    if recovery.get("current_status") == "RECOVERY_PROVEN_V1" and blockers:
        fail("proven recovery state cannot list blockers")

    queued = ((state.get("work_queue") or {}).get("queued") or [])
    owner_action_items = [
        item.get("id")
        for item in queued
        if isinstance(item, dict) and item.get("status") in OWNER_ACTION_STATUSES
    ]
    if recovery.get("current_status") == "RECOVERY_PROVEN_V1" and owner_action_items:
        fail("proven recovery state cannot contain owner-action-required work")

    updated_raw = state.get("updated_at_utc")
    try:
        updated = datetime.fromisoformat(str(updated_raw).replace("Z", "+00:00"))
    except ValueError as exc:
        fail(f"invalid updated_at_utc: {exc}")
    age = datetime.now(timezone.utc) - updated.astimezone(timezone.utc)
    print(f"registry_age_days={age.total_seconds() / 86400:.2f}")
    if age.total_seconds() > 35 * 86400:
        print("WARNING: technical-state registry is older than 35 days", file=sys.stderr)

    print("technical-state registry: PASS")
    if blockers:
        print("open recovery blockers:")
        for blocker in blockers:
            print(f"- {blocker}")


if __name__ == "__main__":
    main()
