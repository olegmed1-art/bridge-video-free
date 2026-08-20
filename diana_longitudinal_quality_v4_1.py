#!/usr/bin/env python3
"""Field-hardening overlay for quality v4 decision-window reconstruction.

A private dry-run against an existing completed master showed that permissive
question and answer matching can connect a long teacher explanation to a later,
unrelated student remark.  v4.1 tightens only that semantic linking layer:

* weak interrogative cues require an actual question mark;
* punctuation-less direct questions are allowed only when short and not
  self-answered in the same clause;
* a student action must contain bridge content or a compact direct
  numeric/reasoning answer;
* task and student action must share bridge context, except for a compact pure
  numeric answer to a count question;
* nested prompts sharing the same teacher intervention and student follow-up are
  de-duplicated by keeping the closest student action/task;
* when adjacent teacher segments were merged into one acoustic turn, the chosen
  task is anchored back to the latest matching source segment rather than the
  start of the whole merged turn.

All authority, identity, source-read-only and zero-paid-AI gates remain inherited
from v4/v3/v2.  Raw ASR is never changed.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping, Sequence

import diana_longitudinal_quality_v4 as v4

QUALITY_SCHEMA = v4.QUALITY_SCHEMA
QUALITY_SCHEMA_VERSION = 4
QUALITY_METHOD_VERSION = "diana-quality-v4.1"

STRONG_TASK_CUES = (
    "почему", "как ты дума", "как вы дума", "что ты дума", "что будешь",
    "что будем", "что делать", "что заяв", "как сыг", "посчитай",
    "посчитаем", "выбери", "в чем", "в чём",
)
WEAK_TASK_CUES = (
    "сколько", "какую", "какой", "какая", "что у тебя", "что у нас",
)
SELF_ANSWER_CUES = (
    "потому что", "ответ такой", "то есть получается", "правильно да",
)
NUMBER_WORDS = {
    "ноль", "один", "одна", "два", "две", "три", "четыре", "пять",
    "шесть", "семь", "восемь", "девять", "десять", "одиннадцать",
    "двенадцать", "тринадцать",
}
REASONING_CUES = (
    "думаю", "мне кажется", "наверно", "наверное", "потому", "так как",
    "поэтому",
)


def _task_excerpts_v41(text: object) -> list[str]:
    value = v4._norm(text)
    if not value:
        return []
    chunks = [
        v4._norm(part)
        for part in re.split(r"(?<=[.!?])\s+|[;]\s+", value)
        if v4._norm(part)
    ] or [value]
    out: list[str] = []
    for index, chunk in enumerate(chunks):
        low = v4._low(chunk)
        has_question_mark = "?" in chunk
        has_strong_cue = any(cue in low for cue in STRONG_TASK_CUES)
        has_weak_cue = any(cue in low for cue in WEAK_TASK_CUES)
        if "сколько хочешь" in low:
            continue
        if not has_question_mark and not has_strong_cue:
            continue
        if not has_question_mark and has_strong_cue:
            if len(v4._words(chunk)) > 32 or any(cue in low for cue in SELF_ANSWER_CUES):
                continue
        if has_question_mark is False and has_weak_cue and not has_strong_cue:
            continue

        local = chunk
        if not v4._bridge_relevant(local) and index:
            prior = chunks[index - 1]
            if v4._bridge_relevant(prior):
                local = f"{prior} {chunk}"
        if v4._bridge_relevant(local) and not v4._organizational(local):
            out.append(local[:500])
    return v4._dedupe(out)


def _meaningful_student_action_v41(text: object) -> bool:
    value = v4._norm(text)
    if not value or v4._organizational(value):
        return False
    if v4._bridge_relevant(value):
        return True
    tokens = v4._words(value)
    if not tokens or set(tokens) <= v4.ACK_ONLY:
        return False
    compact = len(tokens) <= 5
    direct_numeric = bool(re.search(r"\d", value)) or any(token in NUMBER_WORDS for token in tokens)
    reasoning = any(cue in v4._low(value) for cue in REASONING_CUES)
    return bool(compact and (direct_numeric or reasoning))


def _bridge_concepts(text: object) -> set[str]:
    low = v4._low(text)
    out = {cue for cue in v4.BRIDGE_CUES if cue in low}
    for match in re.finditer(
        r"(?<!\w)([1-7])\s*(nt|бк|без\s*козыр\w*|♣|♦|♥|♠|треф\w*|буб\w*|черв\w*|пик\w*)",
        low,
        re.I,
    ):
        denomination = re.sub(r"\s+", "", match.group(2))
        out.add(f"bid:{match.group(1)}:{denomination}")
    return out


def _pure_numeric_answer(text: object) -> bool:
    value = v4._low(text)
    tokens = v4._words(value)
    if len(tokens) > 4:
        return False
    if re.search(r"(?:nt|бк|без\s*козыр|треф|буб|черв|пик|♣|♦|♥|♠)", value):
        return False
    return bool(re.search(r"\d", value) or any(token in NUMBER_WORDS for token in tokens))


def _task_action_aligned(task: object, action: object) -> bool:
    shared = _bridge_concepts(task) & _bridge_concepts(action)
    if shared:
        return True
    if "сколько" in v4._low(task) and _pure_numeric_answer(action):
        return True
    return False


def _task_anchor_segment(
    master: Mapping[str, Any],
    task_turn: Mapping[str, Any],
    task: object,
) -> dict[str, Any]:
    """Return the latest source segment supporting the selected task clause.

    Acoustic turn coalescing can merge adjacent teacher prompts.  The interaction
    time/evidence should follow the selected prompt, not the first prompt in the
    merged turn.  Matching is deliberately local to segment IDs already present
    in that acoustic turn; it never searches unrelated transcript regions.
    """
    segment_map = {
        str(item.get("segment_id")): item
        for item in (master.get("transcript") or [])
        if isinstance(item, Mapping) and item.get("segment_id")
    }
    task_low = v4._low(task)
    best: dict[str, Any] | None = None
    for segment_id in task_turn.get("segment_ids") or []:
        segment = segment_map.get(str(segment_id))
        if not segment:
            continue
        text = segment.get("text") or segment.get("analysis_text") or ""
        excerpts = _task_excerpts_v41(text)
        supported = any(
            v4._low(excerpt) == task_low
            or task_low in v4._low(excerpt)
            or v4._low(excerpt) in task_low
            for excerpt in excerpts
        )
        if not supported:
            continue
        if best is None or float(segment.get("start") or 0) >= float(best.get("start") or 0):
            best = dict(segment)
    if best is not None:
        return best
    return {
        "segment_id": (task_turn.get("segment_ids") or [task_turn.get("turn_id")])[-1],
        "start": task_turn.get("start"),
        "end": task_turn.get("end"),
        "text": task_turn.get("text"),
    }


def _transcript_decision_interactions_v41(
    master: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    sections: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    turns = v4._transcript_turns(master)
    candidates: list[dict[str, Any]] = []
    job_id = str(master.get("job_id") or "unknown")

    for task_index, task_turn in enumerate(turns):
        if task_turn.get("role") != v4.ROLE_TEACHER:
            continue
        excerpts = _task_excerpts_v41(task_turn.get("text"))
        if not excerpts:
            continue
        student_hit = v4._find_next(
            turns,
            task_index,
            v4.ROLE_STUDENT,
            _meaningful_student_action_v41,
            float(task_turn.get("end") or 0),
        )
        if not student_hit:
            continue
        student_index, student_turn = student_hit
        task = excerpts[-1]
        if not _task_action_aligned(task, student_turn.get("text")):
            continue
        teacher_hit = v4._find_next(
            turns,
            student_index,
            v4.ROLE_TEACHER,
            v4._teacher_intervention,
            float(student_turn.get("end") or 0),
        )
        if not teacher_hit:
            continue
        teacher_index, teacher_turn = teacher_hit
        followup_hit = v4._find_next(
            turns,
            teacher_index,
            v4.ROLE_STUDENT,
            v4._substantive_followup,
            float(teacher_turn.get("end") or 0),
        )
        if not followup_hit:
            continue
        _, followup_turn = followup_hit

        task_anchor = _task_anchor_segment(master, task_turn, task)
        task_segment_id = str(task_anchor.get("segment_id") or "")
        evidence = v4._dedupe(
            ([task_segment_id] if task_segment_id else [])
            + list(student_turn.get("segment_ids") or [])
            + list(teacher_turn.get("segment_ids") or [])
            + list(followup_turn.get("segment_ids") or [])
        )
        if len(evidence) < 4:
            continue
        start = float(task_anchor.get("start") or task_turn.get("start") or 0)
        end = float(followup_turn.get("end") or followup_turn.get("start") or start)
        candidates.append({
            "interaction_id": v4._stable_id(
                "interactionv41",
                job_id,
                task_segment_id or task_turn.get("turn_id"),
                student_turn.get("turn_id"),
                teacher_turn.get("turn_id"),
                followup_turn.get("turn_id"),
            ),
            "status": "COMPLETE_EVIDENCE_CANDIDATE",
            "source": "transcript_decision_window_v4_1",
            "section_id": v4.v2._section_for_time(sections, start),
            "focus_episode_id": v4._nearest_focus_episode(events, start),
            "start": start,
            "end": end,
            "task": task,
            "student_action": student_turn.get("text"),
            "teacher_intervention": teacher_turn.get("text"),
            "intervention_type": "observed_explicit_role_turn",
            "student_followup": followup_turn.get("text"),
            "observed_outcome": (
                "Содержательная реакция после вмешательства наблюдается; "
                "правильность решения этим этапом не установлена."
            ),
            "outcome_correctness_verified": False,
            "help_state": "after_observed_intervention",
            "transfer_status": "NOT_CONFIRMED",
            "actor_attribution_status": "EXPLICIT_ACOUSTIC_ROLE_SUPPORTED",
            "event_ids": [],
            "evidence_refs": evidence,
            "visual_evidence_refs": [],
            "decision_window": {
                "task_turn_id": task_turn.get("turn_id"),
                "task_anchor_segment_id": task_segment_id or None,
                "student_turn_id": student_turn.get("turn_id"),
                "teacher_intervention_turn_id": teacher_turn.get("turn_id"),
                "student_followup_turn_id": followup_turn.get("turn_id"),
                "student_turn_start": float(student_turn.get("start") or 0),
                "teacher_intervention_turn_start": float(teacher_turn.get("start") or 0),
                "student_followup_turn_start": float(followup_turn.get("start") or 0),
                "task_to_followup_seconds": round(end - start, 3),
                "task_action_bridge_alignment": True,
            },
            "rejection_reasons": [],
            "profile_write_allowed": False,
            "person_specific_write_allowed": False,
            "methodology_activation_allowed": False,
        })

    best_by_core: dict[tuple[str, str], dict[str, Any]] = {}
    for item in candidates:
        window = item.get("decision_window") or {}
        core = (
            str(window.get("teacher_intervention_turn_id") or ""),
            str(window.get("student_followup_turn_id") or ""),
        )
        previous = best_by_core.get(core)
        score = (
            float(window.get("student_turn_start") or 0),
            float(item.get("start") or 0),
        )
        if previous is None:
            best_by_core[core] = item
            continue
        previous_window = previous.get("decision_window") or {}
        previous_score = (
            float(previous_window.get("student_turn_start") or 0),
            float(previous.get("start") or 0),
        )
        if score > previous_score:
            best_by_core[core] = item
    return sorted(best_by_core.values(), key=lambda item: float(item.get("start") or 0))


def build_quality_layer(
    master: Mapping[str, Any],
    lesson_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    saved = v4._transcript_decision_interactions
    v4._transcript_decision_interactions = _transcript_decision_interactions_v41
    try:
        quality = deepcopy(v4.build_quality_layer(master, lesson_identity))
    finally:
        v4._transcript_decision_interactions = saved

    quality["schema_version"] = QUALITY_SCHEMA_VERSION
    quality["method_version"] = QUALITY_METHOD_VERSION
    interactions = [
        item for item in (quality.get("learning_interactions") or [])
        if isinstance(item, Mapping)
    ]
    v41 = [
        item for item in interactions
        if item.get("source") == "transcript_decision_window_v4_1"
        and item.get("status") == "COMPLETE_EVIDENCE_CANDIDATE"
    ]
    quality["interaction_reconstruction_v4_1"] = {
        "status": "FIELD_HARDENED",
        "complete_decision_window_count": len(v41),
        "weak_question_requires_question_mark": True,
        "punctuationless_question_must_be_short_and_not_self_answered": True,
        "task_action_bridge_alignment_required": True,
        "nested_prompt_deduplication": "teacher_intervention+student_followup core",
        "merged_turn_task_reanchored_to_latest_matching_source_segment": True,
        "correctness_inferred_from_followup": False,
    }
    counts = quality.setdefault("counts", {})
    counts["transcript_decision_window_complete_interactions_v4_1"] = len(v41)
    quality.setdefault("incremental_processing", {})["heavy_video_reprocessing_required"] = False
    quality["incremental_processing"]["raw_asr_mutated"] = False
    quality.setdefault("cost_gate", {})["paid_ai_api_required"] = False
    quality["cost_gate"]["paid_cloud_required"] = False
    return quality


__all__ = [
    "QUALITY_SCHEMA",
    "QUALITY_SCHEMA_VERSION",
    "QUALITY_METHOD_VERSION",
    "_task_excerpts_v41",
    "_meaningful_student_action_v41",
    "_task_action_aligned",
    "_task_anchor_segment",
    "_transcript_decision_interactions_v41",
    "build_quality_layer",
]
