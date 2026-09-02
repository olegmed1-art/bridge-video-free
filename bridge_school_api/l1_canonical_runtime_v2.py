from __future__ import annotations

from typing import Any, Iterable

from .l1_canonical_registry import (
    ACTIVE_DOMAIN_RULE_IDS,
    GOVERNANCE_RULE_IDS,
    KNOWN_RULE_IDS,
    SYSTEM_VERSION,
    classify_rule,
)
from .l1_canonical_runtime import RuleEvaluation, evaluate as _evaluate_v1, resolve

ENGINE_VERSION = "l1-canonical-runtime-v2"


def _result(
    rule_id: str,
    status: str,
    action: Any = None,
    *,
    reason: str | None = None,
) -> RuleEvaluation:
    return RuleEvaluation(
        rule_id=rule_id,
        status=status,
        action=action,
        priority=0,
        specificity=0,
        scope_rank=0,
        evidence=(),
        reason=reason,
    )


def evaluate(
    rule_id: str,
    context: dict[str, Any],
    *,
    system_version: str = SYSTEM_VERSION,
) -> RuleEvaluation:
    """Evaluate only registered SCHOOL_L1_DB_V1 rules and fail closed otherwise.

    Runtime v1 contains the bounded semantic evaluators proven by the current
    regression suite. Runtime v2 adds the complete live L1 rule registry around
    that evaluator. A rule may therefore be:

    * executable now -> delegated to v1;
    * known canonical but not yet encoded as a deterministic evaluator -> BLOCK;
    * unknown / tournament / typo -> BLOCK as UNKNOWN_RULE_ID.

    No natural-language rule text is interpreted at runtime, so missing semantics
    are never guessed.
    """
    if system_version != SYSTEM_VERSION:
        return _result(
            rule_id,
            "NO_MATCH",
            reason=f"system isolation: {system_version}",
        )

    category = classify_rule(rule_id)
    if category == "UNKNOWN":
        return _result(
            rule_id,
            "BLOCK",
            "UNKNOWN_RULE_ID",
            reason="rule is not present in the canonical SCHOOL_L1_DB_V1 registry",
        )

    result = _evaluate_v1(rule_id, context, system_version=system_version)
    if (
        result.status == "BLOCK"
        and result.action == "REFERENCE_ONLY_OR_UNIMPLEMENTED"
    ):
        return _result(
            rule_id,
            "BLOCK",
            "KNOWN_RULE_NOT_EXECUTABLE",
            reason=(
                "active canonical rule is registry-tracked but has no bounded "
                "semantic evaluator yet; runtime fails closed"
            ),
        )
    return result


def runtime_status(rule_id: str) -> dict[str, Any]:
    category = classify_rule(rule_id)
    return {
        "rule_id": rule_id,
        "system_version": SYSTEM_VERSION,
        "engine_version": ENGINE_VERSION,
        "category": category,
        "known": rule_id in KNOWN_RULE_IDS,
        "domain_rule_count": len(ACTIVE_DOMAIN_RULE_IDS),
        "governance_rule_count": len(GOVERNANCE_RULE_IDS),
    }


def resolve_registered(
    evaluations: Iterable[RuleEvaluation],
) -> RuleEvaluation:
    """Use the existing deterministic specificity/scope/priority resolver."""
    return resolve(evaluations)
