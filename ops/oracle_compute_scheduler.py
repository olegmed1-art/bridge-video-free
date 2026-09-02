#!/usr/bin/env python3
"""Advisory, fail-closed scheduler for useful Oracle compute.

This module deliberately does not start/stop services. It validates bounded work
against the repository policy and emits an ordered admission plan. A separate,
reviewed operator may consume the plan later.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

CLASS_RANK = {"P0": 0, "P1": 1, "P2": 2}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_job(job: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = policy["admission"]["required_fields"]
    for field in required:
        if field not in job:
            errors.append(f"missing:{field}")
    if errors:
        return errors
    if job["class"] not in CLASS_RANK:
        errors.append("invalid:class")
    runtime = job["bounded_runtime_seconds"]
    if not isinstance(runtime, int) or runtime <= 0:
        errors.append("invalid:bounded_runtime_seconds")
    if job["compute_value"] not in policy["admission"]["compute_value_allowed"]:
        errors.append("invalid:compute_value")
    if not str(job["evidence_output"]).strip():
        errors.append("invalid:evidence_output")
    scope = str(job.get("scope", "")).lower()
    if ("sealed" in scope or "holdout" in scope) and not job.get("explicit_gate", False):
        errors.append("blocked:sealed_or_holdout_without_explicit_gate")
    if job.get("requires_production_restart", False):
        errors.append("blocked:production_restart_required")
    if job.get("paid_external_dependency", False) and not job.get("budget_gate", False):
        errors.append("blocked:paid_external_dependency_without_budget_gate")
    return errors


def plan(jobs: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    admitted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for job in jobs:
        errors = validate_job(job, policy)
        if errors:
            rejected.append({"job_id": job.get("job_id"), "errors": errors})
        else:
            admitted.append(job)
    admitted.sort(key=lambda j: (CLASS_RANK[j["class"]], int(j.get("priority", 100)), j["job_id"]))
    return {
        "schema": "bridge-school-oracle-compute-plan-v1",
        "mode": "advisory_no_mutation",
        "admitted": admitted,
        "rejected": rejected,
        "next_job": admitted[0]["job_id"] if admitted else None,
        "idle_allowed": not admitted,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", default="ops/oracle_compute_policy.json")
    parser.add_argument("--jobs", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    policy = load_json(Path(args.policy))
    jobs_payload = load_json(Path(args.jobs))
    jobs = jobs_payload["jobs"] if isinstance(jobs_payload, dict) else jobs_payload
    result = plan(jobs, policy)
    rendered = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
