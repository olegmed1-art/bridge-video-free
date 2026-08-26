#!/usr/bin/env python3
"""Fail-closed validation for the School Governance System registries.

Uses only the Python standard library so it can run in CI and on recovery hosts.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = ROOT / "ops/governance/governance-state.json"
PORTFOLIO_PATH = ROOT / "ops/governance/portfolio.json"
ASSURED_SCHEMA_PATH = ROOT / "ops/governance/assured-task.schema.json"


class ValidationError(RuntimeError):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"missing required file: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"invalid JSON in {path.relative_to(ROOT)}: line {exc.lineno}, column {exc.colno}"
        ) from exc


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def parse_iso_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field} must be an ISO date, got {value!r}") from exc


def validate_state(state: dict[str, Any]) -> None:
    require(state.get("schema_version") == "1.0", "unsupported governance-state schema_version")

    system = state.get("governance_system")
    require(isinstance(system, dict), "governance_system must be an object")
    require(system.get("status") == "ACTIVE", "governance system must be ACTIVE")
    require(system.get("authority") == "CANONICAL", "governance authority must be CANONICAL")
    require(system.get("version") == "1.0", "active governance version must be 1.0")
    require(system.get("director_accountable_owner") is True, "director accountability must be explicit")
    require(system.get("ai_delegated_operator") is True, "AI delegated operation must be explicit")

    effective = parse_iso_date(system.get("effective_at"), "governance_system.effective_at")
    canonical_rel = system.get("canonical_document")
    require(isinstance(canonical_rel, str) and canonical_rel, "canonical_document is required")
    canonical_path = ROOT / canonical_rel
    require(canonical_path.is_file(), f"canonical governance document does not exist: {canonical_rel}")
    canonical_text = canonical_path.read_text(encoding="utf-8")
    require("ACTIVE / CANONICAL" in canonical_text, "canonical document must declare ACTIVE / CANONICAL")
    require("Версия: **1.0**" in canonical_text, "canonical document version marker is missing")

    reviews = state.get("reviews")
    require(isinstance(reviews, dict), "reviews must be an object")
    first_review = parse_iso_date(reviews.get("first_operational_audit_due"), "first_operational_audit_due")
    full_review = parse_iso_date(reviews.get("full_review_due"), "full_review_due")
    require(effective < first_review < full_review, "review dates must follow effective date in order")
    require(reviews.get("recurring_review_months", 0) >= 1, "recurring review cadence must be positive")

    expected_enums = {
        "governance_modes": {"LIGHTWEIGHT", "STANDARD", "ASSURED", "INCIDENT"},
        "work_classes": {"INCIDENT", "MANDATORY", "STRATEGIC", "OPERATIONAL", "IMPROVEMENT", "RESEARCH"},
        "urgencies": {"EXPEDITE", "HIGH", "NORMAL", "LOW"},
        "strategic_ranks": {"S1", "S2", "S3", "NONE"},
        "independence_levels": {"I0", "I1", "I2", "I3", "I4"},
        "evidence_levels": {"E0", "E1", "E2", "E3", "E4", "E5"},
        "data_classes": {"PUBLIC", "INTERNAL", "CONFIDENTIAL", "STUDENT_SENSITIVE", "CREDENTIAL_SECRET"},
    }
    for field, expected in expected_enums.items():
        actual = state.get(field)
        require(isinstance(actual, list), f"{field} must be a list")
        require(set(actual) == expected, f"{field} differs from canonical enum: {actual!r}")


REQUIRED_VALUE_FIELDS = {
    "portfolio_id",
    "title",
    "work_class",
    "urgency",
    "strategic_rank",
    "governance_mode",
    "status",
    "problem_statement",
    "benefit_hypothesis",
    "target_users",
    "expected_capability",
    "success_metrics",
    "kill_criteria",
    "tracker",
}

REQUIRED_SERVICE_FIELDS = {
    "service_id",
    "title",
    "status",
    "service_owner",
    "users",
    "service_level",
    "health_metrics",
    "dependencies",
    "data_classes",
    "recovery_requirement",
    "improvement_backlog",
}


def validate_portfolio(portfolio: dict[str, Any], state: dict[str, Any]) -> None:
    require(portfolio.get("schema_version") == "1.0", "unsupported portfolio schema_version")
    require(
        portfolio.get("registry_status") in {"ACTIVE", "ACTIVE_PARTIAL_INVENTORY"},
        "portfolio registry must be active",
    )
    parse_iso_date(portfolio.get("last_verified_at"), "portfolio.last_verified_at")

    value_items = portfolio.get("value_portfolio")
    service_items = portfolio.get("service_portfolio")
    require(isinstance(value_items, list), "value_portfolio must be a list")
    require(isinstance(service_items, list), "service_portfolio must be a list")

    valid_classes = set(state["work_classes"])
    valid_urgencies = set(state["urgencies"])
    valid_ranks = set(state["strategic_ranks"])
    valid_modes = set(state["governance_modes"])
    valid_data_classes = set(state["data_classes"])

    value_ids: set[str] = set()
    for item in value_items:
        require(isinstance(item, dict), "each value portfolio item must be an object")
        missing = REQUIRED_VALUE_FIELDS - item.keys()
        require(not missing, f"value portfolio item is missing fields: {sorted(missing)}")
        item_id = item["portfolio_id"]
        require(isinstance(item_id, str) and item_id, "portfolio_id must be non-empty")
        require(item_id not in value_ids, f"duplicate portfolio_id: {item_id}")
        value_ids.add(item_id)
        require(item["work_class"] in valid_classes, f"invalid work_class for {item_id}")
        require(item["urgency"] in valid_urgencies, f"invalid urgency for {item_id}")
        require(item["strategic_rank"] in valid_ranks, f"invalid strategic_rank for {item_id}")
        require(item["governance_mode"] in valid_modes, f"invalid governance_mode for {item_id}")
        require(item["success_metrics"], f"success_metrics cannot be empty for {item_id}")
        require(item["kill_criteria"], f"kill_criteria cannot be empty for {item_id}")

    service_ids: set[str] = set()
    for item in service_items:
        require(isinstance(item, dict), "each service portfolio item must be an object")
        missing = REQUIRED_SERVICE_FIELDS - item.keys()
        require(not missing, f"service portfolio item is missing fields: {sorted(missing)}")
        item_id = item["service_id"]
        require(isinstance(item_id, str) and item_id, "service_id must be non-empty")
        require(item_id not in service_ids, f"duplicate service_id: {item_id}")
        service_ids.add(item_id)
        unknown_classes = set(item["data_classes"]) - valid_data_classes
        require(not unknown_classes, f"unknown data classes for {item_id}: {sorted(unknown_classes)}")
        require(item["health_metrics"], f"health_metrics cannot be empty for {item_id}")
        require(item["recovery_requirement"], f"recovery requirement is required for {item_id}")


def validate_assured_schema(schema: dict[str, Any]) -> None:
    require(schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema", "unexpected JSON Schema version")
    require(schema.get("type") == "object", "assured task schema root must be an object")
    required = set(schema.get("required", []))
    for field in {"task_id", "governance_mode", "roles", "evidence_contract", "stop_conditions", "status"}:
        require(field in required, f"assured task schema must require {field}")
    mode = schema.get("properties", {}).get("governance_mode", {})
    require(mode.get("const") == "ASSURED", "assured task schema must pin ASSURED mode")


def main() -> int:
    try:
        state = load_json(STATE_PATH)
        portfolio = load_json(PORTFOLIO_PATH)
        assured_schema = load_json(ASSURED_SCHEMA_PATH)
        require(isinstance(state, dict), "governance-state root must be an object")
        require(isinstance(portfolio, dict), "portfolio root must be an object")
        require(isinstance(assured_schema, dict), "assured task schema root must be an object")
        validate_state(state)
        validate_portfolio(portfolio, state)
        validate_assured_schema(assured_schema)
    except ValidationError as exc:
        print(f"GOVERNANCE_VALIDATION=FAIL: {exc}", file=sys.stderr)
        return 1

    print("GOVERNANCE_VALIDATION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
