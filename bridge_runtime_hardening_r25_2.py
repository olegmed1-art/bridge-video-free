#!/usr/bin/env python3
"""Bridge Video 3.1 FREE internal candidate r25.2.

r25.2 keeps the proven r25.1 hard stops for near-empty ASR retries and repeated
non-speech hallucinations, but fixes a semantic safety problem found on a real
known-good bridge lesson: local ASR has no speaker diarization, so text heuristics
must not be presented as confirmed student/teacher attribution.

Rules added here:
- public product name remains 3.1 FREE;
- requested internal revision must be r25.2;
- when no speaker labels exist, actor-specific student/teacher claims are withheld;
- when speaker labels do exist, actor-specific claims that depend on unreliable ASR
  segments are withheld;
- the master gate accepts an explicit "actor attribution unavailable" state instead
  of forcing fabricated student observations.
"""
from __future__ import annotations

import os

import bridge_runtime_hardening_r25_1 as r25_1
import bridge_worker_3_1_free as core
import run_master_3_1_free as base
import run_master_3_1_free_semantic as semantic

REVISION = "3.1-free-r25.2"


def _has_speaker_labels(master: dict) -> bool:
    return any(str(s.get("speaker") or "").strip() for s in (master.get("transcript") or []))


def _unreliable_segment_ids(master: dict) -> set[str]:
    return {
        str(s.get("segment_id"))
        for s in (master.get("transcript") or [])
        if s.get("segment_id") and bool(s.get("unreliable"))
    }


def _depends_on_unreliable(item: dict, unreliable_ids: set[str]) -> bool:
    return bool(unreliable_ids.intersection(str(x) for x in (item.get("evidence") or []) if x))


def sanitize_actor_specific_semantics(master: dict) -> dict:
    """Remove actor-specific claims that the transcript cannot safely support."""
    transcript = master.get("transcript") or []
    has_speakers = _has_speaker_labels(master)
    unreliable_ids = _unreliable_segment_ids(master)
    excluded = 0

    quality = master.setdefault("content_quality", {})
    student = master.setdefault("student_analysis", {})
    cycles = list(master.get("learning_interactions") or [])

    if not has_speakers:
        # Local ASR currently does not diarize speakers.  Role-cue heuristics remain useful
        # for finding generic teaching moments, but they are insufficient to claim that a
        # specific utterance was a student's action or a teacher's intervention.
        old_obs = list(student.get("observations") or [])
        excluded += len(old_obs)
        student["observations"] = []
        student["actor_attribution_status"] = "unavailable_without_speaker_labels"
        student["attribution_note"] = (
            "Персональные действия ученика не публикуются: исходная расшифровка не содержит "
            "надёжных меток говорящих. Текстовые эвристики используются только для поиска "
            "кандидатов учебных событий."
        )

        sanitized_cycles = []
        for cycle in cycles:
            c = dict(cycle)
            for key in ("student_action", "teacher_intervention", "student_response"):
                if c.get(key):
                    excluded += 1
                c[key] = None
            c["intervention_type"] = None
            c["outcome"] = "требует проверки после надёжной атрибуции говорящих"
            c["autonomy"] = "не установлена"
            c["confidence"] = "low"
            c["actor_attribution_status"] = "unavailable_without_speaker_labels"
            sanitized_cycles.append(c)
        master["learning_interactions"] = sanitized_cycles

        # Teacher-specific observations are equally unsafe without speaker attribution.
        excluded += len(master.get("teacher_analysis") or [])
        master["teacher_analysis"] = []
        quality["actor_attribution_status"] = "unavailable_without_speaker_labels"
    else:
        # With diarized/source-labelled speech, retain actor-specific claims only when none
        # of their evidence comes from an ASR block already marked unreliable.
        observations = []
        for item in (student.get("observations") or []):
            if _depends_on_unreliable(item, unreliable_ids):
                excluded += 1
            else:
                observations.append(item)
        student["observations"] = observations
        student["actor_attribution_status"] = "speaker_labels_available"

        kept_cycles = []
        for item in cycles:
            if _depends_on_unreliable(item, unreliable_ids):
                excluded += 1
            else:
                kept_cycles.append(item)
        master["learning_interactions"] = kept_cycles

        kept_teacher = []
        for item in (master.get("teacher_analysis") or []):
            if _depends_on_unreliable(item, unreliable_ids):
                excluded += 1
            else:
                kept_teacher.append(item)
        master["teacher_analysis"] = kept_teacher
        quality["actor_attribution_status"] = "speaker_labels_available"

    quality["actor_specific_claims_excluded"] = excluded
    quality["unreliable_transcript_segments"] = len(unreliable_ids)
    quality["speaker_labels_present"] = has_speakers

    warnings = list(master.get("warnings") or [])
    if not has_speakers:
        warnings.append(
            "Actor attribution QC: speaker labels are unavailable; student/teacher-specific "
            "claims were withheld rather than inferred from wording alone."
        )
    elif excluded:
        warnings.append(
            f"Actor attribution QC excluded {excluded} actor-specific claims that depended on "
            "ASR segments already marked unreliable."
        )
    master["warnings"] = list(dict.fromkeys(warnings))
    return master


def install(token_func):
    """Install r25.1 runtime protections and add r25.2 semantic confidence gates."""
    r25_1.install(token_func)

    core.ALGORITHM_REVISION = REVISION
    base.ALGORITHM_REVISION = REVISION

    requested = os.getenv("BRIDGE_REQUESTED_ALGORITHM_REVISION", "").strip()
    if requested and requested != REVISION:
        raise RuntimeError(
            f"ALGORITHM_REVISION_MISMATCH: requested={requested} executing={REVISION}"
        )

    previous_payload = base.master_analysis_payload
    previous_validate = base.validate_r24_master

    def master_payload_r25_2(**kwargs):
        return sanitize_actor_specific_semantics(previous_payload(**kwargs))

    def validate_r25_2_master(master):
        result = dict(previous_validate(master))
        issues = list(result.get("issues") or [])
        actor_status = (master.get("content_quality") or {}).get("actor_attribution_status")
        observations = (master.get("student_analysis") or {}).get("observations") or []

        # r24 previously forced a non-empty student-analysis section whenever important
        # episodes existed.  When speaker identity is genuinely unavailable, the safe
        # output is an explicit UNKNOWN state, not a fabricated actor attribution.
        if actor_status == "unavailable_without_speaker_labels":
            issues = [x for x in issues if x != "missing-student-analysis"]
            if observations:
                issues.append("actor-specific-claims-without-speaker-labels")
            if master.get("teacher_analysis"):
                issues.append("teacher-claims-without-speaker-labels")

        result["issues"] = list(dict.fromkeys(issues))
        result["ok"] = not result["issues"]
        result["actorAttributionStatus"] = actor_status
        return result

    base.master_analysis_payload = master_payload_r25_2
    base.validate_r24_master = validate_r25_2_master


def run(token_func):
    install(token_func)
    return semantic.process_job(token_func())
