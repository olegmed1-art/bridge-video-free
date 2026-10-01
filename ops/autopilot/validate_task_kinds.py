#!/usr/bin/env python3
"""Fail-closed validator for School Autopilot task-kind contracts.

The validator intentionally uses only the Python standard library so the design
contract can be checked in CI without installing runtime dependencies.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ALLOWED_RISK_CLASSES = {"LOW", "MEDIUM", "HIGH", "OWNER_ONLY"}
ALLOWED_GOVERNANCE_MODES = {"LIGHTWEIGHT", "STANDARD", "ASSURED", "INCIDENT"}
ALLOWED_EXECUTORS = {
    "vercel",
    "github",
    "oracle_via_github",
    "drive",
    "openai",
    "owner",
}
ALLOWED_STATES = {
    "QUEUED",
    "RUNNING",
    "WAITING_EXTERNAL",
    "WAITING_APPROVAL",
    "OWNER_REQUIRED",
    "SUCCEEDED",
    "FAILED_CLOSED",
    "CANCELLED",
    "BUDGET_STOP",
    "DEADLINE_STOP",
}
ALLOWED_BACKOFF = {"none", "fixed", "exponential"}
KIND_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
TOP_LEVEL_KEYS = {
    "schema_version",
    "kind",
    "version",
    "description",
    "risk_class",
    "governance_mode",
    "allowed_executors",
    "allowed_transitions",
    "approval_policy",
    "retry_policy",
    "budget_policy",
    "acceptance_criteria",
    "rollback",
}
REQUIRED_TOP_LEVEL_KEYS = TOP_LEVEL_KEYS - {"description"}


class ContractError(ValueError):
    """Raised when a task-kind contract is unsafe or malformed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def require_exact_keys(value: dict[str, Any], allowed: set[str], context: str) -> None:
    extras = sorted(set(value) - allowed)
    require(not extras, f"{context}: unknown keys: {', '.join(extras)}")


def require_string_list(value: Any, context: str, *, nonempty: bool = False) -> list[str]:
    require(isinstance(value, list), f"{context}: expected array")
    require(all(isinstance(item, str) and item for item in value), f"{context}: expected non-empty strings")
    if nonempty:
        require(bool(value), f"{context}: must not be empty")
    return value


def validate_transition(value: Any, index: int) -> tuple[str, str, str]:
    context = f"allowed_transitions[{index}]"
    require(isinstance(value, dict), f"{context}: expected object")
    allowed = {"from", "to", "event", "owner_approval_required"}
    require_exact_keys(value, allowed, context)
    require({"from", "to", "event"}.issubset(value), f"{context}: missing required keys")

    source = value["from"]
    target = value["to"]
    event = value["event"]
    require(source in ALLOWED_STATES, f"{context}.from: unsupported state {source!r}")
    require(target in ALLOWED_STATES, f"{context}.to: unsupported state {target!r}")
    require(isinstance(event, str) and KIND_RE.fullmatch(event), f"{context}.event: invalid event code")

    approval_required = value.get("owner_approval_required", False)
    require(isinstance(approval_required, bool), f"{context}.owner_approval_required: expected boolean")
    if target == "OWNER_REQUIRED":
        require(approval_required is True, f"{context}: OWNER_REQUIRED transition must declare owner approval")
    if approval_required:
        require(target == "OWNER_REQUIRED", f"{context}: owner approval flag is only valid for OWNER_REQUIRED")

    return source, target, event


def validate_contract(data: Any, *, path: Path) -> None:
    context = str(path)
    require(isinstance(data, dict), f"{context}: top level must be object")
    require_exact_keys(data, TOP_LEVEL_KEYS, context)
    missing = sorted(REQUIRED_TOP_LEVEL_KEYS - set(data))
    require(not missing, f"{context}: missing keys: {', '.join(missing)}")

    require(data["schema_version"] == 1, f"{context}: schema_version must be 1")
    kind = data["kind"]
    require(isinstance(kind, str) and KIND_RE.fullmatch(kind), f"{context}: invalid kind")
    require(path.stem == kind, f"{context}: filename must match kind {kind}.json")

    version = data["version"]
    require(isinstance(version, int) and not isinstance(version, bool) and version >= 1, f"{context}: invalid version")
    if "description" in data:
        require(isinstance(data["description"], str) and len(data["description"]) <= 1000, f"{context}: invalid description")

    require(data["risk_class"] in ALLOWED_RISK_CLASSES, f"{context}: unsupported risk_class")
    require(data["governance_mode"] in ALLOWED_GOVERNANCE_MODES, f"{context}: unsupported governance_mode")

    executors = require_string_list(data["allowed_executors"], f"{context}.allowed_executors", nonempty=True)
    require(len(executors) == len(set(executors)), f"{context}.allowed_executors: duplicates are forbidden")
    unknown_executors = sorted(set(executors) - ALLOWED_EXECUTORS)
    require(not unknown_executors, f"{context}.allowed_executors: unsupported executors {unknown_executors}")
    if data["risk_class"] == "OWNER_ONLY":
        require("owner" in executors, f"{context}: OWNER_ONLY task must include owner executor")

    transitions = data["allowed_transitions"]
    require(isinstance(transitions, list) and transitions, f"{context}.allowed_transitions: expected non-empty array")
    transition_keys = [validate_transition(item, index) for index, item in enumerate(transitions)]
    require(len(transition_keys) == len(set(transition_keys)), f"{context}.allowed_transitions: duplicate transition")
    require(any(source == "QUEUED" for source, _, _ in transition_keys), f"{context}: no transition starts from QUEUED")
    require(
        any(target in {"SUCCEEDED", "FAILED_CLOSED", "OWNER_REQUIRED", "BUDGET_STOP", "DEADLINE_STOP"} for _, target, _ in transition_keys),
        f"{context}: no terminal or protected transition",
    )

    approval = data["approval_policy"]
    require(isinstance(approval, dict), f"{context}.approval_policy: expected object")
    require_exact_keys(approval, {"required_for", "approval_ttl_seconds", "scope_digest_required"}, f"{context}.approval_policy")
    require({"required_for", "scope_digest_required"}.issubset(approval), f"{context}.approval_policy: missing required keys")
    required_for = require_string_list(approval["required_for"], f"{context}.approval_policy.required_for")
    require(len(required_for) == len(set(required_for)), f"{context}.approval_policy.required_for: duplicates are forbidden")
    require(approval["scope_digest_required"] is True, f"{context}: scope_digest_required must be true")
    if "approval_ttl_seconds" in approval:
        ttl = approval["approval_ttl_seconds"]
        require(isinstance(ttl, int) and not isinstance(ttl, bool) and ttl >= 60, f"{context}: invalid approval TTL")

    owner_events = {event for _, target, event in transition_keys if target == "OWNER_REQUIRED"}
    require(owner_events.issubset(set(required_for)), f"{context}: OWNER_REQUIRED events must be listed in approval_policy.required_for")

    retry = data["retry_policy"]
    require(isinstance(retry, dict), f"{context}.retry_policy: expected object")
    require_exact_keys(retry, {"max_external_attempts", "max_model_turns", "backoff"}, f"{context}.retry_policy")
    require({"max_external_attempts", "max_model_turns"}.issubset(retry), f"{context}.retry_policy: missing required keys")
    external_attempts = retry["max_external_attempts"]
    model_turns = retry["max_model_turns"]
    require(isinstance(external_attempts, int) and not isinstance(external_attempts, bool) and 0 <= external_attempts <= 20, f"{context}: invalid max_external_attempts")
    require(isinstance(model_turns, int) and not isinstance(model_turns, bool) and 0 <= model_turns <= 10, f"{context}: invalid max_model_turns")
    if "backoff" in retry:
        require(retry["backoff"] in ALLOWED_BACKOFF, f"{context}: unsupported backoff")
    if "openai" not in executors:
        require(model_turns == 0, f"{context}: max_model_turns must be zero without openai executor")

    budget = data["budget_policy"]
    require(isinstance(budget, dict), f"{context}.budget_policy: expected object")
    require_exact_keys(budget, {"per_task_limit_usd", "critical_model_requires_risk_gate"}, f"{context}.budget_policy")
    require({"per_task_limit_usd", "critical_model_requires_risk_gate"}.issubset(budget), f"{context}.budget_policy: missing required keys")
    limit = budget["per_task_limit_usd"]
    require(isinstance(limit, (int, float)) and not isinstance(limit, bool) and 0 <= float(limit) <= 1000, f"{context}: invalid per-task budget")
    require(budget["critical_model_requires_risk_gate"] is True, f"{context}: critical model risk gate must be true")

    criteria = require_string_list(data["acceptance_criteria"], f"{context}.acceptance_criteria", nonempty=True)
    require(len(criteria) == len(set(criteria)), f"{context}.acceptance_criteria: duplicates are forbidden")

    rollback = data["rollback"]
    require(isinstance(rollback, dict), f"{context}.rollback: expected object")
    require_exact_keys(rollback, {"kill_switch_safe", "instructions"}, f"{context}.rollback")
    require({"kill_switch_safe", "instructions"}.issubset(rollback), f"{context}.rollback: missing required keys")
    require(rollback["kill_switch_safe"] is True, f"{context}: kill_switch_safe must be true")
    require_string_list(rollback["instructions"], f"{context}.rollback.instructions", nonempty=True)

    if kind == "AUTOPILOT_SMOKE_V1":
        require("openai" not in executors, f"{context}: smoke path must not call OpenAI")
        require(model_turns == 0, f"{context}: smoke path must have zero model turns")
        require(data["risk_class"] == "LOW", f"{context}: smoke path must remain LOW risk")

    if kind == "RECOVERY_SHADOW_V1":
        forbidden = {"oracle_via_github", "drive", "owner"} & set(executors)
        require(not forbidden, f"{context}: recovery shadow has write-capable executors {sorted(forbidden)}")
        require(data["risk_class"] == "LOW", f"{context}: recovery shadow must remain LOW risk")


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"{path}: cannot read valid JSON: {exc}") from exc


def validate_directory(directory: Path) -> list[Path]:
    require(directory.is_dir(), f"{directory}: task-kind directory does not exist")
    paths = sorted(directory.glob("*.json"))
    require(paths, f"{directory}: no task-kind contracts found")

    kinds: set[str] = set()
    for path in paths:
        data = load_json(path)
        validate_contract(data, path=path)
        kind = data["kind"]
        require(kind not in kinds, f"{directory}: duplicate task kind {kind}")
        kinds.add(kind)
    return paths


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directory",
        type=Path,
        default=Path(__file__).resolve().parent / "task-kinds",
        help="Directory containing task-kind JSON contracts",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        paths = validate_directory(args.directory)
    except ContractError as exc:
        print(f"AUTOPILOT_TASK_KIND_CONTRACT: FAIL: {exc}", file=sys.stderr)
        return 1

    print(f"AUTOPILOT_TASK_KIND_CONTRACT: PASS contracts={len(paths)}")
    for path in paths:
        print(f"- {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
