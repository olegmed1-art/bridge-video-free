"""Deterministic stage accounting for Universal Video result manifests.

The technical runner status is deliberately kept separate from domain,
pedagogical and publication readiness.  This module is shared by the producer
and the independent verifier so an executed stage cannot also be reported as
deferred and a technically completed bundle cannot masquerade as a complete
bridge analysis.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

READINESS_SCHEMA = "universal-video-readiness-v1"

RUNNER_EXECUTED_STAGES = frozenset(
    {
        "media_preflight",
        "audio_extract",
        "transcribe",
        "transcript_qc",
        "timeline",
        "keyframes",
        "speaker_structure",
        "algorithm_manifest",
        "package",
    }
)


def deferred_stages(planned_stages: Sequence[str]) -> list[str]:
    """Return planned stages which the current runner does not execute."""
    return [stage for stage in planned_stages if stage not in RUNNER_EXECUTED_STAGES]


def build_stage_outcomes(
    planned_stages: Sequence[str],
    *,
    qc_pass: bool,
    speaker_report: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Build one explicit outcome for every planned stage."""
    outcomes: dict[str, str] = {}
    deferred = set(deferred_stages(planned_stages))
    for stage in planned_stages:
        if stage in deferred:
            outcomes[stage] = "DEFERRED"
        elif stage in {"transcribe", "transcript_qc"}:
            outcomes[stage] = "PASS" if qc_pass else "REVIEW"
        elif stage == "speaker_structure":
            outcomes[stage] = (
                "PASS"
                if isinstance(speaker_report, Mapping) and speaker_report.get("quality_gate") == "PASS"
                else "INCONCLUSIVE"
            )
        else:
            outcomes[stage] = "PASS"
    return outcomes


def build_test_readiness(
    planned_stages: Sequence[str],
    *,
    qc_pass: bool,
    speaker_report: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build the fail-closed readiness matrix for the 3.1-test profile."""
    outcomes = build_stage_outcomes(
        planned_stages,
        qc_pass=qc_pass,
        speaker_report=speaker_report,
    )
    deferred = [stage for stage, outcome in outcomes.items() if outcome == "DEFERRED"]
    speaker_outcome = outcomes.get("speaker_structure", "NOT_APPLICABLE")
    domain_outcomes = [
        outcomes.get(stage, "NOT_APPLICABLE")
        for stage in ("bridge_context", "bridge_positions", "educational_candidates")
    ]
    if not qc_pass:
        content_result = "REVIEW"
    elif domain_outcomes and all(outcome == "DEFERRED" for outcome in domain_outcomes):
        content_result = "ARCHIVE_ONLY"
    elif domain_outcomes and all(outcome == "PASS" for outcome in domain_outcomes):
        content_result = "FULL"
    else:
        content_result = "PARTIAL"
    return {
        "schema": READINESS_SCHEMA,
        "technical_execution": "COMPLETED" if qc_pass else "REVIEW",
        "asr_readiness": "PASS" if qc_pass else "REVIEW",
        "speaker_readiness": speaker_outcome,
        "visual_keyframes_readiness": outcomes.get("keyframes", "NOT_APPLICABLE"),
        "bridge_context_readiness": outcomes.get("bridge_context", "NOT_APPLICABLE"),
        "bridge_positions_readiness": outcomes.get("bridge_positions", "NOT_APPLICABLE"),
        "dds3_readiness": outcomes.get("dds3_optional", "NOT_APPLICABLE"),
        "pedagogical_readiness": outcomes.get("educational_candidates", "NOT_APPLICABLE"),
        "publication_readiness": "BLOCKED_SHADOW_ONLY",
        "content_result": content_result,
        "canonical_promotion_allowed": False,
        "production_activation_allowed": False,
        "deferred_stages": deferred,
        "stage_outcomes": outcomes,
    }


__all__ = [
    "READINESS_SCHEMA",
    "RUNNER_EXECUTED_STAGES",
    "build_stage_outcomes",
    "build_test_readiness",
    "deferred_stages",
]
