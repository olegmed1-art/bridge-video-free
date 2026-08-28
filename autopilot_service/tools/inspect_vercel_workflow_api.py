#!/usr/bin/env python3
"""Inspect only documented/public Vercel Workflows symbols.

The compatibility spike uses this report to avoid guessing a changing beta API.
No workflow is started and no network or credential is used.
"""

from __future__ import annotations

import inspect
import json
from importlib.metadata import version
from typing import Any

from vercel import workflow

from autopilot_app.workflow import wf
from autopilot_app.workflows import ShadowSignal, shadow_wait_workflow


PINNED_VERSION = "0.10.0"


def safe_signature(value: Any) -> str | None:
    try:
        return str(inspect.signature(value))
    except (TypeError, ValueError):
        return None


def public_members(value: Any) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for name in sorted(name for name in dir(value) if not name.startswith("_")):
        member = getattr(value, name)
        result[name] = {
            "callable": callable(member),
            "signature": safe_signature(member),
            "type": type(member).__name__,
        }
    return result


def main() -> None:
    installed = version("vercel")
    if installed != PINNED_VERSION:
        raise SystemExit(
            f"VERCEL_VERSION_MISMATCH expected={PINNED_VERSION} observed={installed}"
        )

    required = {
        "module.Workflows": hasattr(workflow, "Workflows"),
        "module.BaseHook": hasattr(workflow, "BaseHook"),
        "module.Run": hasattr(workflow, "Run"),
        "module.sleep": callable(getattr(workflow, "sleep", None)),
        "module.start": callable(getattr(workflow, "start", None)),
        "registry.workflow": callable(getattr(wf, "workflow", None)),
        "registry.step": callable(getattr(wf, "step", None)),
        "hook.wait": callable(getattr(ShadowSignal, "wait", None)),
        "hook.resume": callable(getattr(ShadowSignal, "resume", None)),
    }
    missing = [name for name, present in required.items() if not present]
    if missing:
        raise SystemExit("VERCEL_DOCUMENTED_API_MISSING " + ",".join(missing))

    startup_candidates: list[dict[str, object]] = []
    for owner_name, owner in (
        ("vercel.workflow", workflow),
        ("Workflows registry", wf),
        ("decorated workflow", shadow_wait_workflow),
    ):
        for name in ("start", "run", "create_run", "start_workflow"):
            candidate = getattr(owner, name, None)
            if callable(candidate):
                startup_candidates.append(
                    {
                        "owner": owner_name,
                        "name": name,
                        "signature": safe_signature(candidate),
                    }
                )

    report = {
        "vercel_version": installed,
        "required_documented_api": required,
        "startup_candidates": startup_candidates,
        "module_public": public_members(workflow),
        "registry_public": public_members(wf),
        "run_class_public": public_members(workflow.Run),
        "run_class_signature": safe_signature(workflow.Run),
        "decorated_workflow_public": public_members(shadow_wait_workflow),
        "hook_public": public_members(ShadowSignal),
        "shadow_workflow_callable": callable(shadow_wait_workflow),
        "shadow_workflow_signature": safe_signature(shadow_wait_workflow),
    }
    print("VERCEL_WORKFLOW_API=" + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
