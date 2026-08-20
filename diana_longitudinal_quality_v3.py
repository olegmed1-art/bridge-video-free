#!/usr/bin/env python3
"""Dynamic learning-state extension for School bridge lesson videos.

Version 3 wraps the conservative v2 evidence layer and adds a learning-history
model without weakening any existing authority gates.  The new objects are
candidates only: they may describe observed role-level learning evidence, but
they never activate School canon, curriculum, methodology, or a production
student profile.

The key invariant is anti-confirmation-bias: every learning hypothesis remains
open to counterevidence, and stable skill states require independent
cross-lesson evidence rather than repetition inside one video.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from diana_longitudinal_quality_v2 import build_quality_layer as build_quality_layer_v2

QUALITY_SCHEMA = "diana-longitudinal-quality"
QUALITY_SCHEMA_VERSION = 3
QUALITY_METHOD_VERSION = "diana-quality-v3.0"

SKILL_STATES = (
    "NOT_OBSERVED",
    "EXPLAINED",
    "UNDERSTOOD_WITH_HELP",
    "INDEPENDENT_SUCCESS_ONCE",
    "STABLE_INDEPENDENT",
    "FAILED_UNDER_LOAD",
    "REVIEW_REQUIRED",
)

INDEPENDENT_HELP_STATES = {
    "independent",
    "independent_success",
    "without_help",
    "without_prompt",
    "autonomous",
    "self",
}
POSITIVE_OUTCOME_CUES = (
    "правиль",
    "верно",
    "успеш",
    "самостоятель",
    "correct",
    "success",
)
KNOWLEDGE_GAP_CUES = (
    "не знаю",
    "не помню",
    "не понимаю",
    "забыл",
    "забыла",
    "не выуч",
)
EXECUTION_FAILURE_CUES = (
    "знал, но",
    "знала, но",
    "потороп",
    "не замет",
    "пропуст",
    "невнимател",
    "ошиблась",
    "ошибся",
)
UNDER_LOAD_CUES = (
    "под нагруз",
    "не хватило времени",
    "в цейтнот",
    "потороп",
    "из-за темпа",
)
EXPLANATION_CUES = (
    "нужно",
    "надо",
    "правило",
    "означает",
    "показывает",
    "обещает",
    "если ",
)
TOPIC_ANCHORS = (
    "торговля",
    "открытие",
    "ответ",
    "ребид",
    "пас",
    "контра",
    "интервенция",
    "стейман",
    "трансфер",
    "инвит",
    "форсинг",
    "фит",
    "без козыря",
    "козырь",
    "контракт",
    "гейм",
    "шлем",
    "розыгрыш",
    "защита",
    "первый ход",
    "импас",
    "экспас",
    "взятка",
    "переход",
    "масть",
    "мажор",
    "минор",
    "расклад",
)


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _low(value: object) -> str:
    return _norm(value).casefold()


def _stable_id(kind: str, *parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    return f"{kind}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def _json_digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _dedupe(values: Sequence[object]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value))


def _text_topics(value: object) -> list[str]:
    low = _low(value)
    return [anchor for anchor in TOPIC_ANCHORS if anchor in low]


def _episode_topics(master: Mapping[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for episode in master.get("episodes") or []:
        if not isinstance(episode, Mapping) or not episode.get("episode_id"):
            continue
        topics = [_norm(topic) for topic in (episode.get("terms") or []) if _norm(topic)]
        if not topics:
            topics = _text_topics(episode.get("summary_text"))
        out[str(episode["episode_id"])] = _dedupe(topics)
    return out


def _topics_for_interaction(
    interaction: Mapping[str, Any],
    episode_topics: Mapping[str, Sequence[str]],
) -> list[str]:
    focus = str(interaction.get("focus_episode_id") or "")
    topics = list(episode_topics.get(focus) or [])
    if not topics:
        topics = _text_topics(
            " ".join(
                _norm(interaction.get(key))
                for key in ("task", "student_action", "teacher_intervention", "student_followup")
            )
        )
    return _dedupe(topics)


def _explicit_independent_success(interaction: Mapping[str, Any]) -> bool:
    help_state = _low(interaction.get("help_state"))
    outcome = _low(interaction.get("observed_outcome"))
    explicit_status = _low(
        interaction.get("success_status")
        or interaction.get("autonomy_status")
        or interaction.get("result_status")
    )
    independent = help_state in INDEPENDENT_HELP_STATES or "independent" in explicit_status
    positive = any(cue in outcome for cue in POSITIVE_OUTCOME_CUES) or any(
        cue in explicit_status for cue in ("correct", "success", "verified")
    )
    return bool(independent and positive)


def _explicit_under_load_failure(interaction: Mapping[str, Any]) -> bool:
    text = _low(
        " ".join(
            _norm(interaction.get(key))
            for key in ("student_action", "teacher_intervention", "student_followup", "observed_outcome")
        )
    )
    failure = any(
        cue in text
        for cue in ("неправиль", "ошиб", "не получилось", "failure", "incorrect")
    )
    return failure and any(cue in text for cue in UNDER_LOAD_CUES)


def _prior_learning_state(master: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("prior_learning_state", "learning_history", "prior_quality_v3"):
        value = master.get(key)
        if isinstance(value, Mapping):
            return value
    return {}


def _prior_skill_map(prior: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    candidates = prior.get("skill_states")
    if not isinstance(candidates, list):
        candidates = prior.get("skill_state_changes")
    if not isinstance(candidates, list):
        dynamic = prior.get("dynamic_learning_model")
        if isinstance(dynamic, Mapping):
            candidates = dynamic.get("skill_state_changes")
    out: dict[str, Mapping[str, Any]] = {}
    for item in candidates or []:
        if not isinstance(item, Mapping):
            continue
        topic = _norm(item.get("topic_candidate") or item.get("topic"))
        if topic:
            out[topic] = item
    return out


def _prior_hypotheses(prior: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    candidates = prior.get("hypotheses_requiring_confirmation")
    if not isinstance(candidates, list):
        dynamic = prior.get("dynamic_learning_model")
        candidates = dynamic.get("hypotheses_requiring_confirmation") if isinstance(dynamic, Mapping) else []
    return [item for item in (candidates or []) if isinstance(item, Mapping)]


def _prior_lesson_ids(item: Mapping[str, Any]) -> set[str]:
    values: list[object] = []
    history = item.get("history_support")
    if isinstance(history, Mapping):
        values.extend(history.get("independent_lesson_ids") or [])
        values.extend(history.get("lesson_ids") or [])
    values.extend(item.get("independent_lesson_ids") or [])
    lesson_id = item.get("lesson_id")
    if lesson_id:
        values.append(lesson_id)
    return {str(value) for value in values if value}


def _topic_observations(
    master: Mapping[str, Any],
    quality: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    episode_topics = _episode_topics(master)
    observations: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "events": [],
            "interactions": [],
            "explanation_count": 0,
            "partial_count": 0,
            "complete_count": 0,
            "aided_followup_count": 0,
            "independent_success_count": 0,
            "under_load_failure_count": 0,
            "error_signal_count": 0,
            "evidence_refs": [],
        }
    )

    events = ((quality.get("hierarchy") or {}).get("atomic_events") or [])
    for event in events:
        if not isinstance(event, Mapping):
            continue
        topics = list(episode_topics.get(str(event.get("source_episode_id") or "")) or [])
        if not topics:
            topics = _text_topics(event.get("text"))
        for topic in topics:
            bucket = observations[topic]
            bucket["events"].append(event)
            bucket["evidence_refs"].extend(event.get("evidence_refs") or [])
            text = _low(event.get("text"))
            event_type = _low(event.get("event_type"))
            if event.get("speaker_role") == "teacher" and (
                "объяс" in event_type or any(cue in text for cue in EXPLANATION_CUES)
            ):
                bucket["explanation_count"] += 1
            if event.get("speaker_role") == "student" and any(
                cue in text for cue in KNOWLEDGE_GAP_CUES + EXECUTION_FAILURE_CUES
            ):
                bucket["error_signal_count"] += 1

    for interaction in quality.get("learning_interactions") or []:
        if not isinstance(interaction, Mapping):
            continue
        topics = _topics_for_interaction(interaction, episode_topics)
        for topic in topics:
            bucket = observations[topic]
            bucket["interactions"].append(interaction)
            bucket["evidence_refs"].extend(interaction.get("evidence_refs") or [])
            if interaction.get("status") == "COMPLETE_EVIDENCE_CANDIDATE":
                bucket["complete_count"] += 1
            else:
                bucket["partial_count"] += 1
            if interaction.get("teacher_intervention") and interaction.get("student_followup"):
                bucket["aided_followup_count"] += 1
            if _explicit_independent_success(interaction):
                bucket["independent_success_count"] += 1
            if _explicit_under_load_failure(interaction):
                bucket["under_load_failure_count"] += 1
            student_text = _low(interaction.get("student_action"))
            if any(cue in student_text for cue in KNOWLEDGE_GAP_CUES + EXECUTION_FAILURE_CUES):
                bucket["error_signal_count"] += 1

    for bucket in observations.values():
        bucket["evidence_refs"] = _dedupe(bucket["evidence_refs"])
    return dict(observations)


def _state_for_topic(
    topic: str,
    observation: Mapping[str, Any],
    prior_item: Mapping[str, Any] | None,
    current_lesson_id: str | None,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    independent_count = int(observation.get("independent_success_count") or 0)
    under_load_count = int(observation.get("under_load_failure_count") or 0)
    aided_count = int(observation.get("aided_followup_count") or 0)
    partial_count = int(observation.get("partial_count") or 0)
    error_count = int(observation.get("error_signal_count") or 0)
    explanation_count = int(observation.get("explanation_count") or 0)

    if under_load_count:
        reasons.append("EXPLICIT_FAILURE_UNDER_LOAD_EVIDENCE")
        return "FAILED_UNDER_LOAD", reasons

    if independent_count:
        prior_state = _norm(
            (prior_item or {}).get("current_state_candidate")
            or (prior_item or {}).get("state")
        )
        prior_lessons = _prior_lesson_ids(prior_item or {})
        if current_lesson_id:
            prior_lessons.discard(current_lesson_id)
        if prior_state == "STABLE_INDEPENDENT" or len(prior_lessons) >= 2:
            reasons.append("INDEPENDENT_SUCCESS_WITH_CROSS_LESSON_SUPPORT")
            return "STABLE_INDEPENDENT", reasons
        reasons.append("EXPLICIT_INDEPENDENT_SUCCESS_THIS_LESSON")
        return "INDEPENDENT_SUCCESS_ONCE", reasons

    if aided_count:
        reasons.append("FOLLOWUP_OBSERVED_AFTER_TEACHER_INTERVENTION")
        return "UNDERSTOOD_WITH_HELP", reasons

    if partial_count >= 2 or error_count >= 2:
        reasons.append("REPEATED_PARTIAL_OR_ERROR_SIGNAL")
        return "REVIEW_REQUIRED", reasons

    if explanation_count:
        reasons.append("TEACHER_EXPLANATION_OBSERVED")
        return "EXPLAINED", reasons

    reasons.append("TOPIC_PRESENT_WITHOUT_STATE_QUALIFYING_EVIDENCE")
    return "NOT_OBSERVED", reasons


def build_skill_state_changes(
    master: Mapping[str, Any],
    quality: Mapping[str, Any],
    lesson_identity: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    prior = _prior_learning_state(master)
    prior_map = _prior_skill_map(prior)
    current_lesson_id = str((lesson_identity or {}).get("lesson_id") or "") or None
    observations = _topic_observations(master, quality)
    out: list[dict[str, Any]] = []

    for topic in sorted(observations):
        observation = observations[topic]
        previous = prior_map.get(topic)
        state, reasons = _state_for_topic(topic, observation, previous, current_lesson_id)
        previous_state = _norm(
            (previous or {}).get("current_state_candidate")
            or (previous or {}).get("state")
        ) or "UNKNOWN_NO_HISTORY"
        if previous is None:
            change_status = "BASELINE_OBSERVATION_ONLY"
        elif previous_state == state:
            change_status = "NO_CONFIRMED_CHANGE"
        else:
            change_status = "CHANGE_CANDIDATE_REQUIRES_CONFIRMATION"

        prior_lessons = sorted(_prior_lesson_ids(previous or {}))
        independent_lessons = set(prior_lessons)
        if state in {"INDEPENDENT_SUCCESS_ONCE", "STABLE_INDEPENDENT"} and current_lesson_id:
            independent_lessons.add(current_lesson_id)
        out.append({
            "skill_state_change_id": _stable_id(
                "skillstate", master.get("job_id"), topic, previous_state, state
            ),
            "topic_candidate": topic,
            "previous_state": previous_state,
            "current_state_candidate": state,
            "allowed_states": list(SKILL_STATES),
            "change_status": change_status,
            "state_reasons": reasons,
            "lesson_id": current_lesson_id,
            "lesson_number": (lesson_identity or {}).get("lesson_number"),
            "history_support": {
                "history_supplied": bool(prior),
                "prior_lesson_ids": prior_lessons,
                "independent_lesson_ids": sorted(independent_lessons),
                "stable_state_requires_independent_cross_lesson_evidence": True,
            },
            "signal_counts": {
                key: int(observation.get(key) or 0)
                for key in (
                    "explanation_count",
                    "partial_count",
                    "complete_count",
                    "aided_followup_count",
                    "independent_success_count",
                    "under_load_failure_count",
                    "error_signal_count",
                )
            },
            "evidence_refs": list(observation.get("evidence_refs") or []),
            "confidence_status": "CANDIDATE_ONLY",
            "production_profile_write_allowed": False,
            "person_specific_write_allowed": False,
        })
    return out


def build_learning_evidence_chains(
    master: Mapping[str, Any],
    quality: Mapping[str, Any],
) -> list[dict[str, Any]]:
    episode_topics = _episode_topics(master)
    interactions = [
        item for item in (quality.get("learning_interactions") or [])
        if isinstance(item, Mapping)
    ]
    interactions.sort(key=lambda item: float(item.get("start") or 0))
    topics_by_id = {
        str(item.get("interaction_id")): _topics_for_interaction(item, episode_topics)
        for item in interactions
    }
    out: list[dict[str, Any]] = []

    for index, item in enumerate(interactions):
        topics = topics_by_id.get(str(item.get("interaction_id"))) or []
        if not topics and not item.get("teacher_intervention"):
            continue
        later = []
        for candidate in interactions[index + 1:]:
            candidate_topics = topics_by_id.get(str(candidate.get("interaction_id"))) or []
            if set(topics) & set(candidate_topics):
                later.append(candidate)
        first_application = later[0] if later else None
        explanation = item.get("teacher_intervention")
        reaction = item.get("student_followup")
        application = first_application.get("student_action") if first_application else None
        result = first_application.get("observed_outcome") if first_application else None
        complete_through_application = bool(explanation and reaction and application and result)
        evidence = list(item.get("evidence_refs") or [])
        if first_application:
            evidence.extend(first_application.get("evidence_refs") or [])
        out.append({
            "learning_chain_id": _stable_id(
                "learningchain", master.get("job_id"), item.get("interaction_id")
            ),
            "topic_candidates": topics,
            "trigger_task": item.get("task"),
            "pre_intervention_action": item.get("student_action"),
            "stages": {
                "explanation": {
                    "status": "OBSERVED" if explanation else "NOT_OBSERVED",
                    "text": explanation,
                },
                "student_reaction": {
                    "status": "OBSERVED" if reaction else "NOT_OBSERVED",
                    "text": reaction,
                },
                "later_application": {
                    "status": "OBSERVED" if application else "NOT_OBSERVED",
                    "text": application,
                    "interaction_id": first_application.get("interaction_id") if first_application else None,
                },
                "observed_result": {
                    "status": "OBSERVED" if result else "NOT_OBSERVED",
                    "text": result,
                },
                "later_recurrence": {
                    "status": "OBSERVED" if len(later) > 1 else "NOT_OBSERVED",
                    "interaction_ids": [
                        candidate.get("interaction_id") for candidate in later[1:]
                    ],
                },
            },
            "chain_status": (
                "COMPLETE_THROUGH_APPLICATION"
                if complete_through_application
                else "PARTIAL_CHAIN"
            ),
            "cross_lesson_recurrence_confirmed": False,
            "evidence_refs": _dedupe(evidence),
            "promotion_allowed": False,
            "person_specific_write_allowed": False,
        })
    return out


def build_hypotheses(
    master: Mapping[str, Any],
    skill_states: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    state_to_hypothesis = {
        "EXPLAINED": "RETENTION_UNCONFIRMED",
        "UNDERSTOOD_WITH_HELP": "INDEPENDENCE_UNCONFIRMED",
        "INDEPENDENT_SUCCESS_ONCE": "GENERALIZATION_UNCONFIRMED",
        "FAILED_UNDER_LOAD": "LOAD_SENSITIVITY_CANDIDATE",
        "REVIEW_REQUIRED": "POSSIBLE_PERSISTENT_GAP",
    }
    out: list[dict[str, Any]] = []
    for skill in skill_states:
        topic = _norm(skill.get("topic_candidate"))
        state = _norm(skill.get("current_state_candidate"))
        hypothesis_type = state_to_hypothesis.get(state)
        if hypothesis_type:
            out.append({
                "hypothesis_id": _stable_id(
                    "hypothesis", master.get("job_id"), topic, hypothesis_type
                ),
                "topic_candidate": topic,
                "hypothesis_type": hypothesis_type,
                "status": "OPEN_CANDIDATE",
                "basis_state": state,
                "evidence_refs": list(skill.get("evidence_refs") or []),
                "confirmation_required": True,
                "disconfirmation_search_required": True,
                "promotion_allowed": False,
                "person_specific_write_allowed": False,
            })

        signals = skill.get("signal_counts") if isinstance(skill.get("signal_counts"), Mapping) else {}
        if (
            int(signals.get("partial_count") or 0) >= 2
            or int(signals.get("error_signal_count") or 0) >= 2
        ) and hypothesis_type != "POSSIBLE_PERSISTENT_GAP":
            out.append({
                "hypothesis_id": _stable_id(
                    "hypothesis", master.get("job_id"), topic, "POSSIBLE_SKILL_GAP"
                ),
                "topic_candidate": topic,
                "hypothesis_type": "POSSIBLE_SKILL_GAP",
                "status": "OPEN_CANDIDATE",
                "basis_state": state,
                "evidence_refs": list(skill.get("evidence_refs") or []),
                "confirmation_required": True,
                "disconfirmation_search_required": True,
                "promotion_allowed": False,
                "person_specific_write_allowed": False,
            })
    return out


def build_evidence_against_hypotheses(
    master: Mapping[str, Any],
    skill_states: Sequence[Mapping[str, Any]],
    current_hypotheses: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    prior = _prior_learning_state(master)
    hypotheses = list(_prior_hypotheses(prior)) + list(current_hypotheses)
    skills = {
        _norm(item.get("topic_candidate")): item
        for item in skill_states
        if _norm(item.get("topic_candidate"))
    }
    out: list[dict[str, Any]] = []
    negative_types = {
        "POSSIBLE_SKILL_GAP",
        "POSSIBLE_PERSISTENT_GAP",
        "INDEPENDENCE_UNCONFIRMED",
        "LOAD_SENSITIVITY_CANDIDATE",
    }
    for hypothesis in hypotheses:
        topic = _norm(hypothesis.get("topic_candidate") or hypothesis.get("topic"))
        kind = _norm(hypothesis.get("hypothesis_type"))
        skill = skills.get(topic)
        if not skill or kind not in negative_types:
            continue
        state = _norm(skill.get("current_state_candidate"))
        if state not in {"INDEPENDENT_SUCCESS_ONCE", "STABLE_INDEPENDENT"}:
            continue
        hypothesis_id = str(
            hypothesis.get("hypothesis_id")
            or _stable_id("hypothesis", "prior", topic, kind)
        )
        out.append({
            "counterevidence_id": _stable_id(
                "counterevidence", master.get("job_id"), hypothesis_id, state
            ),
            "hypothesis_id": hypothesis_id,
            "topic_candidate": topic,
            "hypothesis_type": kind,
            "counterevidence_type": "INDEPENDENT_SUCCESS_OBSERVED",
            "current_state_candidate": state,
            "evidence_refs": list(skill.get("evidence_refs") or []),
            "hypothesis_resolved": False,
            "review_required": True,
            "promotion_allowed": False,
            "person_specific_write_allowed": False,
        })
    return out


def build_error_mode_candidates(
    master: Mapping[str, Any],
    quality: Mapping[str, Any],
) -> list[dict[str, Any]]:
    episode_topics = _episode_topics(master)
    out: list[dict[str, Any]] = []
    for item in quality.get("learning_interactions") or []:
        if not isinstance(item, Mapping):
            continue
        text = _low(
            " ".join(
                _norm(item.get(key))
                for key in ("student_action", "student_followup", "observed_outcome")
            )
        )
        if any(cue in text for cue in KNOWLEDGE_GAP_CUES):
            mode = "KNOWLEDGE_GAP_CANDIDATE"
            reason = "EXPLICIT_KNOWLEDGE_OR_RECALL_CUE"
        elif any(cue in text for cue in EXECUTION_FAILURE_CUES):
            mode = "EXECUTION_FAILURE_CANDIDATE"
            reason = "EXPLICIT_EXECUTION_CUE"
        elif item.get("status") != "COMPLETE_EVIDENCE_CANDIDATE":
            mode = "UNRESOLVED"
            reason = "PARTIAL_INTERACTION_INSUFFICIENT_FOR_CAUSE"
        else:
            continue
        out.append({
            "error_mode_candidate_id": _stable_id(
                "errormode", master.get("job_id"), item.get("interaction_id"), mode
            ),
            "interaction_id": item.get("interaction_id"),
            "topic_candidates": _topics_for_interaction(item, episode_topics),
            "error_mode_candidate": mode,
            "reason": reason,
            "cause_confirmed": False,
            "requires_targeted_probe": True,
            "evidence_refs": list(item.get("evidence_refs") or []),
            "promotion_allowed": False,
            "person_specific_write_allowed": False,
        })
    return out


def build_teaching_intervention_effect_candidates(
    master: Mapping[str, Any],
    quality: Mapping[str, Any],
) -> list[dict[str, Any]]:
    episode_topics = _episode_topics(master)
    out: list[dict[str, Any]] = []
    for item in quality.get("learning_interactions") or []:
        if not isinstance(item, Mapping) or not item.get("teacher_intervention"):
            continue
        followup = item.get("student_followup")
        out.append({
            "teaching_effect_candidate_id": _stable_id(
                "teachingeffect", master.get("job_id"), item.get("interaction_id")
            ),
            "interaction_id": item.get("interaction_id"),
            "topic_candidates": _topics_for_interaction(item, episode_topics),
            "intervention": item.get("teacher_intervention"),
            "observed_followup": followup,
            "effect_status": (
                "FOLLOWUP_OBSERVED_AFTER_INTERVENTION"
                if followup
                else "NO_FOLLOWUP_OBSERVED"
            ),
            "causal_effect_confirmed": False,
            "cross_lesson_comparison_required": True,
            "evidence_refs": list(item.get("evidence_refs") or []),
            "methodology_activation_allowed": False,
            "promotion_allowed": False,
            "person_specific_write_allowed": False,
        })
    return out


def build_recommended_next_probes(
    master: Mapping[str, Any],
    skill_states: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    instructions = {
        "NOT_OBSERVED": (
            "Наблюдать навык в естественной подходящей ситуации; не делать вывод "
            "по отсутствию данных."
        ),
        "EXPLAINED": (
            "Проверить воспроизведение и применение без предварительной подсказки "
            "в новой подходящей ситуации."
        ),
        "UNDERSTOOD_WITH_HELP": (
            "Проверить самостоятельное применение без предварительного напоминания."
        ),
        "INDEPENDENT_SUCCESS_ONCE": (
            "Проверить перенос на изменённый контекст и повторяемость самостоятельного решения."
        ),
        "STABLE_INDEPENDENT": (
            "Проверять только естественным повторным наблюдением; не усложнять тему "
            "автоматически без учебной необходимости."
        ),
        "FAILED_UNDER_LOAD": (
            "Повторить навык в сопоставимой нагрузке и отдельно проверить знание правила "
            "в спокойной ситуации."
        ),
        "REVIEW_REQUIRED": (
            "Развести знание и исполнение: сначала коротко проверить знание, затем дать "
            "практическое решение без подсказки."
        ),
    }
    out: list[dict[str, Any]] = []
    for skill in skill_states:
        topic = _norm(skill.get("topic_candidate"))
        state = _norm(skill.get("current_state_candidate"))
        out.append({
            "recommended_probe_id": _stable_id(
                "nextprobe", master.get("job_id"), topic, state
            ),
            "topic_candidate": topic,
            "basis_state": state,
            "probe_instruction": instructions.get(
                state,
                "Проверить навык в новой подходящей ситуации без предварительного напоминания.",
            ),
            "exact_bridge_content_source": "VERIFIED_SCHOOL_CANON_ONLY",
            "success_definition": (
                "Наблюдаемое решение с Evidence; автоматический числовой порог не задаётся."
            ),
            "retention_check_required": state in {
                "EXPLAINED",
                "UNDERSTOOD_WITH_HELP",
                "INDEPENDENT_SUCCESS_ONCE",
                "REVIEW_REQUIRED",
            },
            "generalization_check_required": state in {
                "INDEPENDENT_SUCCESS_ONCE",
                "STABLE_INDEPENDENT",
            },
            "knowledge_vs_execution_check_required": state in {
                "FAILED_UNDER_LOAD",
                "REVIEW_REQUIRED",
            },
            "evidence_refs": list(skill.get("evidence_refs") or []),
            "status": "PENDING_CANDIDATE",
            "promotion_allowed": False,
            "person_specific_write_allowed": False,
        })
    return out


def _staging_record(
    master: Mapping[str, Any],
    candidate_type: str,
    stable_key: object,
    payload: Mapping[str, Any],
    quality_status: str,
) -> dict[str, Any]:
    return {
        "candidate_id": _stable_id(
            "candidate", master.get("job_id"), candidate_type, stable_key
        ),
        "candidate_type": candidate_type,
        "stable_key": str(stable_key),
        "quality_status": quality_status,
        "promotion_status": "STAGING_ONLY",
        "promotion_allowed": False,
        "reasons": [],
        "evidence_refs": list(payload.get("evidence_refs") or []),
        "payload": dict(payload),
        "method_version": QUALITY_METHOD_VERSION,
    }


def build_quality_layer(
    master: Mapping[str, Any],
    lesson_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    quality = deepcopy(build_quality_layer_v2(master, lesson_identity))
    skill_states = build_skill_state_changes(master, quality, lesson_identity)
    chains = build_learning_evidence_chains(master, quality)
    hypotheses = build_hypotheses(master, skill_states)
    counterevidence = build_evidence_against_hypotheses(
        master, skill_states, hypotheses
    )
    error_modes = build_error_mode_candidates(master, quality)
    teaching_effects = build_teaching_intervention_effect_candidates(master, quality)
    next_probes = build_recommended_next_probes(master, skill_states)
    prior = _prior_learning_state(master)

    quality["schema"] = QUALITY_SCHEMA
    quality["schema_version"] = QUALITY_SCHEMA_VERSION
    quality["method_version"] = QUALITY_METHOD_VERSION
    quality["dynamic_learning_model"] = {
        "status": "CANDIDATE_ONLY",
        "history_status": (
            "PRIOR_HISTORY_SUPPLIED"
            if prior
            else "NO_PRIOR_HISTORY_SUPPLIED"
        ),
        "identity_scope": "ROLE_LEVEL_ONLY",
        "learning_evidence_chains": chains,
        "skill_state_changes": skill_states,
        "hypotheses_requiring_confirmation": hypotheses,
        "evidence_against_existing_hypotheses": counterevidence,
        "error_mode_candidates": error_modes,
        "teaching_intervention_effect_candidates": teaching_effects,
        "recommended_next_probes": next_probes,
        "rules": {
            "observation_interpretation_pedagogy_separated": True,
            "counterevidence_required": True,
            "stable_skill_requires_cross_lesson_evidence": True,
            "single_lesson_can_create_stable_skill": False,
            "person_specific_write_without_operational_identity_mapping": False,
        },
    }

    quality["learning_evidence_chains"] = chains
    quality["skill_state_changes"] = skill_states
    quality["hypotheses_requiring_confirmation"] = hypotheses
    quality["evidence_against_existing_hypotheses"] = counterevidence
    quality["error_mode_candidates"] = error_modes
    quality["teaching_intervention_effect_candidates"] = teaching_effects
    quality["recommended_next_probes"] = next_probes
    quality["history_gate"] = {
        "status": (
            "HISTORY_AVAILABLE_FOR_COMPARISON"
            if prior
            else "NO_PRIOR_HISTORY_SUPPLIED"
        ),
        "stable_state_requires_independent_cross_lesson_evidence": True,
        "single_lesson_stable_state_allowed": False,
        "automatic_cross_lesson_person_join_allowed": False,
        "reason": (
            "Cross-lesson person-specific joins require an operational identity mapping; "
            "role-level evidence may be staged without one."
        ),
    }

    authority = quality.get("authority")
    if not isinstance(authority, dict):
        authority = {}
        quality["authority"] = authority
    authority.update({
        "canon_activation": "DENY",
        "curriculum_activation": "DENY",
        "student_profile_production_write": "DENY",
        "methodology_activation": "DENY",
        "student_skill_state_production_write": "DENY",
        "hypothesis_promotion": "DENY",
        "person_specific_learning_conclusion": "DENY",
        "database_destination": "STAGING_ONLY",
    })

    staging = [
        dict(item)
        for item in (quality.get("candidate_staging_records") or [])
        if isinstance(item, Mapping)
    ]
    for item in staging:
        item["method_version"] = QUALITY_METHOD_VERSION
        item["promotion_status"] = "STAGING_ONLY"
        item["promotion_allowed"] = False

    for item in chains:
        staging.append(_staging_record(
            master, "learning_evidence_chain", item["learning_chain_id"], item, item["chain_status"]
        ))
    for item in skill_states:
        staging.append(_staging_record(
            master, "skill_state_change", item["skill_state_change_id"], item, item["change_status"]
        ))
    for item in hypotheses:
        staging.append(_staging_record(
            master, "learning_hypothesis", item["hypothesis_id"], item, item["status"]
        ))
    for item in counterevidence:
        staging.append(_staging_record(
            master, "hypothesis_counterevidence", item["counterevidence_id"], item, "COUNTEREVIDENCE_CANDIDATE"
        ))
    for item in error_modes:
        staging.append(_staging_record(
            master, "error_mode_candidate", item["error_mode_candidate_id"], item, item["error_mode_candidate"]
        ))
    for item in teaching_effects:
        staging.append(_staging_record(
            master, "teaching_intervention_effect", item["teaching_effect_candidate_id"], item, item["effect_status"]
        ))
    for item in next_probes:
        staging.append(_staging_record(
            master, "recommended_next_probe", item["recommended_probe_id"], item, item["status"]
        ))
    quality["candidate_staging_records"] = staging

    counts = quality.get("counts")
    if not isinstance(counts, dict):
        counts = {}
        quality["counts"] = counts
    counts.update({
        "learning_evidence_chains": len(chains),
        "skill_state_changes": len(skill_states),
        "hypotheses_requiring_confirmation": len(hypotheses),
        "hypothesis_counterevidence": len(counterevidence),
        "error_mode_candidates": len(error_modes),
        "teaching_intervention_effect_candidates": len(teaching_effects),
        "recommended_next_probes_v3": len(next_probes),
        "staging_records": len(staging),
    })

    incremental = quality.get("incremental_processing")
    if not isinstance(incremental, dict):
        incremental = {}
        quality["incremental_processing"] = incremental
    incremental["input_fingerprint"] = _json_digest({
        "v2_fingerprint": incremental.get("input_fingerprint"),
        "prior_learning_state": prior,
        "quality_method": QUALITY_METHOD_VERSION,
    })
    incremental["heavy_video_reprocessing_required"] = False
    incremental["semantic_only_rebuild_supported"] = True

    cost_gate = quality.get("cost_gate")
    if not isinstance(cost_gate, dict):
        cost_gate = {}
        quality["cost_gate"] = cost_gate
    cost_gate.update({
        "paid_ai_api_required": False,
        "paid_cloud_required": False,
        "heavy_video_reprocessing_for_this_layer": False,
        "reuses_existing_transcript_and_evidence": True,
    })
    return quality


__all__ = [
    "QUALITY_SCHEMA",
    "QUALITY_SCHEMA_VERSION",
    "QUALITY_METHOD_VERSION",
    "SKILL_STATES",
    "build_quality_layer",
    "build_learning_evidence_chains",
    "build_skill_state_changes",
    "build_hypotheses",
    "build_evidence_against_hypotheses",
    "build_error_mode_candidates",
    "build_teaching_intervention_effect_candidates",
    "build_recommended_next_probes",
]
