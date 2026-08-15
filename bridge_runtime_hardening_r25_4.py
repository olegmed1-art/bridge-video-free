#!/usr/bin/env python3
"""Bridge Video 3.1 FREE internal candidate r25.5.

r25.5 preserves the validated late-OAuth and semantic attribution protections,
quarantines exhausted non-hallucinatory ASR disagreements, and excludes every
unreliable segment from semantic episodes, errors, canon matching and
recommendations. Detected repeated non-speech hallucination remains a hard stop.

The full transcript is preserved with unreliable markers for audit. The public
product name remains exactly ``3.1 FREE``.
"""
from __future__ import annotations

import os

import bridge_runtime_hardening_r25_3 as r25_3
import bridge_worker_3_1_free as core
import run_master_3_1_free as base

REVISION = "3.1-free-r25.5"

_DERIVED_KEYS = (
    "episodes", "learning_interactions", "errors", "strengths",
    "teacher_analysis", "best_explanations", "deals", "decisions",
    "knowledge_gaps",
)


def _unreliable_ids(transcript) -> set[str]:
    return {
        str(s.get("segment_id"))
        for s in (transcript or [])
        if s.get("segment_id") and bool(s.get("unreliable"))
    }


def _derived_evidence_ids(master: dict) -> set[str]:
    refs: set[str] = set()
    for key in _DERIVED_KEYS:
        for item in master.get(key) or []:
            for value in item.get("evidence") or []:
                if value:
                    refs.add(str(value))
    student = master.get("student_analysis") or {}
    for bucket in ("observations", "strengths", "difficulties"):
        for item in student.get(bucket) or []:
            for value in item.get("evidence") or []:
                if value:
                    refs.add(str(value))
    return refs


def install(token_func):
    """Install r25.3, then isolate unreliable transcript text from derivation."""
    requested = os.getenv("BRIDGE_REQUESTED_ALGORITHM_REVISION", "").strip()
    if requested and requested != REVISION:
        raise RuntimeError(
            f"ALGORITHM_REVISION_MISMATCH: requested={requested} executing={REVISION}"
        )

    had_requested = "BRIDGE_REQUESTED_ALGORITHM_REVISION" in os.environ
    saved_requested = os.environ.get("BRIDGE_REQUESTED_ALGORITHM_REVISION")
    os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = r25_3.REVISION
    try:
        r25_3.install(token_func)
    finally:
        if had_requested:
            os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = saved_requested or ""
        else:
            os.environ.pop("BRIDGE_REQUESTED_ALGORITHM_REVISION", None)

    core.ALGORITHM_REVISION = REVISION
    base.ALGORITHM_REVISION = REVISION

    previous_episode_plan = base.semantic_episode_plan
    previous_payload = base.master_analysis_payload
    previous_validate = base.validate_r24_master

    def reliable_semantic_episode_plan(segments, job_id=""):
        reliable = [s for s in (segments or []) if not bool(s.get("unreliable"))]
        return previous_episode_plan(reliable, job_id)

    def payload_r25_5(**kwargs):
        master = previous_payload(**kwargs)
        transcript = master.get("transcript") or []
        unreliable = _unreliable_ids(transcript)
        quality = master.setdefault("content_quality", {})
        quality["unreliable_transcript_segments"] = len(unreliable)
        quality["unreliable_segments_excluded_from_semantic_derivation"] = True
        quality["semantic_derivation_transcript_segments"] = sum(
            1 for s in transcript if not bool(s.get("unreliable"))
        )
        master.setdefault("principles", {})[
            "unreliable_asr_excluded_from_semantic_derivation"
        ] = True
        if unreliable:
            warnings = list(master.get("warnings") or [])
            warnings.append(
                f"ASR isolation QC: {len(unreliable)} unreliable transcript segments are "
                "preserved for review but excluded from derived analytics."
            )
            master["warnings"] = list(dict.fromkeys(warnings))
        return master

    def validate_r25_5_master(master):
        result = dict(previous_validate(master))
        issues = list(result.get("issues") or [])
        contaminated = _unreliable_ids(master.get("transcript")).intersection(
            _derived_evidence_ids(master)
        )
        if contaminated:
            issues.append("unreliable-asr-used-in-derived-analytics")
        result["issues"] = list(dict.fromkeys(issues))
        result["ok"] = not result["issues"]
        result["unreliableDerivedEvidenceCount"] = len(contaminated)
        return result

    base.semantic_episode_plan = reliable_semantic_episode_plan
    base.master_analysis_payload = payload_r25_5
    base.validate_r24_master = validate_r25_5_master


def run(token_func):
    install(token_func)
    import run_master_3_1_free_semantic as semantic
    return semantic.process_job(token_func())
