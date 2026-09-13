#!/usr/bin/env python3
"""Quality-first longitudinal extraction for School bridge lesson videos.

This module is deliberately independent from Drive, Neon, ASR and PDF code.  It
turns an evidence-linked ``master_analysis`` payload into a conservative v2
pedagogical layer.  It never activates School canon, curriculum or a production
student profile.

The central invariant is: fewer complete and auditable objects are preferable
to many weak candidates.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping, Sequence

QUALITY_SCHEMA = "diana-longitudinal-quality"
QUALITY_SCHEMA_VERSION = 2
QUALITY_METHOD_VERSION = "diana-quality-v2.0"

ROLE_TEACHER = "teacher"
ROLE_STUDENT = "student"
ROLE_UNKNOWN = "unknown"

TECHNICAL_INTRO_PATTERNS = (
    "сегодня мы поговорим",
    "сегодня мы пройдем",
    "сегодня мы пройдём",
    "посмотрим как",
    "на следующем занятии",
    "звук",
    "интернет",
    "соединение",
    "меня выкинул",
    "дождь",
    "подключ",
)

TASK_CUES = (
    "?",
    "почему",
    "как ты думаешь",
    "как вы думаете",
    "что будешь",
    "что будем",
    "что делать",
    "сколько",
    "какую заявку",
    "что заявишь",
    "что заявите",
    "как сыграешь",
    "как сыграете",
    "посчитай",
    "посчитаем",
    "выбери",
    "заяви",
    "сыграй",
    "ходи",
)

DECLARATIVE_CUES = (
    "если ",
    " то ",
    "нужно",
    "надо",
    "можно",
    "нельзя",
    "означает",
    "называется",
    "правило",
    "при игре",
    "при торговле",
    "мы считаем",
    "мы начинаем",
    "должно быть",
    "должен быть",
    "должна быть",
    "показывает",
    "обещает",
    "форсирует",
)

BRIDGE_ANCHORS = {
    "торговля", "заявка", "открытие", "ответ", "ребид", "пас", "контра",
    "интервенция", "стейман", "трансфер", "инвит", "форсинг", "фит",
    "без козыря", "бк", "козырь", "контракт", "гейм", "шлем",
    "розыгрыш", "защита", "первый ход", "импас", "экспас", "взятка",
    "переход", "масть", "мажор", "минор", "расклад", "стол",
    "туз", "король", "дама", "валет", "десятка", "фоска",
}

ROLE_TEACHER_CUES = (
    "диана",
    "как ты думаешь",
    "почему ты",
    "давай посмотрим",
    "давай посчитаем",
    "обрати внимание",
    "запомни",
    "правильно",
    "неправильно",
    "тебе нужно",
    "твоя задача",
)

ROLE_STUDENT_CUES = (
    "я не знаю",
    "я не помню",
    "я думаю",
    "я вижу",
    "мне кажется",
    "я посчитала",
    "я посчитал",
    "я поняла",
    "я понял",
    "я пропустила",
    "я забыла",
)

INTERVENTION_CUES = (
    "почему",
    "обрати внимание",
    "давай ещё раз",
    "давай еще раз",
    "смотри",
    "правильно",
    "неправильно",
    "нужно",
    "надо",
    "запомни",
    "это называется",
)

FOLLOWUP_CUES = (
    "поняла",
    "понял",
    "тогда",
    "значит",
    "получается",
    "теперь",
    "да",
)


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _low(value: object) -> str:
    return _norm(value).casefold()


def _words(value: object) -> list[str]:
    return re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", _low(value))


def _stable_id(kind: str, *parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    return f"{kind}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def _json_digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _evidence_refs(item: Mapping[str, Any] | None) -> list[str]:
    if not item:
        return []
    values: list[object] = []
    for key in ("evidence", "evidence_refs", "visual_evidence", "visual_evidence_refs"):
        candidate = item.get(key)
        if isinstance(candidate, list):
            values.extend(candidate)
    return list(dict.fromkeys(str(value) for value in values if value))


def _episode_map(master: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("episode_id")): dict(item)
        for item in (master.get("episodes") or [])
        if isinstance(item, Mapping) and item.get("episode_id")
    }


def _transcript_map(master: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("segment_id")): dict(item)
        for item in (master.get("transcript") or [])
        if isinstance(item, Mapping) and item.get("segment_id")
    }


def _contains_any(text: object, cues: Iterable[str]) -> bool:
    low = _low(text)
    return any(cue in low for cue in cues)


def _is_intro_or_technical(text: object) -> bool:
    low = _low(text)
    return any(pattern in low for pattern in TECHNICAL_INTRO_PATTERNS)


def _is_task(text: object) -> bool:
    value = _norm(text)
    if len(value) < 8 or _is_intro_or_technical(value):
        return False
    low = value.casefold()
    return any(cue in low for cue in TASK_CUES)


def _speaker_role_from_segment(segment: Mapping[str, Any]) -> tuple[str, float]:
    explicit = _low(segment.get("speaker_role") or segment.get("speaker_role_candidate"))
    if explicit in {ROLE_TEACHER, ROLE_STUDENT}:
        try:
            confidence = float(segment.get("speaker_role_confidence") or segment.get("speaker_confidence") or 0.8)
        except (TypeError, ValueError):
            confidence = 0.8
        return explicit, max(0.0, min(1.0, confidence))

    text = segment.get("text") or segment.get("analysis_text") or ""
    teacher = sum(cue in _low(text) for cue in ROLE_TEACHER_CUES)
    student = sum(cue in _low(text) for cue in ROLE_STUDENT_CUES)
    if teacher and not student:
        return ROLE_TEACHER, min(0.75, 0.45 + 0.1 * teacher)
    if student and not teacher:
        return ROLE_STUDENT, min(0.75, 0.45 + 0.1 * student)
    return ROLE_UNKNOWN, 0.0


def _episode_role(
    episode: Mapping[str, Any], transcript: Mapping[str, Mapping[str, Any]]
) -> tuple[str, float, list[str]]:
    votes: Counter[str] = Counter()
    confidences: defaultdict[str, list[float]] = defaultdict(list)
    refs = [str(ref) for ref in (episode.get("evidence") or []) if ref]
    for ref in refs:
        segment = transcript.get(ref)
        if not segment:
            continue
        role, confidence = _speaker_role_from_segment(segment)
        if role != ROLE_UNKNOWN:
            votes[role] += 1
            confidences[role].append(confidence)
    if not votes:
        role_hint = _low(episode.get("role_hint"))
        if role_hint in {ROLE_TEACHER, ROLE_STUDENT}:
            return role_hint, 0.35, refs
        return ROLE_UNKNOWN, 0.0, refs
    winner, count = votes.most_common(1)[0]
    total = sum(votes.values())
    runner = votes.most_common(2)[1][1] if len(votes) > 1 else 0
    if count == runner:
        return ROLE_UNKNOWN, 0.0, refs
    mean_conf = sum(confidences[winner]) / max(1, len(confidences[winner]))
    confidence = min(1.0, mean_conf * (count / max(1, total)))
    return winner, confidence, refs


def speaker_summary(master: Mapping[str, Any]) -> dict[str, Any]:
    transcript = [item for item in (master.get("transcript") or []) if isinstance(item, Mapping)]
    total = len(transcript)
    labels = 0
    role_counts: Counter[str] = Counter()
    cluster_counts: Counter[str] = Counter()
    role_conf: list[float] = []
    for segment in transcript:
        label = _norm(segment.get("speaker") or segment.get("speaker_cluster"))
        if label:
            labels += 1
            cluster_counts[label] += 1
        role, confidence = _speaker_role_from_segment(segment)
        if role != ROLE_UNKNOWN:
            role_counts[role] += 1
            role_conf.append(confidence)
    labeled_ratio = labels / total if total else 0.0
    role_ratio = sum(role_counts.values()) / total if total else 0.0
    lexical_roles_present = bool(role_counts.get(ROLE_TEACHER) and role_counts.get(ROLE_STUDENT))
    mapped = bool(lexical_roles_present and role_ratio >= 0.25 and labeled_ratio >= 0.25)
    return {
        "status": (
            "ROLE_MAPPED" if mapped
            else "DIARIZED_UNMAPPED" if labels
            else "UNAVAILABLE"
        ),
        "transcript_segments": total,
        "speaker_labeled_segments": labels,
        "speaker_labeled_ratio": round(labeled_ratio, 4),
        "role_labeled_segments": sum(role_counts.values()),
        "role_labeled_ratio": round(role_ratio, 4),
        "role_counts": dict(role_counts),
        "speaker_clusters": dict(cluster_counts),
        "mean_role_confidence": round(sum(role_conf) / len(role_conf), 4) if role_conf else None,
        "roles_mapped": mapped,
        "warning": None if mapped else "Роли преподавателя и ученицы не доказаны для достаточной доли реплик.",
    }


def build_atomic_events(master: Mapping[str, Any]) -> list[dict[str, Any]]:
    transcript = _transcript_map(master)
    events: list[dict[str, Any]] = []
    for episode in master.get("episodes") or []:
        if not isinstance(episode, Mapping):
            continue
        role, role_confidence, refs = _episode_role(episode, transcript)
        text = _norm(episode.get("summary_text"))
        events.append({
            "event_id": str(episode.get("episode_id") or _stable_id("event", episode.get("start"), text)),
            "sequence_no": episode.get("ordinal"),
            "start": episode.get("start"),
            "end": episode.get("end"),
            "event_type": episode.get("type") or "unclassified",
            "topics": list(episode.get("terms") or []),
            "text": text,
            "speaker_role": role,
            "speaker_role_confidence": round(role_confidence, 4),
            "task_candidate": _is_task(text),
            "technical_or_intro": _is_intro_or_technical(text),
            "evidence_refs": refs,
            "visual_evidence_refs": list(episode.get("visual_evidence") or []),
            "source_episode_id": episode.get("episode_id"),
        })
    events.sort(key=lambda item: (float(item.get("start") or 0), float(item.get("end") or 0)))
    return events


def _dominant_domain(events: Sequence[Mapping[str, Any]]) -> str:
    counts = Counter(_norm(event.get("event_type")) or "unclassified" for event in events)
    return counts.most_common(1)[0][0] if counts else "unclassified"


def _top_topics(events: Sequence[Mapping[str, Any]], limit: int = 6) -> list[str]:
    counts = Counter(
        _norm(topic)
        for event in events
        for topic in (event.get("topics") or [])
        if _norm(topic)
    )
    return [topic for topic, _ in counts.most_common(limit)]


def build_sections(events: Sequence[Mapping[str, Any]], job_id: str) -> list[dict[str, Any]]:
    """Group atomic events into medium-grained lesson sections.

    Boundaries are deterministic and conservative.  A section is split on a long
    gap, a sustained domain shift, or a maximum duration.  This is an index, not
    an assertion that the teacher planned the section exactly this way.
    """
    if not events:
        return []
    sections: list[list[Mapping[str, Any]]] = []
    current: list[Mapping[str, Any]] = []
    for event in events:
        if not current:
            current = [event]
            continue
        prev = current[-1]
        start = float(event.get("start") or 0)
        prev_end = float(prev.get("end") or prev.get("start") or 0)
        section_start = float(current[0].get("start") or 0)
        gap = start - prev_end
        duration = start - section_start
        # Long stretches without bridge cues are common while a deal is being
        # played.  Do not turn every such stretch or short domain change into a
        # new pedagogical section.  A section boundary therefore needs either a
        # substantial gap or a bounded maximum duration.
        if gap > 180 or duration > 720:
            sections.append(current)
            current = [event]
        else:
            current.append(event)
    if current:
        sections.append(current)

    out: list[dict[str, Any]] = []
    for index, group in enumerate(sections, 1):
        start = float(group[0].get("start") or 0)
        end = float(group[-1].get("end") or start)
        domain = _dominant_domain(group)
        topics = _top_topics(group)
        out.append({
            "section_id": _stable_id("section", job_id, index, round(start, 3), round(end, 3), domain),
            "sequence_no": index,
            "start": start,
            "end": end,
            "domain_candidate": domain,
            "topic_candidates": topics,
            "atomic_event_ids": [str(event.get("event_id")) for event in group],
            "status": "STRUCTURAL_CANDIDATE",
            "authority_note": "Секция восстановлена алгоритмом и не считается нормативным планом занятия.",
        })
    return out


def _section_for_time(sections: Sequence[Mapping[str, Any]], start: float) -> str | None:
    for section in sections:
        if float(section.get("start") or 0) <= start <= float(section.get("end") or 0) + 0.001:
            return str(section.get("section_id"))
    return None


def _complete_text(value: object, minimum: int = 12) -> bool:
    text = _norm(value)
    return len(text) >= minimum and len(_words(text)) >= 3


def _build_interaction_from_existing(
    item: Mapping[str, Any],
    event_by_episode: Mapping[str, Mapping[str, Any]],
    sections: Sequence[Mapping[str, Any]],
    job_id: str,
) -> dict[str, Any]:
    sequence = item.get("role_neutral_sequence") if isinstance(item.get("role_neutral_sequence"), Mapping) else {}
    focus_id = str(item.get("focus_episode_id") or "")
    focus = event_by_episode.get(focus_id, {})
    task = _norm(item.get("task_or_trigger") or sequence.get("trigger_context"))
    action = _norm(item.get("student_action") or sequence.get("observed_action"))
    intervention = _norm(item.get("teacher_intervention") or sequence.get("intervention"))
    response = _norm(item.get("student_response") or sequence.get("followup_response"))
    outcome = _norm(item.get("outcome") or sequence.get("observed_outcome"))
    evidence = list(dict.fromkeys(_evidence_refs(item) + list(focus.get("evidence_refs") or [])))
    role_status = _low(item.get("attribution_status") or item.get("actor_attribution_status"))
    actor_attributed = (
        "unavailable" not in role_status
        and "unknown" not in role_status
        and bool(role_status)
    ) or (
        focus.get("speaker_role") in {ROLE_TEACHER, ROLE_STUDENT}
        and float(focus.get("speaker_role_confidence") or 0) >= 0.55
    )
    reasons: list[str] = []
    if not _is_task(task):
        reasons.append("TASK_NOT_OBSERVED_OR_IS_INTRO")
    if not _complete_text(action):
        reasons.append("STUDENT_ACTION_MISSING")
    if not _complete_text(intervention):
        reasons.append("TEACHER_INTERVENTION_MISSING")
    if not _complete_text(response):
        reasons.append("FOLLOWUP_RESPONSE_MISSING")
    if not _complete_text(outcome):
        reasons.append("OUTCOME_MISSING")
    if not evidence:
        reasons.append("EVIDENCE_MISSING")
    if not actor_attributed:
        reasons.append("ACTOR_ATTRIBUTION_UNPROVEN")
    start = float(focus.get("start") or 0)
    status = "COMPLETE_EVIDENCE_CANDIDATE" if not reasons else "STAGING_PARTIAL"
    return {
        "interaction_id": str(item.get("cycle_id") or _stable_id("interaction", job_id, focus_id, task)),
        "status": status,
        "source": "existing_learning_cycle",
        "section_id": _section_for_time(sections, start),
        "focus_episode_id": focus_id or None,
        "start": focus.get("start"),
        "end": focus.get("end"),
        "task": task or None,
        "student_action": action or None,
        "teacher_intervention": intervention or None,
        "intervention_type": item.get("intervention_type"),
        "student_followup": response or None,
        "observed_outcome": outcome or None,
        "help_state": item.get("autonomy"),
        "transfer_status": "NOT_CONFIRMED",
        "actor_attribution_status": "SUPPORTED" if actor_attributed else "UNPROVEN",
        "evidence_refs": evidence,
        "visual_evidence_refs": list(item.get("visual_evidence") or []),
        "rejection_reasons": reasons,
        "profile_write_allowed": False,
        "methodology_activation_allowed": False,
    }


def _derive_role_interactions(
    events: Sequence[Mapping[str, Any]],
    sections: Sequence[Mapping[str, Any]],
    job_id: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    used: set[str] = set()
    for index, task_event in enumerate(events):
        if task_event.get("speaker_role") != ROLE_TEACHER:
            continue
        if float(task_event.get("speaker_role_confidence") or 0) < 0.5:
            continue
        if not task_event.get("task_candidate"):
            continue
        student_event = None
        teacher_event = None
        followup_event = None
        task_end = float(task_event.get("end") or task_event.get("start") or 0)
        for candidate in events[index + 1:index + 8]:
            if float(candidate.get("start") or 0) - task_end > 90:
                break
            if candidate.get("speaker_role") == ROLE_STUDENT and student_event is None:
                student_event = candidate
                continue
            if student_event is not None and candidate.get("speaker_role") == ROLE_TEACHER:
                if _contains_any(candidate.get("text"), INTERVENTION_CUES):
                    teacher_event = candidate
                    continue
            if teacher_event is not None and candidate.get("speaker_role") == ROLE_STUDENT:
                followup_event = candidate
                break
        if student_event is None:
            continue
        event_ids = [str(task_event.get("event_id")), str(student_event.get("event_id"))]
        if teacher_event:
            event_ids.append(str(teacher_event.get("event_id")))
        if followup_event:
            event_ids.append(str(followup_event.get("event_id")))
        key = "|".join(event_ids)
        if key in used:
            continue
        used.add(key)
        evidence: list[str] = []
        for event in (task_event, student_event, teacher_event, followup_event):
            if event:
                evidence.extend(event.get("evidence_refs") or [])
        outcome = None
        if followup_event:
            outcome = (
                "Наблюдается содержательный ответ после вмешательства."
                if _contains_any(followup_event.get("text"), FOLLOWUP_CUES)
                else "Наблюдается последующая реакция; правильность требует предметной проверки."
            )
        reasons: list[str] = []
        if teacher_event is None:
            reasons.append("TEACHER_INTERVENTION_MISSING")
        if followup_event is None:
            reasons.append("FOLLOWUP_RESPONSE_MISSING")
        if not evidence:
            reasons.append("EVIDENCE_MISSING")
        complete = not reasons
        out.append({
            "interaction_id": _stable_id("interaction", job_id, key),
            "status": "COMPLETE_EVIDENCE_CANDIDATE" if complete else "STAGING_PARTIAL",
            "source": "role_sequence_v2",
            "section_id": _section_for_time(sections, float(task_event.get("start") or 0)),
            "focus_episode_id": task_event.get("source_episode_id"),
            "start": task_event.get("start"),
            "end": (followup_event or teacher_event or student_event).get("end"),
            "task": task_event.get("text"),
            "student_action": student_event.get("text"),
            "teacher_intervention": teacher_event.get("text") if teacher_event else None,
            "intervention_type": "observed_teacher_turn" if teacher_event else None,
            "student_followup": followup_event.get("text") if followup_event else None,
            "observed_outcome": outcome,
            "help_state": "after_observed_intervention" if teacher_event else "not_determined",
            "transfer_status": "NOT_CONFIRMED",
            "actor_attribution_status": "SUPPORTED",
            "event_ids": event_ids,
            "evidence_refs": list(dict.fromkeys(evidence)),
            "visual_evidence_refs": list(dict.fromkeys(
                ref
                for event in (task_event, student_event, teacher_event, followup_event)
                if event
                for ref in (event.get("visual_evidence_refs") or [])
            )),
            "rejection_reasons": reasons,
            "profile_write_allowed": False,
            "methodology_activation_allowed": False,
        })
    return out


def build_learning_interactions(
    master: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    sections: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    job_id = str(master.get("job_id") or "unknown")
    event_by_episode = {
        str(event.get("source_episode_id")): event
        for event in events
        if event.get("source_episode_id")
    }
    candidates = [
        _build_interaction_from_existing(item, event_by_episode, sections, job_id)
        for item in (master.get("learning_interactions") or [])
        if isinstance(item, Mapping)
    ]
    candidates.extend(_derive_role_interactions(events, sections, job_id))

    # Dedupe on the observable task/action/evidence rather than worker IDs.
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in sorted(candidates, key=lambda value: (float(value.get("start") or 0), value.get("source") or "")):
        key = _json_digest({
            "task": _low(item.get("task")),
            "action": _low(item.get("student_action")),
            "evidence": item.get("evidence_refs") or [],
        })
        if key in seen:
            continue
        seen.add(key)
        item["dedupe_key"] = key
        out.append(item)
    return out


def methodology_readiness(
    master: Mapping[str, Any], interactions: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    content_quality = master.get("content_quality") if isinstance(master.get("content_quality"), Mapping) else {}
    technical = master.get("technical_qc") if isinstance(master.get("technical_qc"), Mapping) else {}
    transcript_qc = technical.get("transcript") if isinstance(technical.get("transcript"), Mapping) else {}
    visual_qc = technical.get("visual") if isinstance(technical.get("visual"), Mapping) else {}
    speaker = speaker_summary(master)

    technical_issues: list[str] = []
    if not (master.get("transcript") or []):
        technical_issues.append("TRANSCRIPT_MISSING")
    if int(content_quality.get("semantic_critical_unresolved") or 0) > 0:
        technical_issues.append("CRITICAL_SEMANTIC_CANDIDATES_UNRESOLVED")
    semantic_status = _low(content_quality.get("semantic_qc_status"))
    if semantic_status and semantic_status not in {"pass", "passed"}:
        technical_issues.append("SEMANTIC_QC_NOT_PASS")
    pass1 = _low((visual_qc.get("pass1") or {}).get("status") if isinstance(visual_qc.get("pass1"), Mapping) else visual_qc.get("pass1"))
    pass2 = _low((visual_qc.get("pass2") or {}).get("status") if isinstance(visual_qc.get("pass2"), Mapping) else visual_qc.get("pass2"))
    if pass1 and "complete" not in pass1:
        technical_issues.append("VISUAL_PASS_1_INCOMPLETE")
    if pass2 and "complete" not in pass2:
        technical_issues.append("VISUAL_PASS_2_INCOMPLETE")
    technical_ready = not technical_issues

    episodes = master.get("episodes") or []
    evidence_count = sum(bool(_evidence_refs(episode)) for episode in episodes if isinstance(episode, Mapping))
    content_issues: list[str] = []
    if not episodes:
        content_issues.append("SEMANTIC_EPISODES_MISSING")
    if not evidence_count:
        content_issues.append("EPISODE_EVIDENCE_MISSING")
    content_extracted = technical_ready and not content_issues

    complete = [item for item in interactions if item.get("status") == "COMPLETE_EVIDENCE_CANDIDATE"]
    methodology_issues: list[str] = []
    if not speaker.get("roles_mapped"):
        methodology_issues.append("TEACHER_STUDENT_ROLES_NOT_RELIABLY_MAPPED")
    if not complete:
        methodology_issues.append("NO_COMPLETE_LEARNING_INTERACTION")
    if any(not _complete_text(item.get("teacher_intervention")) for item in complete):
        methodology_issues.append("COMPLETE_INTERACTION_WITHOUT_TEACHER_INTERVENTION")
    if any(not _complete_text(item.get("student_followup")) for item in complete):
        methodology_issues.append("COMPLETE_INTERACTION_WITHOUT_FOLLOWUP")
    methodology_ready = content_extracted and not methodology_issues

    if methodology_ready:
        status = "METHODOLOGY_READY"
    elif content_extracted:
        status = "METHODOLOGY_PARTIAL"
    else:
        status = "METHODOLOGY_NOT_READY"
    return {
        "status": status,
        "technical_status": "TECHNICAL_READY" if technical_ready else "TECHNICAL_NOT_READY",
        "content_status": "CONTENT_EXTRACTED" if content_extracted else "CONTENT_NOT_EXTRACTED",
        "methodology_status": status,
        "technical_issues": technical_issues,
        "content_issues": content_issues,
        "methodology_issues": list(dict.fromkeys(methodology_issues)),
        "complete_learning_interactions": len(complete),
        "partial_learning_interactions": sum(item.get("status") != "COMPLETE_EVIDENCE_CANDIDATE" for item in interactions),
        "speaker_summary": speaker,
        "promotion_allowed": False,
        "note": "Готовность методики не активирует канон или обязательный способ преподавания.",
    }


def _bridge_terms(text: object) -> set[str]:
    low = _low(text)
    out = set()
    for anchor in BRIDGE_ANCHORS:
        if anchor in low:
            out.add(anchor)
    return out


def _numeric_anchors(text: object) -> set[str]:
    return set(re.findall(r"(?<!\w)\d{1,2}(?:[–\-]\d{1,2})?(?!\w)", _low(text)))


def classify_canon_links(master: Mapping[str, Any]) -> list[dict[str, Any]]:
    episodes = _episode_map(master)
    out: list[dict[str, Any]] = []
    for index, link in enumerate(master.get("canon_links") or [], 1):
        if not isinstance(link, Mapping):
            continue
        episode_id = str(link.get("episode_id") or "")
        episode = episodes.get(episode_id, {})
        observed = _norm(episode.get("summary_text"))
        canonical = _norm(link.get("canonical_excerpt"))
        try:
            score = float(link.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        observed_terms = _bridge_terms(observed)
        canonical_terms = _bridge_terms(canonical)
        shared_terms = observed_terms & canonical_terms
        observed_numbers = _numeric_anchors(observed)
        canonical_numbers = _numeric_anchors(canonical)
        numeric_overlap = bool(observed_numbers & canonical_numbers)
        numeric_conflict = bool(observed_numbers and canonical_numbers and observed_numbers.isdisjoint(canonical_numbers))

        if not canonical or score < 0.035:
            classification = "NO_CANON_MATCH"
        elif (
            numeric_conflict
            and score >= 0.18
            and len(shared_terms) >= 2
            and _contains_any(observed, ("неправильно", "вместо", "ошибка", "противореч"))
        ):
            classification = "CANON_CONFLICT_CANDIDATE"
        elif score >= 0.16 and (len(shared_terms) >= 2 or numeric_overlap):
            classification = (
                "RULE_PARAPHRASE_MATCH"
                if _contains_any(observed, DECLARATIVE_CUES)
                else "EXAMPLE_OF_RULE"
            )
        elif score >= 0.08 and shared_terms:
            classification = "CANON_RETRIEVAL_CANDIDATE"
        else:
            classification = "TOPIC_MENTION"

        counts = classification in {
            "RULE_PARAPHRASE_MATCH",
            "EXAMPLE_OF_RULE",
            "CANON_CONFLICT_CANDIDATE",
        }
        out.append({
            "canon_observation_id": _stable_id("canonv2", master.get("job_id"), episode_id, index),
            "classification": classification,
            "status": "CANDIDATE",
            "episode_id": episode_id or None,
            "observed_lesson_text": observed[:1600] or None,
            "candidate_canonical_excerpt": canonical[:1600] or None,
            "retrieval_score": round(score, 4),
            "shared_bridge_anchors": sorted(shared_terms),
            "observed_numeric_anchors": sorted(observed_numbers),
            "canonical_numeric_anchors": sorted(canonical_numbers),
            "counts_as_canon_evidence": counts,
            "activation_allowed": False,
            "evidence_refs": _evidence_refs(episode),
            "authority_note": "Видео и автоматическое совпадение не активируют канон школы.",
        })
    return out


def _extract_complete_claim(summary: object) -> str | None:
    text = _norm(summary)
    if not text or _is_intro_or_technical(text):
        return None
    # Prefer complete, declarative sentences over copying a whole noisy ASR
    # episode.  This deliberately loses recall to protect knowledge quality.
    sentences = [
        _norm(sentence)
        for sentence in re.split(r"(?<=[.!?])\s+|[.!?]+", text)
        if _norm(sentence)
    ]
    selected: list[str] = []
    for sentence in sentences:
        if len(sentence) < 45 or len(_words(sentence)) < 8:
            continue
        if _is_intro_or_technical(sentence):
            continue
        if not _bridge_terms(sentence):
            continue
        if not _contains_any(sentence, DECLARATIVE_CUES):
            continue
        selected.append(sentence)
        if len(selected) == 2:
            break
    claim = ". ".join(selected).strip()
    if not claim:
        return None
    if len(claim) > 700:
        claim = claim[:700].rsplit(" ", 1)[0]
    return claim


def _knowledge_rejection_reasons(episode: Mapping[str, Any], claim: str | None) -> list[str]:
    summary = _norm(episode.get("summary_text"))
    reasons: list[str] = []
    if len(summary) < 80 or len(_words(summary)) < 12:
        reasons.append("CONTENT_TOO_FRAGMENTARY")
    if _is_intro_or_technical(summary):
        reasons.append("PLANNING_OR_TECHNICAL_CHAT")
    if not claim:
        reasons.append("NO_COMPLETE_PROPOSITION")
    inferred_topics = set(_norm(topic) for topic in (episode.get("terms") or []) if _norm(topic))
    inferred_topics.update(_bridge_terms(claim or ""))
    if not inferred_topics:
        reasons.append("TOPIC_SCOPE_MISSING")
    if not _evidence_refs(episode):
        reasons.append("EVIDENCE_MISSING")
    if episode.get("unreliable"):
        reasons.append("SOURCE_MARKED_UNRELIABLE")
    return reasons


def knowledge_value_gate(master: Mapping[str, Any]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for episode in master.get("episodes") or []:
        if not isinstance(episode, Mapping):
            continue
        summary = _norm(episode.get("summary_text"))
        if not summary:
            continue
        claim = _extract_complete_claim(summary)
        topics = sorted({
            *(_norm(topic) for topic in (episode.get("terms") or []) if _norm(topic)),
            *_bridge_terms(claim or ""),
        })
        claim_key = _json_digest({"topics": topics, "claim": _low(claim or summary)})
        if claim_key in seen:
            continue
        seen.add(claim_key)
        reasons = _knowledge_rejection_reasons(episode, claim)
        status = "VALUE_GATE_PASSED_CANDIDATE" if not reasons else "STAGING_REJECTED"
        out.append({
            "knowledge_candidate_id": _stable_id("knowledgev2", master.get("job_id"), claim_key),
            "status": status,
            "promotion_allowed": False,
            "knowledge_type": episode.get("type") or "lesson_observation",
            "title_candidates": topics,
            "normalized_claim": claim,
            "source_episode_excerpt": summary[:1600],
            "scope": {
                "single_lesson": True,
                "historical": True,
                "topic_candidates": topics,
            },
            "episode_id": episode.get("episode_id"),
            "evidence_refs": _evidence_refs(episode),
            "visual_evidence_refs": list(episode.get("visual_evidence") or []),
            "confidence_class": episode.get("confidence"),
            "dedupe_key": claim_key,
            "rejection_reasons": reasons,
            "verification_required": True,
        })
    return out


def _asset_source_specs(master: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any], str]]:
    out: list[tuple[str, Mapping[str, Any], str]] = []
    for asset_type, key, id_field in (
        ("EXPLANATION_CANDIDATE", "best_explanations", "explanation_id"),
        ("TYPICAL_ERROR_CANDIDATE", "errors", "error_id"),
        ("POSITIVE_DECISION_CANDIDATE", "strengths", "strength_id"),
        ("TEACHER_INTERVENTION_CANDIDATE", "teacher_analysis", "observation_id"),
    ):
        for item in master.get(key) or []:
            if isinstance(item, Mapping):
                out.append((asset_type, item, id_field))
    return out


def reusable_asset_gate(
    master: Mapping[str, Any], interactions: Sequence[Mapping[str, Any]], limit: int = 7
) -> list[dict[str, Any]]:
    episodes = _episode_map(master)
    candidates: list[dict[str, Any]] = []
    for asset_type, item, id_field in _asset_source_specs(master):
        episode_id = str(item.get("episode_id") or "")
        episode = episodes.get(episode_id, {})
        excerpt = _norm(episode.get("summary_text"))
        topics = sorted({
            *(_norm(topic) for topic in ((item.get("topics") or []) + (episode.get("terms") or [])) if _norm(topic)),
            *_bridge_terms(excerpt),
        })
        evidence = list(dict.fromkeys(_evidence_refs(item) + _evidence_refs(episode)))
        reasons: list[str] = []
        if not topics:
            reasons.append("TOPIC_MISSING")
        if len(excerpt) < 100:
            reasons.append("CONTEXT_INCOMPLETE")
        if not evidence:
            reasons.append("EVIDENCE_MISSING")
        if _is_intro_or_technical(excerpt):
            reasons.append("NOT_REUSABLE_CONTENT")
        use_case = {
            "EXPLANATION_CANDIDATE": "candidate_explanation_library",
            "TYPICAL_ERROR_CANDIDATE": "candidate_error_library",
            "POSITIVE_DECISION_CANDIDATE": "candidate_positive_example",
            "TEACHER_INTERVENTION_CANDIDATE": "candidate_teacher_question_or_intervention",
        }[asset_type]
        score = 0
        score += min(3, len(evidence))
        score += 2 if topics else 0
        score += 2 if len(excerpt) >= 180 else 0
        score += 1 if episode.get("visual_evidence") else 0
        score += 1 if _contains_any(excerpt, DECLARATIVE_CUES + TASK_CUES) else 0
        candidates.append({
            "asset_id": str(item.get(id_field) or _stable_id("assetv2", asset_type, episode_id, excerpt)),
            "asset_type": asset_type,
            "status": "PASSED_VALUE_GATE" if not reasons else "STAGING_REJECTED",
            "active_library_candidate": False,
            "episode_id": episode_id or None,
            "topic_candidates": topics,
            "target_level": "UNKNOWN_UNTIL_CURRICULUM_MAPPING",
            "complete_context": excerpt[:2200] or None,
            "intended_use": use_case,
            "value_reason": item.get("candidate_reason") or item.get("note") or item.get("description"),
            "source_payload": dict(item),
            "evidence_refs": evidence,
            "visual_evidence_refs": list(episode.get("visual_evidence") or []),
            "quality_score": score,
            "rejection_reasons": reasons,
            "reuse_authority": "NON_CANONICAL_CANDIDATE",
        })

    # Complete interactions are also candidates for diagnostic/teaching cards.
    for interaction in interactions:
        if interaction.get("status") != "COMPLETE_EVIDENCE_CANDIDATE":
            continue
        topics: list[str] = []
        focus = episodes.get(str(interaction.get("focus_episode_id") or ""), {})
        topics = sorted({_norm(topic) for topic in (focus.get("terms") or []) if _norm(topic)})
        candidates.append({
            "asset_id": _stable_id("assetv2", "LEARNING_INTERACTION", interaction.get("interaction_id")),
            "asset_type": "LEARNING_INTERACTION_CARD_CANDIDATE",
            "status": "PASSED_VALUE_GATE",
            "active_library_candidate": False,
            "episode_id": interaction.get("focus_episode_id"),
            "topic_candidates": topics,
            "target_level": "HISTORICAL_LESSON_STAGE",
            "complete_context": interaction.get("task"),
            "intended_use": "diagnostic_or_teaching_card_candidate",
            "value_reason": "Полный наблюдаемый цикл задача → действие → помощь → реакция.",
            "source_payload": dict(interaction),
            "evidence_refs": list(interaction.get("evidence_refs") or []),
            "visual_evidence_refs": list(interaction.get("visual_evidence_refs") or []),
            "quality_score": 10,
            "rejection_reasons": [],
            "reuse_authority": "NON_CANONICAL_CANDIDATE",
        })

    passed = sorted(
        (item for item in candidates if item.get("status") == "PASSED_VALUE_GATE"),
        key=lambda item: (-int(item.get("quality_score") or 0), str(item.get("asset_id"))),
    )
    active_ids = {str(item.get("asset_id")) for item in passed[: max(0, min(10, limit))]}
    for item in candidates:
        item["active_library_candidate"] = str(item.get("asset_id")) in active_ids
        if item.get("status") == "PASSED_VALUE_GATE" and not item["active_library_candidate"]:
            item["status"] = "STAGING_PASSED_NOT_SELECTED"
    return candidates


_CARD_RE = re.compile(r"(?<![A-Z0-9])([AKQJT2-9]|10)([SHDC♠♥♦♣])(?![A-Z0-9])", re.I)


def _normalize_hand_cards(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        raw = " ".join(str(part) for part in value)
    else:
        raw = str(value)
    raw = raw.upper().replace("10", "T")
    suit_map = {"♠": "S", "♥": "H", "♦": "D", "♣": "C"}
    cards = set()
    for rank, suit in _CARD_RE.findall(raw):
        rank = rank.upper().replace("10", "T")
        suit = suit_map.get(suit, suit.upper())
        cards.add(rank + suit)
    return cards


def deal_reconstruction_gate(master: Mapping[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, deal in enumerate(master.get("deals") or [], 1):
        if not isinstance(deal, Mapping):
            continue
        hands = deal.get("hands") if isinstance(deal.get("hands"), Mapping) else {}
        cards_by_seat = {seat: _normalize_hand_cards(hands.get(seat)) for seat in ("N", "E", "S", "W")}
        all_cards = set().union(*cards_by_seat.values()) if cards_by_seat else set()
        duplicate_count = sum(len(cards) for cards in cards_by_seat.values()) - len(all_cards)
        full = all(len(cards_by_seat[seat]) == 13 for seat in cards_by_seat) and len(all_cards) == 52 and duplicate_count == 0
        partial = bool(all_cards) and not full
        board_status = "VERIFIED_FULL_BOARD" if full else "PARTIAL_BOARD" if partial else "BOARD_UNKNOWN"
        action_sequence = deal.get("play_sequence") or deal.get("actions") or deal.get("card_sequence")
        if isinstance(action_sequence, list) and action_sequence:
            action_status = "VERIFIED_ACTION_SEQUENCE" if all(isinstance(item, Mapping) and item.get("card") for item in action_sequence) else "PARTIAL_ACTION_SEQUENCE"
        else:
            action_status = "ACTION_SEQUENCE_UNKNOWN"
        dds_eligible = bool(
            full
            and deal.get("contract")
            and deal.get("declarer")
            and deal.get("opening_lead")
        )
        out.append({
            "deal_candidate_id": str(deal.get("deal_id") or _stable_id("dealv2", master.get("job_id"), index)),
            "episode_id": deal.get("episode_id"),
            "board_status": board_status,
            "action_sequence_status": action_status,
            "cards_per_seat": {seat: len(cards) for seat, cards in cards_by_seat.items()},
            "unique_card_count": len(all_cards),
            "duplicate_card_count": duplicate_count,
            "contract": deal.get("contract"),
            "declarer": deal.get("declarer"),
            "opening_lead": deal.get("opening_lead"),
            "dds_eligible": dds_eligible,
            "dds_status": "PENDING_ELIGIBLE" if dds_eligible else "NOT_ELIGIBLE_INSUFFICIENT_VERIFIED_DATA",
            "evidence_refs": _evidence_refs(deal),
            "authority_note": "Кандидат раздачи не считается восстановленной полной раздачей без 52 уникальных карт.",
        })
    return out


def pending_learning_probes(
    master: Mapping[str, Any],
    interactions: Sequence[Mapping[str, Any]],
    knowledge: Sequence[Mapping[str, Any]],
    limit: int = 10,
) -> list[dict[str, Any]]:
    topics: Counter[str] = Counter()
    episodes = _episode_map(master)
    for item in interactions:
        focus = episodes.get(str(item.get("focus_episode_id") or ""), {})
        for topic in focus.get("terms") or []:
            if _norm(topic):
                topics[_norm(topic)] += 3 if item.get("status") == "COMPLETE_EVIDENCE_CANDIDATE" else 1
    for item in knowledge:
        if item.get("status") != "VALUE_GATE_PASSED_CANDIDATE":
            continue
        for topic in (item.get("title_candidates") or []):
            if _norm(topic):
                topics[_norm(topic)] += 1
    out = []
    for topic, _ in topics.most_common(limit):
        out.append({
            "probe_id": _stable_id("probe", master.get("job_id"), topic),
            "topic_candidate": topic,
            "current_evidence_status": "SINGLE_LESSON_OBSERVATION",
            "retention_status": "NOT_CHECKED",
            "generalization_status": "NOT_CHECKED",
            "transfer_status": "NOT_CHECKED",
            "future_probe": "Проверить на новой подходящей ситуации без предварительного напоминания; точное содержание берётся только из проверенного канона школы.",
            "success_definition": "Наблюдаемое самостоятельное решение с зафиксированным Evidence; порог не задаётся автоматически.",
            "status": "PENDING",
        })
    return out


def _format_time(seconds: object) -> str:
    try:
        value = max(0, int(float(seconds or 0)))
    except (TypeError, ValueError):
        value = 0
    return f"{value // 3600:02d}:{(value % 3600) // 60:02d}:{value % 60:02d}"


def build_learning_cards(
    interactions: Sequence[Mapping[str, Any]],
    assets: Sequence[Mapping[str, Any]],
    minimum: int = 3,
    maximum: int = 7,
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for item in interactions:
        if item.get("status") != "COMPLETE_EVIDENCE_CANDIDATE":
            continue
        cards.append({
            "card_id": _stable_id("card", item.get("interaction_id")),
            "card_type": "OBSERVED_LEARNING_INTERACTION",
            "time_range": f"{_format_time(item.get('start'))}–{_format_time(item.get('end'))}",
            "topic_candidates": [],
            "task": item.get("task"),
            "student_action": item.get("student_action"),
            "teacher_intervention": item.get("teacher_intervention"),
            "observed_result": item.get("observed_outcome"),
            "next_probe": "Новая подходящая ситуация без предварительного напоминания.",
            "evidence_refs": list(item.get("evidence_refs") or []),
            "status": "CANDIDATE_CARD",
        })
        if len(cards) >= maximum:
            return cards
    for asset in assets:
        if not asset.get("active_library_candidate"):
            continue
        cards.append({
            "card_id": _stable_id("card", asset.get("asset_id")),
            "card_type": asset.get("asset_type"),
            "time_range": None,
            "topic_candidates": list(asset.get("topic_candidates") or []),
            "task": asset.get("complete_context"),
            "student_action": None,
            "teacher_intervention": None,
            "observed_result": asset.get("value_reason"),
            "next_probe": None,
            "evidence_refs": list(asset.get("evidence_refs") or []),
            "status": "CANDIDATE_CARD_WITHOUT_STUDENT_ATTRIBUTION",
        })
        if len(cards) >= maximum:
            break
    # Do not manufacture cards merely to satisfy the requested range.
    return cards


def teacher_brief(
    master: Mapping[str, Any],
    readiness: Mapping[str, Any],
    interactions: Sequence[Mapping[str, Any]],
    assets: Sequence[Mapping[str, Any]],
    probes: Sequence[Mapping[str, Any]],
    lesson_identity: Mapping[str, Any] | None,
) -> dict[str, Any]:
    session = master.get("session_summary") if isinstance(master.get("session_summary"), Mapping) else {}
    topics = [str(topic) for topic in (session.get("topics") or [])[:12]]
    complete = [item for item in interactions if item.get("status") == "COMPLETE_EVIDENCE_CANDIDATE"]
    active_assets = [item for item in assets if item.get("active_library_candidate")]
    limitations = list(readiness.get("methodology_issues") or [])
    return {
        "title": f"Краткий отчёт преподавателю — занятие {((lesson_identity or {}).get('lesson_number') or '?')}",
        "lesson_date": (lesson_identity or {}).get("lesson_date"),
        "lesson_date_status": (lesson_identity or {}).get("lesson_date_status"),
        "methodology_status": readiness.get("methodology_status"),
        "topic_candidates": topics,
        "verified_learning_interaction_count": len(complete),
        "student_conclusions": (
            [
                {
                    "task": item.get("task"),
                    "action": item.get("student_action"),
                    "help": item.get("teacher_intervention"),
                    "result": item.get("observed_outcome"),
                    "evidence_refs": item.get("evidence_refs") or [],
                }
                for item in complete[:5]
            ]
            if complete
            else []
        ),
        "reusable_asset_candidates": [
            {
                "asset_type": item.get("asset_type"),
                "topics": item.get("topic_candidates") or [],
                "value_reason": item.get("value_reason"),
                "evidence_refs": item.get("evidence_refs") or [],
            }
            for item in active_assets[:7]
        ],
        "pending_probes": list(probes[:5]),
        "limitations": limitations,
        "teacher_message": (
            "Надёжных персональных выводов по Диане пока недостаточно; используйте отчёт как индекс содержания и очередь будущих проверок."
            if not complete
            else "Ни один вывод не повышает канон или профиль ученицы без отдельного Evidence Gate."
        ),
    }


def candidate_staging_records(
    master: Mapping[str, Any],
    canon: Sequence[Mapping[str, Any]],
    knowledge: Sequence[Mapping[str, Any]],
    assets: Sequence[Mapping[str, Any]],
    interactions: Sequence[Mapping[str, Any]],
    deals: Sequence[Mapping[str, Any]],
    probes: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    job_id = str(master.get("job_id") or "unknown")
    records: list[dict[str, Any]] = []

    def add(candidate_type: str, stable_key: object, payload: Mapping[str, Any], status: str, reasons: Sequence[str] = ()) -> None:
        records.append({
            "candidate_id": _stable_id("candidate", job_id, candidate_type, stable_key),
            "candidate_type": candidate_type,
            "stable_key": str(stable_key),
            "quality_status": status,
            "promotion_status": "STAGING_ONLY",
            "promotion_allowed": False,
            "reasons": list(reasons),
            "evidence_refs": list(payload.get("evidence_refs") or []),
            "payload": dict(payload),
            "method_version": QUALITY_METHOD_VERSION,
        })

    for item in canon:
        add("canon_observation", item.get("canon_observation_id"), item, item.get("classification") or "UNKNOWN")
    for item in knowledge:
        add("knowledge_candidate", item.get("knowledge_candidate_id"), item, item.get("status") or "UNKNOWN", item.get("rejection_reasons") or [])
    for item in assets:
        add("reusable_asset", item.get("asset_id"), item, item.get("status") or "UNKNOWN", item.get("rejection_reasons") or [])
    for item in interactions:
        add("learning_interaction", item.get("interaction_id"), item, item.get("status") or "UNKNOWN", item.get("rejection_reasons") or [])
    for item in deals:
        add("deal_reconstruction", item.get("deal_candidate_id"), item, item.get("board_status") or "UNKNOWN")
    for item in probes:
        add("pending_learning_probe", item.get("probe_id"), item, item.get("status") or "UNKNOWN")
    return records


def build_quality_layer(
    master: Mapping[str, Any],
    lesson_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    job_id = str(master.get("job_id") or "unknown")
    events = build_atomic_events(master)
    sections = build_sections(events, job_id)
    interactions = build_learning_interactions(master, events, sections)
    readiness = methodology_readiness(master, interactions)
    canon = classify_canon_links(master)
    knowledge = knowledge_value_gate(master)
    assets = reusable_asset_gate(master, interactions)
    deals = deal_reconstruction_gate(master)
    probes = pending_learning_probes(master, interactions, knowledge)
    cards = build_learning_cards(interactions, assets)
    brief = teacher_brief(master, readiness, interactions, assets, probes, lesson_identity)
    staging = candidate_staging_records(master, canon, knowledge, assets, interactions, deals, probes)

    input_fingerprint = _json_digest({
        "job_id": job_id,
        "algorithm_revision": master.get("algorithmRevision"),
        "transcript_segments": [
            {
                "id": segment.get("segment_id"),
                "speaker": segment.get("speaker"),
                "speaker_role": segment.get("speaker_role") or segment.get("speaker_role_candidate"),
                "text": segment.get("text"),
            }
            for segment in (master.get("transcript") or [])
            if isinstance(segment, Mapping)
        ],
        "quality_method": QUALITY_METHOD_VERSION,
    })
    return {
        "schema": QUALITY_SCHEMA,
        "schema_version": QUALITY_SCHEMA_VERSION,
        "method_version": QUALITY_METHOD_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "job_id": job_id,
        "source_algorithm_revision": master.get("algorithmRevision"),
        "incremental_processing": {
            "input_fingerprint": input_fingerprint,
            "semantic_only_rebuild_supported": True,
            "heavy_video_reprocessing_required": False,
        },
        "readiness": readiness,
        "hierarchy": {
            "sections": sections,
            "teaching_tasks": [item for item in interactions if item.get("status") == "COMPLETE_EVIDENCE_CANDIDATE"],
            "atomic_events": events,
        },
        "learning_interactions": interactions,
        "canon_candidates": canon,
        "knowledge_candidates": knowledge,
        "reusable_assets": assets,
        "deal_reconstructions": deals,
        "pending_learning_probes": probes,
        "teacher_brief": brief,
        "learning_cards": cards,
        "candidate_staging_records": staging,
        "counts": {
            "sections": len(sections),
            "atomic_events": len(events),
            "complete_learning_interactions": sum(item.get("status") == "COMPLETE_EVIDENCE_CANDIDATE" for item in interactions),
            "partial_learning_interactions": sum(item.get("status") != "COMPLETE_EVIDENCE_CANDIDATE" for item in interactions),
            "strong_canon_evidence_candidates": sum(item.get("counts_as_canon_evidence") for item in canon),
            "canon_retrieval_or_topic_only": sum(not item.get("counts_as_canon_evidence") for item in canon),
            "promotable_knowledge_candidates": sum(item.get("status") == "VALUE_GATE_PASSED_CANDIDATE" for item in knowledge),
            "rejected_knowledge_fragments": sum(item.get("status") == "STAGING_REJECTED" for item in knowledge),
            "active_reusable_asset_candidates": sum(item.get("active_library_candidate") for item in assets),
            "all_reusable_asset_candidates": len(assets),
            "verified_full_boards": sum(item.get("board_status") == "VERIFIED_FULL_BOARD" for item in deals),
            "partial_boards": sum(item.get("board_status") == "PARTIAL_BOARD" for item in deals),
            "unknown_boards": sum(item.get("board_status") == "BOARD_UNKNOWN" for item in deals),
            "pending_learning_probes": len(probes),
            "learning_cards": len(cards),
            "staging_records": len(staging),
        },
        "authority": {
            "canon_activation": "DENY",
            "curriculum_activation": "DENY",
            "student_profile_production_write": "DENY",
            "methodology_activation": "DENY",
            "database_destination": "STAGING_ONLY",
        },
        "cost_gate": {
            "paid_ai_api_required": False,
            "paid_cloud_required": False,
            "heavy_video_reprocessing_for_this_layer": False,
            "reuses_existing_transcript_and_evidence": True,
        },
    }


__all__ = [
    "QUALITY_SCHEMA",
    "QUALITY_SCHEMA_VERSION",
    "QUALITY_METHOD_VERSION",
    "build_quality_layer",
    "build_atomic_events",
    "build_sections",
    "build_learning_interactions",
    "methodology_readiness",
    "classify_canon_links",
    "knowledge_value_gate",
    "reusable_asset_gate",
    "deal_reconstruction_gate",
    "pending_learning_probes",
    "candidate_staging_records",
]
