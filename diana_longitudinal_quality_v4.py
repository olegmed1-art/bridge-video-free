#!/usr/bin/env python3
"""Evidence-linked decision-window refinement for bridge lesson video analysis.

Quality v4 keeps the v2/v3 authority, privacy and cost gates intact while
repairing three field defects exposed by a real lesson:

* complete Learning Interaction reconstruction must use observed transcript
  turns rather than isolated semantic episodes;
* acoustic speaker coverage and semantic role hints must be reported as
  different metrics;
* structured board fragments may be merged only under an exact explicit board
  identity, never by topic, board number, time proximity or narrative guess.

The layer is semantic-only.  It never mutates raw ASR, never names a person,
never activates School canon/methodology/profile writes, and never requires a
new media/ASR pass.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

import diana_longitudinal_quality_v2 as v2
import diana_longitudinal_quality_v3 as v3

QUALITY_SCHEMA = "diana-longitudinal-quality"
QUALITY_SCHEMA_VERSION = 4
QUALITY_METHOD_VERSION = "diana-quality-v4.0"

_ORIG_BUILD_INTERACTIONS = v2.build_learning_interactions
_ORIG_SPEAKER_SUMMARY = v2.speaker_summary
_ORIG_DEAL_GATE = v2.deal_reconstruction_gate

ROLE_TEACHER = "teacher"
ROLE_STUDENT = "student"

BRIDGE_CUES = (
    "торгов", "заяв", "откры", "ответ", "ребид", "пас", "контр",
    "стейман", "трансфер", "инвит", "форс", "фит", "без козыр", "бк",
    "козыр", "контракт", "гейм", "шлем", "розыг", "защит", "первый ход",
    "импас", "экспас", "взят", "переход", "масть", "мажор", "минор",
    "расклад", "стол", "рука", "карты", "туз", "корол", "дама",
    "валет", "десятк", "очки", "буб", "треф", "пик", "черв", "ход",
)
TASK_CUES = (
    "?", "почему", "как ты дума", "как вы дума", "что ты дума",
    "что будешь", "что будем", "что делать", "сколько", "какую",
    "какой", "какая", "что у тебя", "что у нас", "что заяв",
    "как сыг", "посчитай", "посчитаем", "выбери", "в чем", "в чём",
)
INTERVENTION_CUES = (
    "смотри", "давай", "обрати внимание", "правильно", "неправильно",
    "верно", "неверно", "нужно", "надо", "почему", "запомни",
    "это называется", "то есть", "лучше", "ошиб", "проверь", "посчитай",
    "не так", "абсолютно", "подумай", "объясни",
)
ORGANIZATIONAL_CUES = (
    "звук", "интернет", "соединение", "микрофон", "камера", "подключ",
    "видишь сейчас", "экран видно", "картинка", "зум", "zoom", "ватсап",
    "whatsapp", "частные занятия", "группа", "расписание", "папа",
    "мама", "оплата", "перерыв", "заканчиваем", "следующее занятие",
)
ACK_ONLY = {
    "да", "нет", "ага", "угу", "поняла", "понял", "хорошо", "ладно",
    "ок", "okay", "ясно", "точно",
}


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _low(value: object) -> str:
    return _norm(value).casefold()


def _words(value: object) -> list[str]:
    return re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", _low(value))


def _stable_id(kind: str, *parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    return f"{kind}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def _dedupe(values: Sequence[object]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value))


def _role_explicit(segment: Mapping[str, Any]) -> tuple[str | None, float]:
    role = _low(segment.get("speaker_role") or segment.get("speaker_role_candidate"))
    if role not in {ROLE_TEACHER, ROLE_STUDENT}:
        return None, 0.0
    raw = segment.get("speaker_role_confidence")
    if raw is None:
        raw = segment.get("speaker_confidence")
    try:
        confidence = float(raw if raw is not None else 0.8)
    except (TypeError, ValueError):
        confidence = 0.8
    return role, max(0.0, min(1.0, confidence))


def _acoustic_label(segment: Mapping[str, Any]) -> str:
    return _norm(segment.get("speaker") or segment.get("speaker_cluster"))


def speaker_summary_v4(master: Mapping[str, Any]) -> dict[str, Any]:
    """Report acoustic and role evidence without mixing lexical fallback into it."""
    transcript = [x for x in (master.get("transcript") or []) if isinstance(x, Mapping)]
    total = len(transcript)
    acoustic = 0
    explicit_role = 0
    acoustic_role = 0
    semantic_fallback = 0
    role_without_acoustic = 0
    cluster_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    role_confidence: list[float] = []

    for segment in transcript:
        label = _acoustic_label(segment)
        role, confidence = _role_explicit(segment)
        if label:
            acoustic += 1
            cluster_counts[label] += 1
        if role:
            explicit_role += 1
            if not label:
                role_without_acoustic += 1
            if label:
                acoustic_role += 1
                role_counts[role] += 1
                role_confidence.append(confidence)
        else:
            fallback_role, _ = v2._speaker_role_from_segment(segment)
            if fallback_role in {ROLE_TEACHER, ROLE_STUDENT}:
                semantic_fallback += 1

    acoustic_ratio = acoustic / total if total else 0.0
    acoustic_role_ratio = acoustic_role / total if total else 0.0
    mapped = bool(
        acoustic_ratio >= 0.25
        and acoustic_role_ratio >= 0.25
        and role_counts.get(ROLE_TEACHER)
        and role_counts.get(ROLE_STUDENT)
    )
    return {
        # Compatibility fields now have one precise meaning: explicit role on an
        # acoustically labeled segment.  Lexical role hints are diagnostics only.
        "status": "ROLE_MAPPED" if mapped else "DIARIZED_UNMAPPED" if acoustic else "UNAVAILABLE",
        "transcript_segments": total,
        "speaker_labeled_segments": acoustic,
        "speaker_labeled_ratio": round(acoustic_ratio, 4),
        "role_labeled_segments": acoustic_role,
        "role_labeled_ratio": round(acoustic_role_ratio, 4),
        "role_counts": dict(role_counts),
        "speaker_clusters": dict(cluster_counts),
        "mean_role_confidence": (
            round(sum(role_confidence) / len(role_confidence), 4)
            if role_confidence else None
        ),
        "roles_mapped": mapped,
        "warning": None if mapped else "Explicit acoustic teacher/student mapping is insufficient.",
        "metric_semantics_revision": "speaker-role-separation-v4",
        "acoustic_speaker_labeled_segments": acoustic,
        "explicit_role_labeled_segments": explicit_role,
        "acoustic_role_mapped_segments": acoustic_role,
        "semantic_fallback_role_segments": semantic_fallback,
        "role_without_acoustic_speaker_segments": role_without_acoustic,
        "semantic_fallback_counts_as_role_coverage": False,
    }


def _reliable(segment: Mapping[str, Any]) -> bool:
    if segment.get("unreliable") is True:
        return False
    status = _low(segment.get("speech_block_status") or segment.get("status"))
    return status not in {"unreliable", "speech_unreliable"}


def _transcript_turns(master: Mapping[str, Any]) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for segment in master.get("transcript") or []:
        if not isinstance(segment, Mapping) or not _reliable(segment):
            continue
        label = _acoustic_label(segment)
        role, confidence = _role_explicit(segment)
        text = _norm(segment.get("text") or segment.get("analysis_text"))
        if not label or role not in {ROLE_TEACHER, ROLE_STUDENT} or confidence < 0.60 or not text:
            continue
        try:
            start = float(segment.get("start") or 0)
            end = float(segment.get("end") or start)
        except (TypeError, ValueError):
            continue
        segment_id = str(segment.get("segment_id") or _stable_id("segment", start, end, text))
        if (
            turns
            and turns[-1]["speaker"] == label
            and turns[-1]["role"] == role
            and start - float(turns[-1]["end"]) <= 3.5
        ):
            turns[-1]["end"] = max(float(turns[-1]["end"]), end)
            turns[-1]["texts"].append(text)
            turns[-1]["segment_ids"].append(segment_id)
            turns[-1]["role_confidences"].append(confidence)
        else:
            turns.append({
                "turn_id": _stable_id("turn", segment_id, round(start, 3)),
                "speaker": label,
                "role": role,
                "start": start,
                "end": end,
                "texts": [text],
                "segment_ids": [segment_id],
                "role_confidences": [confidence],
            })
    for turn in turns:
        turn["text"] = " ".join(turn.pop("texts"))
        values = turn.pop("role_confidences")
        turn["role_confidence"] = round(sum(values) / len(values), 4)
    return turns


def _bridge_relevant(text: object) -> bool:
    low = _low(text)
    if any(cue in low for cue in BRIDGE_CUES):
        return True
    return bool(re.search(r"(?<!\w)[1-7]\s*(?:nt|бк|♣|♦|♥|♠|треф|буб|черв|пик)(?!\w)", low, re.I))


def _organizational(text: object) -> bool:
    low = _low(text)
    return any(cue in low for cue in ORGANIZATIONAL_CUES)


def _task_excerpts(text: object) -> list[str]:
    value = _norm(text)
    if not value:
        return []
    # Keep question/exercise clauses local.  Text after the task is deliberately
    # not appended, preventing a teacher's own explanation from leaking into the
    # student's pre-intervention state.
    chunks = [
        _norm(part)
        for part in re.split(r"(?<=[.!?])\s+|[;]\s+", value)
        if _norm(part)
    ]
    if not chunks:
        chunks = [value]
    out: list[str] = []
    for index, chunk in enumerate(chunks):
        low = _low(chunk)
        if not any(cue in low for cue in TASK_CUES):
            continue
        local = chunk
        if not _bridge_relevant(local) and index:
            prior = chunks[index - 1]
            if _bridge_relevant(prior):
                local = f"{prior} {chunk}"
        if _bridge_relevant(local) and not _organizational(local):
            out.append(local[:700])
    # ASR occasionally omits punctuation; inspect bounded cue neighborhoods.
    if not out:
        low = _low(value)
        for cue in TASK_CUES:
            if cue == "?":
                continue
            position = low.find(cue)
            if position < 0:
                continue
            left = max(0, position - 180)
            right = min(len(value), position + 260)
            local = _norm(value[left:right])
            if _bridge_relevant(local) and not _organizational(local):
                out.append(local)
                break
    return _dedupe(out)


def _meaningful_student_action(text: object) -> bool:
    value = _norm(text)
    if not value or _organizational(value):
        return False
    if _bridge_relevant(value):
        return True
    tokens = _words(value)
    return len(tokens) >= 4 and not (set(tokens) <= ACK_ONLY)


def _teacher_intervention(text: object) -> bool:
    value = _norm(text)
    if not value or _organizational(value):
        return False
    low = _low(value)
    return any(cue in low for cue in INTERVENTION_CUES)


def _substantive_followup(text: object) -> bool:
    value = _norm(text)
    if not value or _organizational(value):
        return False
    tokens = _words(value)
    if tokens and len(tokens) <= 3 and set(tokens) <= ACK_ONLY:
        return False
    return _bridge_relevant(value) or len(tokens) >= 4


def _nearest_focus_episode(events: Sequence[Mapping[str, Any]], start: float) -> str | None:
    contained = [
        event for event in events
        if float(event.get("start") or 0) - 0.5 <= start <= float(event.get("end") or 0) + 0.5
    ]
    if contained:
        return str(contained[0].get("source_episode_id") or contained[0].get("event_id") or "") or None
    near = sorted(
        events,
        key=lambda event: abs(float(event.get("start") or 0) - start),
    )
    if near and abs(float(near[0].get("start") or 0) - start) <= 20:
        return str(near[0].get("source_episode_id") or near[0].get("event_id") or "") or None
    return None


def _find_next(
    turns: Sequence[Mapping[str, Any]],
    start_index: int,
    role: str,
    predicate,
    origin_end: float,
    max_turns: int = 8,
    max_seconds: float = 120.0,
) -> tuple[int, Mapping[str, Any]] | None:
    for index in range(start_index + 1, min(len(turns), start_index + 1 + max_turns)):
        turn = turns[index]
        if float(turn.get("start") or 0) - origin_end > max_seconds:
            break
        if turn.get("role") == role and predicate(turn.get("text")):
            return index, turn
    return None


def _transcript_decision_interactions(
    master: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    sections: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    turns = _transcript_turns(master)
    candidates: list[dict[str, Any]] = []
    job_id = str(master.get("job_id") or "unknown")

    for task_index, task_turn in enumerate(turns):
        if task_turn.get("role") != ROLE_TEACHER:
            continue
        excerpts = _task_excerpts(task_turn.get("text"))
        if not excerpts:
            continue
        student_hit = _find_next(
            turns, task_index, ROLE_STUDENT, _meaningful_student_action,
            float(task_turn.get("end") or 0),
        )
        if not student_hit:
            continue
        student_index, student_turn = student_hit
        teacher_hit = _find_next(
            turns, student_index, ROLE_TEACHER, _teacher_intervention,
            float(student_turn.get("end") or 0),
        )
        if not teacher_hit:
            continue
        teacher_index, teacher_turn = teacher_hit
        followup_hit = _find_next(
            turns, teacher_index, ROLE_STUDENT, _substantive_followup,
            float(teacher_turn.get("end") or 0),
        )
        if not followup_hit:
            continue
        _, followup_turn = followup_hit

        evidence = _dedupe(
            list(task_turn.get("segment_ids") or [])
            + list(student_turn.get("segment_ids") or [])
            + list(teacher_turn.get("segment_ids") or [])
            + list(followup_turn.get("segment_ids") or [])
        )
        if len(evidence) < 4:
            continue
        task = excerpts[-1]  # closest/local task wins inside one teacher turn
        start = float(task_turn.get("start") or 0)
        end = float(followup_turn.get("end") or followup_turn.get("start") or start)
        focus_id = _nearest_focus_episode(events, start)
        section_id = v2._section_for_time(sections, start)
        candidates.append({
            "interaction_id": _stable_id(
                "interactionv4", job_id,
                task_turn.get("turn_id"), student_turn.get("turn_id"),
                teacher_turn.get("turn_id"), followup_turn.get("turn_id"),
            ),
            "status": "COMPLETE_EVIDENCE_CANDIDATE",
            "source": "transcript_decision_window_v4",
            "section_id": section_id,
            "focus_episode_id": focus_id,
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
                "student_turn_id": student_turn.get("turn_id"),
                "teacher_intervention_turn_id": teacher_turn.get("turn_id"),
                "student_followup_turn_id": followup_turn.get("turn_id"),
                "task_to_followup_seconds": round(end - start, 3),
            },
            "rejection_reasons": [],
            "profile_write_allowed": False,
            "person_specific_write_allowed": False,
            "methodology_activation_allowed": False,
        })

    # Multiple teacher questions can point at the same downstream answer/help/
    # follow-up chain.  Keep the closest task; do not inflate evidence counts.
    best_by_core: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in candidates:
        window = item.get("decision_window") or {}
        core = (
            str(window.get("student_turn_id") or ""),
            str(window.get("teacher_intervention_turn_id") or ""),
            str(window.get("student_followup_turn_id") or ""),
        )
        previous = best_by_core.get(core)
        if previous is None or float(item.get("start") or 0) > float(previous.get("start") or 0):
            best_by_core[core] = item
    return sorted(best_by_core.values(), key=lambda item: float(item.get("start") or 0))


def build_learning_interactions_v4(
    master: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    sections: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    legacy = [dict(item) for item in _ORIG_BUILD_INTERACTIONS(master, events, sections)]
    new_items = _transcript_decision_interactions(master, events, sections)
    out = list(legacy)
    existing_evidence = [set(str(ref) for ref in (item.get("evidence_refs") or [])) for item in legacy]
    for item in new_items:
        evidence = set(str(ref) for ref in (item.get("evidence_refs") or []))
        # Do not duplicate a legacy complete chain that already covers the same
        # observed four-turn evidence.
        if any(
            other.get("status") == "COMPLETE_EVIDENCE_CANDIDATE"
            and len(evidence & other_evidence) >= 4
            for other, other_evidence in zip(legacy, existing_evidence)
        ):
            continue
        out.append(item)
    out.sort(key=lambda item: (float(item.get("start") or 0), str(item.get("source") or "")))
    return out


def _board_identity(deal: Mapping[str, Any]) -> tuple[str, str] | None:
    for key in ("board_fingerprint", "platform_board_key", "board_id"):
        value = deal.get(key)
        if isinstance(value, Mapping):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        value = _norm(value)
        if value:
            return key, value
    return None


def _cards_by_seat(deal: Mapping[str, Any]) -> dict[str, set[str]]:
    hands = deal.get("hands") if isinstance(deal.get("hands"), Mapping) else {}
    return {seat: set(v2._normalize_hand_cards(hands.get(seat))) for seat in ("N", "E", "S", "W")}


def deal_reconstruction_gate_v4(master: Mapping[str, Any]) -> list[dict[str, Any]]:
    base = [dict(item) for item in _ORIG_DEAL_GATE(master)]
    groups: defaultdict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for deal in master.get("deals") or []:
        if not isinstance(deal, Mapping):
            continue
        identity = _board_identity(deal)
        if identity:
            groups[identity].append(deal)

    for identity, fragments in groups.items():
        if len(fragments) < 2:
            continue
        union = {seat: set() for seat in ("N", "E", "S", "W")}
        evidence: list[str] = []
        source_ids: list[str] = []
        max_fragment_cards = 0
        for fragment in fragments:
            cards = _cards_by_seat(fragment)
            max_fragment_cards = max(max_fragment_cards, len(set().union(*cards.values())))
            for seat in union:
                union[seat].update(cards[seat])
            evidence.extend(v2._evidence_refs(fragment))
            source_ids.append(str(fragment.get("deal_id") or ""))
        all_cards = [card for cards in union.values() for card in cards]
        if not all_cards or len(set(all_cards)) != len(all_cards):
            continue
        if len(set(all_cards)) <= max_fragment_cards:
            continue

        merged = deepcopy(dict(fragments[0]))
        merged["deal_id"] = _stable_id("mergeddeal", master.get("job_id"), identity[0], identity[1])
        merged["hands"] = {seat: sorted(union[seat]) for seat in union}
        merged["evidence"] = _dedupe(evidence)
        tmp_master = {"job_id": master.get("job_id"), "deals": [merged]}
        result = _ORIG_DEAL_GATE(tmp_master)[0]
        result["fragment_merge_status"] = "MERGED_EXACT_EXPLICIT_BOARD_IDENTITY"
        result["fragment_identity_type"] = identity[0]
        result["source_fragment_deal_ids"] = [value for value in source_ids if value]
        result["authority_note"] = (
            "Structured fragments were merged only because an exact explicit board identity matched; "
            "board number/time/topic alone are never sufficient."
        )
        base.append(result)
    return base


def build_quality_layer(
    master: Mapping[str, Any],
    lesson_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    # v3 deliberately calls the v2 builder.  Patch only its evidence-resolution
    # hooks for this call, then restore them even on failure.
    saved = (v2.speaker_summary, v2.build_learning_interactions, v2.deal_reconstruction_gate)
    v2.speaker_summary = speaker_summary_v4
    v2.build_learning_interactions = build_learning_interactions_v4
    v2.deal_reconstruction_gate = deal_reconstruction_gate_v4
    try:
        quality = deepcopy(v3.build_quality_layer(master, lesson_identity))
    finally:
        v2.speaker_summary, v2.build_learning_interactions, v2.deal_reconstruction_gate = saved

    quality["schema"] = QUALITY_SCHEMA
    quality["schema_version"] = QUALITY_SCHEMA_VERSION
    quality["method_version"] = QUALITY_METHOD_VERSION

    interactions = [x for x in (quality.get("learning_interactions") or []) if isinstance(x, Mapping)]
    transcript_complete = [
        x for x in interactions
        if x.get("source") == "transcript_decision_window_v4"
        and x.get("status") == "COMPLETE_EVIDENCE_CANDIDATE"
    ]
    legacy_complete = [
        x for x in interactions
        if x.get("source") != "transcript_decision_window_v4"
        and x.get("status") == "COMPLETE_EVIDENCE_CANDIDATE"
    ]
    deals = [x for x in (quality.get("deal_reconstructions") or []) if isinstance(x, Mapping)]
    merged_deals = [x for x in deals if x.get("fragment_merge_status")]

    quality["interaction_reconstruction_v4"] = {
        "strategy": "explicit-acoustic-role transcript decision windows",
        "completion_requires_observed_task_action_intervention_followup": True,
        "correctness_inferred_from_followup": False,
        "semantic_role_only_can_complete_interaction": False,
        "legacy_complete_count": len(legacy_complete),
        "transcript_decision_window_complete_count": len(transcript_complete),
        "total_complete_count": len(legacy_complete) + len(transcript_complete),
    }
    quality["board_fragment_reconstruction_v4"] = {
        "merge_requires_exact_explicit_identity": True,
        "board_number_only_merge_allowed": False,
        "time_or_topic_only_merge_allowed": False,
        "structured_fragment_merges_created": len(merged_deals),
        "note": (
            "Zero merges is a valid result when existing visual evidence contains no structured card fragments."
        ),
    }

    counts = quality.setdefault("counts", {})
    review_count = int(counts.get("promotable_knowledge_candidates") or 0)
    counts["knowledge_candidates_for_review"] = review_count
    counts["promotable_knowledge_candidates_deprecated_alias"] = review_count
    counts["transcript_decision_window_complete_interactions_v4"] = len(transcript_complete)
    counts["structured_board_fragment_merges_v4"] = len(merged_deals)
    quality["count_semantics"] = {
        "knowledge_candidates_for_review": (
            "Candidates that passed a value filter but remain staging-only and require authority review."
        ),
        "promotable_knowledge_candidates": (
            "DEPRECATED compatibility name; it does not grant promotion or canon activation."
        ),
    }
    for item in quality.get("knowledge_candidates") or []:
        if isinstance(item, dict) and item.get("status") == "VALUE_GATE_PASSED_CANDIDATE":
            item["review_status"] = "KNOWLEDGE_CANDIDATE_FOR_REVIEW"
            item["promotion_allowed"] = False
            item["terminology_note"] = "Passed value gate is not authority to promote."

    brief = quality.get("teacher_brief")
    if isinstance(brief, dict):
        brief["identity_scope"] = "ROLE_LEVEL_ONLY_UNLESS_SEPARATE_R29_MAPPING_IS_OPERATIONAL"
        brief["person_specific_conclusions_allowed_by_this_layer"] = False

    authority = quality.setdefault("authority", {})
    authority.update({
        "canon_activation": "DENY",
        "curriculum_activation": "DENY",
        "methodology_activation": "DENY",
        "student_profile_production_write": "DENY",
        "student_skill_state_production_write": "DENY",
        "person_specific_learning_conclusion": "DENY",
        "database_destination": "STAGING_ONLY",
    })
    incremental = quality.setdefault("incremental_processing", {})
    incremental.update({
        "semantic_only_rebuild_supported": True,
        "heavy_video_reprocessing_required": False,
        "raw_asr_mutated": False,
    })
    cost_gate = quality.setdefault("cost_gate", {})
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
    "speaker_summary_v4",
    "build_learning_interactions_v4",
    "deal_reconstruction_gate_v4",
    "build_quality_layer",
]
