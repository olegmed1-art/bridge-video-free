#!/usr/bin/env python3
"""Bridge Video 3.1 FREE r25.11 — conservative content and evidence gates."""
from __future__ import annotations

import json
import os
import re
import time

import bridge_runtime_hardening_r25_10 as stable
import bridge_worker_3_1_free as core
import run_master_3_1_free as base
import run_master_3_1_free_semantic as semantic

REVISION = "3.1-free-r25.11"
_INSTALLED = False

_WORD = r"0-9A-Za-zА-Яа-яЁё"
_ACTION_PATTERNS = (
    re.compile(
        r"(?i)(?<![" + _WORD + r"])(?:решил(?:а|и)?|решаем|выбира(?:ем|ю|ет)|"
        r"выбрал(?:а|и)?|игра(?:ем|ть)|сыгра(?:ть|ем)|ходим|ходить|пойти|"
        r"заявля(?:ем|ть)|заявить|пасуем|пасовать|контрим|контрить|"
        r"клад[её]м|класть|бер[её]м|взять)(?![" + _WORD + r"])",
    ),
    re.compile(
        r"(?i)(?<![" + _WORD + r"])(?:нужно|надо|следует|лучше|можно)\s+"
        r"(?:сыграть|играть|пойти|ходить|заявить|пасовать|контрить|положить|взять)"
        r"(?![" + _WORD + r"])",
    ),
    re.compile(
        r"(?i)(?<![" + _WORD + r"])(?:что|как|какую|какой|каким)\s+"
        r"(?:играть|сыграть|заявить|ходить|пойти|карту|ход|заявку)"
        r"(?![" + _WORD + r"])",
    ),
    re.compile(r"(?i)(?<![" + _WORD + r"])(?:выбор|вариант|альтернатива)(?![" + _WORD + r"])")
)
_CAUSE = re.compile(
    r"(?i)(?<![" + _WORD + r"])(?:потому\s+что|так\s+как|поскольку|поэтому|"
    r"следовательно|учитывая|из-за\s+того\s+что)(?![" + _WORD + r"])"
)
_ALTERNATIVE = re.compile(
    r"(?i)(?<![" + _WORD + r"])(?:или|вместо|альтернатива|можно\s+было|а\s+не)(?![" + _WORD + r"])"
)
_TRIGGER = re.compile(
    r"(?i)(?:\?|(?<![" + _WORD + r"])(?:почему|зачем|как|что|какой|какую)(?![" + _WORD + r"]))"
)
_INSTRUCTION = re.compile(
    r"(?i)(?<![" + _WORD + r"])(?:обратите\s+внимание|проверьте|посчитайте|"
    r"правильно|неверно|ошибка|нет|да|нужно|надо|значит|потому\s+что)"
    r"(?![" + _WORD + r"])"
)


def _sentences(text: str) -> list[str]:
    return [
        part.strip()
        for part in re.split(r"(?<=[.!?])\s+|\n+", str(text or ""))
        if part.strip()
    ]


def _exact_match(text: str, cue: str) -> bool:
    cue = str(cue or "").strip()
    if not cue:
        return False
    return bool(re.search(
        r"(?i)(?<![" + _WORD + r"])" + re.escape(cue) + r"(?![" + _WORD + r"])",
        str(text or ""),
    ))


def _explicit_action_sentence(text: str, cues) -> tuple[str | None, list[str]]:
    exact = [str(cue) for cue in cues or [] if _exact_match(text, cue)]
    for sentence in _sentences(text):
        if not any(_exact_match(sentence, cue) for cue in exact):
            continue
        if any(pattern.search(sentence) for pattern in _ACTION_PATTERNS):
            return sentence[:1000], exact
    return None, exact


def _reasoning_clause(text: str, action: str | None) -> str | None:
    for sentence in _sentences(text):
        match = _CAUSE.search(sentence)
        if not match:
            continue
        clause = sentence[match.end():].strip(" ,:;-—")
        if len(re.findall(r"[" + _WORD + r"]+", clause)) >= 3:
            if action and clause.casefold() == action.casefold():
                continue
            return clause[:1000]
    return None


def _alternatives(text: str) -> list[str]:
    return [sentence[:1000] for sentence in _sentences(text) if _ALTERNATIVE.search(sentence)][:3]


def derive_deals_decisions(episodes, job_id):
    """Reject keyword mentions while preserving a per-episode extraction audit."""
    deals, _ = stable.derive_deals_decisions(episodes, job_id)
    decisions = []
    for episode in episodes or []:
        cues = list(episode.get("decision_cues") or [])
        if not cues:
            continue
        context = str(episode.get("summary_text") or "").strip()
        action, exact_cues = _explicit_action_sentence(context, cues)
        evidence = list(dict.fromkeys(episode.get("evidence") or []))
        if not exact_cues:
            episode["decision_extraction_audit"] = {
                "status": "REJECTED_MENTION_ONLY",
                "reason": "NO_EXACT_CUE_BOUNDARY",
                "candidate_cues": cues,
            }
            continue
        if not action:
            episode["decision_extraction_audit"] = {
                "status": "REJECTED_MENTION_ONLY",
                "reason": "NO_EXPLICIT_CHOICE_LANGUAGE",
                "candidate_cues": cues,
                "exact_cues": exact_cues,
            }
            continue
        reasoning = _reasoning_clause(context, action)
        alternatives = _alternatives(context)
        complete = bool(action and reasoning and evidence)
        episode["decision_extraction_audit"] = {
            "status": "ACCEPTED_OBSERVED_DECISION",
            "reason": None,
            "candidate_cues": cues,
            "exact_cues": exact_cues,
            "evidence_refs": evidence,
        }
        decisions.append({
            "decision_id": core.stable_entity_id("decision", job_id, episode["episode_id"]),
            "episode_id": episode["episode_id"],
            "actor": episode.get("speaker"),
            "actor_attribution_status": (
                "speaker_label_present" if episode.get("speaker")
                else "unavailable_without_speaker_labels"
            ),
            "verification_status": "OBSERVED_DECISION",
            "observed_context": context[:1200],
            "action_taken": {"status": "observed_choice", "text": action, "cues": exact_cues},
            "available_information": {
                "bridge_terms": list(episode.get("terms") or []),
                "question_cues": list(episode.get("question_cues") or []),
                "context_excerpt": context[:1200],
            },
            "alternatives": alternatives,
            "reasoning": reasoning,
            "content_completeness": "FULL" if complete else "PARTIAL",
            "decision_quality": "not rated without sufficient context",
            "single_deal_result_must_not_determine_quality": True,
            "statement_type": core.INFERENCE,
            "evidence": evidence,
        })
    return deals, decisions


def _enrich_cycles(original_cycles, episodes):
    """Build only evidenced role-neutral sequences; never use adjacency as a fallback."""
    by_id = {item.get("episode_id"): index for index, item in enumerate(episodes or [])}
    enriched = []
    for cycle in original_cycles or []:
        item = dict(cycle)
        index = by_id.get(item.get("focus_episode_id"))
        window = (episodes or [])[index:index + 5] if index is not None else []
        trigger = str(item.get("task_or_trigger") or "").strip()
        if not trigger and window:
            trigger = str(window[0].get("summary_text") or "").strip()
        trigger = trigger if _TRIGGER.search(trigger) else None
        action = intervention = followup = None
        used = []
        action_pos = None
        for pos, episode in enumerate(window):
            candidate, _ = _explicit_action_sentence(
                episode.get("summary_text") or "", episode.get("decision_cues") or []
            )
            if candidate:
                action = candidate
                action_pos = pos
                used.append(episode)
                break
        response_pos = None
        if action_pos is not None:
            for pos in range(action_pos + 1, len(window)):
                episode = window[pos]
                text = str(episode.get("summary_text") or "").strip()
                cue_backed = bool(episode.get("teacher_cues") or episode.get("error_cues"))
                if cue_backed and _INSTRUCTION.search(text):
                    intervention = text[:800] or None
                    response_pos = pos
                    used.append(episode)
                    break
        if response_pos is not None and response_pos + 1 < len(window):
            text = str(window[response_pos + 1].get("summary_text") or "").strip()
            if text and text not in {trigger, action, intervention}:
                followup = text[:800]
                used.append(window[response_pos + 1])
        evidence = list(dict.fromkeys(
            list(item.get("evidence") or [])
            + [ref for episode in used for ref in (episode.get("evidence") or [])]
        ))
        verified = bool(trigger and action and intervention and followup and evidence)
        labels = any(episode.get("speaker") for episode in used)
        item.update({
            "attribution_status": (
                "available" if labels else "unavailable_without_speaker_labels"
            ),
            "verification_status": (
                "VERIFIED_ROLE_NEUTRAL_SEQUENCE" if verified else "CANDIDATE_ONLY"
            ),
            "role_neutral_sequence": {
                "trigger_context": trigger,
                "observed_action": action,
                "instructional_response": intervention,
                "observed_followup": followup,
            },
            "content_completeness": (
                "EVIDENCE_COMPLETE_ROLE_NEUTRAL" if verified else "PARTIAL"
            ),
            "evidence": evidence,
        })
        enriched.append(item)
    return enriched


def _complete_deal(item: dict) -> bool:
    return stable._complete_deal(item)


def _complete_decision(item: dict) -> bool:
    action = item.get("action_taken") or {}
    return bool(
        item.get("verification_status") == "OBSERVED_DECISION"
        and action.get("text") and item.get("reasoning") and item.get("evidence")
    )


def _complete_cycle(item: dict) -> bool:
    sequence = item.get("role_neutral_sequence") or {}
    return bool(
        item.get("verification_status") == "VERIFIED_ROLE_NEUTRAL_SEQUENCE"
        and item.get("evidence")
        and all(sequence.get(key) for key in (
            "trigger_context", "observed_action", "instructional_response", "observed_followup"
        ))
    )


def augment_quality_gate(master: dict, gate: dict) -> dict:
    deals = master.get("deals") or []
    decisions = master.get("decisions") or []
    cycles = master.get("learning_interactions") or []
    episodes = master.get("episodes") or []
    quality = master.get("content_quality") or {}
    segments = max(1, int(quality.get("transcript_segments") or 0))
    unresolved = int(quality.get("semantic_unresolved_candidates") or 0)
    audits = [item.get("decision_extraction_audit") for item in episodes]
    audits = [item for item in audits if item]
    stats = {
        "candidateDeals": len(deals),
        "completeDeals": sum(_complete_deal(item) for item in deals),
        "decisionCandidatesInspected": len(audits),
        "falsePositiveDecisionsRejected": sum(
            str(item.get("status", "")).startswith("REJECTED") for item in audits
        ),
        "verifiedDecisions": len(decisions),
        "contentCompleteDecisions": sum(_complete_decision(item) for item in decisions),
        "evidenceBackedDecisions": sum(bool(item.get("evidence")) for item in decisions),
        "actorAttributedDecisions": sum(bool(item.get("actor")) for item in decisions),
        "candidateLearningCycles": len(cycles),
        "verifiedRoleNeutralSequences": sum(_complete_cycle(item) for item in cycles),
        "evidenceBackedLearningCycles": sum(bool(item.get("evidence")) for item in cycles),
        "actorAttributedLearningCycles": sum(
            item.get("attribution_status") == "available" for item in cycles
        ),
        "semanticUnresolvedRatio": round(unresolved / segments, 4),
    }
    issues = []
    if stats["candidateDeals"] and not stats["completeDeals"]:
        issues.append("hollow-deal-candidates")
    if stats["decisionCandidatesInspected"] and not stats["verifiedDecisions"]:
        issues.append("no-verified-decisions")
    elif stats["verifiedDecisions"] and not stats["contentCompleteDecisions"]:
        issues.append("no-content-complete-decisions")
    if stats["candidateLearningCycles"] and not stats["verifiedRoleNeutralSequences"]:
        issues.append("no-verified-role-neutral-sequences")
    if stats["candidateLearningCycles"] and not stats["actorAttributedLearningCycles"]:
        issues.append("speaker-attribution-unavailable")
    if stats["semanticUnresolvedRatio"] > 0.25:
        issues.append("high-semantic-unresolved-ratio")
    episode_count = int(quality.get("semantic_episodes") or len(episodes))
    level = "ARCHIVE_ONLY" if episode_count == 0 else ("PARTIAL" if issues else "FULL")
    result = dict(gate or {})
    result.update({
        "qualityContractRevision": REVISION,
        "analysisCompletenessLevel": level,
        "methodologyReady": level != "ARCHIVE_ONLY",
        "qualityIssues": issues,
        "canonEvidencePreservedInMaster": True,
        **stats,
    })
    return result


def receipt_matches_revision(payload: dict, job_id: str, revision: str = REVISION) -> bool:
    return (
        payload.get("status") == "CLEANUP_ACK"
        and payload.get("job_id") == job_id
        and payload.get("algorithmRevision") == revision
    )


def _already_completed_for_revision(token: str, job_id: str) -> bool:
    name = f"CLEANUP_ACK_{job_id}.json"
    candidates = base.io.search(token, f"trashed=false and name='{name}'")
    candidates.sort(key=lambda item: item.get("modifiedTime") or "", reverse=True)
    for candidate in candidates:
        try:
            payload = json.loads(base._read_text(token, candidate))
        except Exception:
            continue
        if receipt_matches_revision(payload, job_id):
            return True
    return False


def _latest_done_for_revision(token: str, job_id: str) -> dict | None:
    name = f"AI_DONE_{job_id}.json"
    candidates = base.io.search(token, f"trashed=false and name='{name}'")
    candidates.sort(key=lambda item: item.get("modifiedTime") or "", reverse=True)
    for candidate in candidates:
        try:
            payload = json.loads(base._read_text(token, candidate))
        except Exception:
            continue
        if (
            payload.get("status") == "AI_DONE"
            and payload.get("job_id") == job_id
            and payload.get("algorithmRevision") == REVISION
        ):
            return payload
    return None


def _knowledge_status(result: dict, persistence_result) -> dict:
    pdf = result.get("masterPdf") or {}
    applied = bool(persistence_result and persistence_result.get("persisted"))
    return {
        "schema": "bridge-video-knowledge-status",
        "status": "KNOWLEDGE_APPLIED" if applied else "KNOWLEDGE_NOT_APPLIED",
        "job_id": result.get("job_id"),
        "algorithmRevision": REVISION,
        "masterPdfSha256": pdf.get("sha256"),
        "database": {
            "persisted": applied,
            "analysis_run_id": (persistence_result or {}).get("analysis_run_id"),
            "transcript_id": (persistence_result or {}).get("transcript_id"),
            "episodes": (persistence_result or {}).get("episodes", 0),
            "learning_cycles": (persistence_result or {}).get("learning_cycles", 0),
            "decisions": (persistence_result or {}).get("decisions", 0),
            "evidence": (persistence_result or {}).get("evidence", 0),
            "evidence_links": (persistence_result or {}).get("evidence_links", 0),
        },
        "reason": None if applied else "DATABASE_PERSISTENCE_DID_NOT_CONFIRM_COMMIT",
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _publish_knowledge_status(token_func, result: dict) -> dict:
    parent = ((result.get("original") or {}).get("parentFolderId") or "").strip()
    if not parent:
        raise RuntimeError("KNOWLEDGE_STATUS_PARENT_MISSING")
    receipt = _knowledge_status(result, stable._PERSISTENCE_STATE["result"])
    base.io.upload_json(token_func(), parent, f"KNOWLEDGE_STATUS_{result['job_id']}.json", receipt)
    base.io.safe(job_id=result.get("job_id"), stage=receipt["status"], exit_code=0)
    if receipt["status"] != "KNOWLEDGE_APPLIED":
        raise RuntimeError("DATABASE_PERSISTENCE_NOT_APPLIED")
    return receipt


def install(token_func):
    global _INSTALLED
    requested = os.getenv("BRIDGE_REQUESTED_ALGORITHM_REVISION", "").strip()
    if requested and requested != REVISION:
        raise RuntimeError(
            f"ALGORITHM_REVISION_MISMATCH: requested={requested} executing={REVISION}"
        )
    previous = os.environ.get("BRIDGE_REQUESTED_ALGORITHM_REVISION")
    os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = stable.REVISION
    try:
        stable.install(token_func)
    finally:
        if previous is None:
            os.environ.pop("BRIDGE_REQUESTED_ALGORITHM_REVISION", None)
        else:
            os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = previous
    core.ALGORITHM_REVISION = REVISION
    base.ALGORITHM_REVISION = REVISION
    if _INSTALLED:
        return
    base.derive_deals_decisions = derive_deals_decisions
    original_cycles = core.learning_interaction_cycles
    core.learning_interaction_cycles = lambda episodes, job_id: _enrich_cycles(
        original_cycles(episodes, job_id), episodes
    )
    original_gate = base.validate_r24_master
    base.validate_r24_master = lambda master: augment_quality_gate(
        master, original_gate(master)
    )
    original_payload = base.master_analysis_payload
    def payload_with_r2511_principles(**kwargs):
        master = original_payload(**kwargs)
        principles = master.setdefault("principles", {})
        principles["keyword_mentions_are_not_decisions"] = True
        principles["adjacent_episodes_are_not_learning_cycles"] = True
        principles["database_domain_rows_reference_evidence_entities"] = True
        return master
    base.master_analysis_payload = payload_with_r2511_principles
    _INSTALLED = True


def run(token_func):
    install(token_func)
    token = token_func()
    job_id = os.environ["BRIDGE_JOB_ID"]
    if _already_completed_for_revision(token, job_id):
        result = _latest_done_for_revision(token, job_id)
        if result is None:
            raise RuntimeError("AI_DONE_MISSING_FOR_COMPLETED_REVISION")
        semantic.persist_completed_drive_job(token)
        _publish_knowledge_status(token_func, result)
        base.io.safe(
            job_id=job_id,
            stage="ALREADY_COMPLETED",
            exit_code=0,
            terminal_receipt="CLEANUP_ACK+KNOWLEDGE_APPLIED",
            algorithm_revision=REVISION,
        )
        return result
    result = semantic.process_job(token)
    if not isinstance(result, dict):
        return result
    _publish_knowledge_status(token_func, result)
    return result
