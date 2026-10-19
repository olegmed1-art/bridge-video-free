"""Common teacher answer envelope for one explicitly test-only vertical.

Native verification NOT_RUN. Production eligibility, general planning, deal
memory, UI, source extraction and BML semantics are outside this module.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import teacher_entry_blocking_reader_path as entry_path

SCHEMA = "teacher-answer-v04-test-v1"
VERSION = "teacher-v04-integration-source-v1"
SCOPE = "ENTRY_ORDER_TEST_ONLY"


def _withheld(status: str, reason: str, missing_context=None) -> dict[str, Any]:
    return {
        "schema": SCHEMA, "controller_version": VERSION, "scope": SCOPE,
        "test_only": True, "production_ready": False,
        "status": status, "reason": reason,
        "missing_context": list(missing_context or []),
        "answer": None, "decision": None, "proposal": None, "binding": None,
        "knowledge_versions": [], "trace": [],
    }


def answer_entry_order(observation: Any, read_eligible_skills: Any,
                       *, test_only: bool = False) -> dict[str, Any]:
    """Prepare an answer, re-read, then disclose only a still-bound result.

    The supplied reader is a server-side dependency, never request JSON.
    Only the existing 0003/0004 test adapter is supported. There is no fallback
    to BML, WORLD text, an exercise answer, an earlier card or a queued job.
    A symbolic answer is allowed under ordinary uncertainty. An actionable
    card still requires the adapter's explicit concrete visible context.
    """
    if test_only is not True:
        return _withheld("ABSTAIN", "TEST_ONLY_OPT_IN_REQUIRED")

    initial = entry_path.decide_from_reader(
        observation, read_eligible_skills, test_only=True)
    if initial["binding"] is None:
        return _withheld(initial["status"], initial["reason"],
                         initial["missing_context"])

    fresh = entry_path.next_action_from_reader(
        initial, observation, read_eligible_skills, test_only=True)
    expected_status = ("ACTION" if initial["immediate_action"] is not None
                       else initial["status"])
    expected_reason = ("FRESHLY_RECOMPUTED_TEST_ONLY_FIRST_STEP"
                       if expected_status == "ACTION" else initial["reason"])
    if fresh["status"] != expected_status or fresh["reason"] != expected_reason:
        # Revocation, failed re-read or changed observation withholds BOTH the
        # old explanation and the card. No initial trace escapes this branch.
        return _withheld(fresh["status"], fresh["reason"],
                         fresh.get("missing_context"))
    if expected_status == "ACTION" and (
            fresh.get("binding") != initial["binding"]
            or fresh.get("action") != initial["immediate_action"]):
        return _withheld("ABSTAIN", "ACTION_BINDING_MISMATCH")

    explanation = initial["explanation"]
    return {
        "schema": SCHEMA, "controller_version": VERSION, "scope": SCOPE,
        "test_only": True, "production_ready": False,
        "status": initial["status"], "reason": initial["reason"],
        "missing_context": deepcopy(initial["missing_context"]),
        "answer": {
            "paragraphs_ru": deepcopy(explanation["paragraphs_ru"]),
            "selection_node": explanation["selection_node"],
            "comparison_node": explanation["comparison_node"],
        },
        "decision": {
            "selected_plan": initial["selected_plan"],
            "alternatives": deepcopy(initial["alternatives"]),
            "conditional_premises": deepcopy(initial["conditional_premises"]),
            "success_probability": initial["success_probability"],
            "comparison_confidence": initial["comparison_confidence"],
            "limits": deepcopy(initial["limits"]),
        },
        "proposal": deepcopy(fresh.get("action")),
        "binding": deepcopy(initial["binding"]),
        "knowledge_versions": deepcopy(explanation["citations"]),
        "trace": deepcopy(initial["trace"]),
    }
