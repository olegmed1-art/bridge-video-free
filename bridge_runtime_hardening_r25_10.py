#!/usr/bin/env python3
"""Bridge Video 3.1 FREE r25.10 — evidence-preserving knowledge persistence.

This revision keeps the complete master evidence archive, adds role-neutral
decision/teaching-sequence extraction, requires the requested ASR model, removes
permission principals from derived artifacts, and treats database persistence as
a required terminal stage.
"""
from __future__ import annotations

from collections import Counter
import json
import os
import re
import time

import bridge_runtime_hardening_r25_6 as stable
import bridge_worker_3_1_free as core
import run_master_3_1_free as base
import run_master_3_1_free_semantic as semantic

REVISION = "3.1-free-r25.10"
ALLOWED_WHISPER_MODELS = {"small", "medium"}
_INSTALLED = False
_PERSISTENCE_STATE = {"attempted": False, "applied": False, "result": None}


def validate_model_contract(requested: str | None = None, effective: str | None = None) -> str:
    requested = (requested if requested is not None else os.getenv(
        "BRIDGE_REQUESTED_WHISPER_MODEL", ""
    )).strip()
    effective = (effective if effective is not None else os.getenv(
        "WHISPER_MODEL", "small"
    )).strip()
    if requested and requested not in ALLOWED_WHISPER_MODELS:
        raise RuntimeError(f"REQUESTED_WHISPER_MODEL_UNSUPPORTED: {requested}")
    if effective not in ALLOWED_WHISPER_MODELS:
        raise RuntimeError(f"WHISPER_MODEL_UNSUPPORTED: {effective}")
    if requested and requested != effective:
        raise RuntimeError(
            f"WHISPER_MODEL_MISMATCH: requested={requested} effective={effective}"
        )
    return effective


def principal_free_permission_matrix(permissions) -> list[dict]:
    counts = Counter(
        (
            str(p.get("type") or "unknown"),
            str(p.get("role") or "unknown"),
            p.get("allowFileDiscovery"),
        )
        for p in (permissions or [])
        if p.get("role") != "owner"
    )
    return [
        {"type": kind, "role": role, "allowFileDiscovery": discovery, "count": count}
        for (kind, role, discovery), count in sorted(
            counts.items(), key=lambda item: tuple(str(x) for x in item[0])
        )
    ]


def _repetition_artifact(text: str) -> bool:
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", text or "")
    if len(words) < 8:
        return False
    _, count = Counter(word.lower() for word in words).most_common(1)[0]
    return count >= 8 and count / len(words) >= 0.70


def is_proven_no_speech(record: dict) -> bool:
    if record.get("primaryTextEmpty") is not True:
        return False
    evidence = record.get("qcEvidence") or []
    texts = [str(item.get("text") or "").strip() for item in evidence]
    return bool(evidence) and all((not text) or _repetition_artifact(text) for text in texts)


def normalize_no_speech_qc(qc) -> int:
    changed = 0
    for record in qc or []:
        if bool(record.get("ok")) or not is_proven_no_speech(record):
            continue
        record.update({
            "ok": True,
            "status": "NO_SPEECH",
            "similarity": None,
            "failureReasons": [],
            "estimatedErrorRisk": 0.0,
            "riskBand": "NO_SPEECH",
            "noSpeechControlArtifact": True,
        })
        changed += 1
    return changed


def _sentences(text: str) -> list[str]:
    return [
        part.strip()
        for part in re.split(r"(?<=[.!?])\s+|\n+", str(text or ""))
        if part.strip()
    ]


def _first_sentence(text: str, markers) -> str | None:
    markers = tuple(str(x).lower() for x in markers if x)
    for sentence in _sentences(text):
        low = sentence.lower()
        if any(marker in low for marker in markers):
            return sentence[:1000]
    return None


def derive_deals_decisions(episodes, job_id):
    """Keep unknown bridge state unknown while extracting explicit decision content."""
    deals = []
    decisions = []
    causal = (
        "потому что", "так как", "поэтому", "поскольку", "значит", "если ",
        "учитывая", "из-за", "следовательно", "чтобы ",
    )
    alternative = (" или ", "вместо", "альтернати", "можно было", "другой")
    for episode in episodes or []:
        if episode.get("type") in {"торговля", "розыгрыш", "защита", "ошибка/коррекция"}:
            deals.append({
                "deal_id": core.stable_entity_id("deal", job_id, episode["episode_id"]),
                "episode_id": episode["episode_id"],
                "status": "candidate",
                "hands": {"N": None, "E": None, "S": None, "W": None},
                "auction": None,
                "contract": None,
                "declarer": None,
                "opening_lead": None,
                "result": None,
                "reconstruction_rule": "UNKNOWN unless explicitly recoverable from transcript/visual evidence",
                "statement_type": core.UNCERTAIN,
                "evidence": list(dict.fromkeys(
                    (episode.get("evidence") or []) + (episode.get("visual_evidence") or [])
                )),
            })
        cues = list(episode.get("decision_cues") or [])
        if not cues:
            continue
        context = str(episode.get("summary_text") or "").strip()
        action_sentence = _first_sentence(context, cues)
        reasoning_sentence = _first_sentence(context, causal)
        alternative_sentence = _first_sentence(context, alternative)
        evidence = list(dict.fromkeys(episode.get("evidence") or []))
        content_complete = bool(action_sentence and reasoning_sentence and evidence)
        decisions.append({
            "decision_id": core.stable_entity_id("decision", job_id, episode["episode_id"]),
            "episode_id": episode["episode_id"],
            "actor": episode.get("speaker"),
            "actor_attribution_status": (
                "speaker_label_present" if episode.get("speaker")
                else "unavailable_without_speaker_labels"
            ),
            "observed_context": context[:1200],
            "action_taken": {
                "status": "observed_text" if action_sentence else "cue_only",
                "text": action_sentence,
                "cues": cues,
            },
            "available_information": {
                "bridge_terms": list(episode.get("terms") or []),
                "question_cues": list(episode.get("question_cues") or []),
                "context_excerpt": context[:1200],
            },
            "alternatives": [alternative_sentence] if alternative_sentence else [],
            "reasoning": reasoning_sentence,
            "content_completeness": "FULL" if content_complete else "PARTIAL",
            "decision_quality": "not rated without sufficient context",
            "single_deal_result_must_not_determine_quality": True,
            "statement_type": core.INFERENCE,
            "evidence": evidence,
        })
    return deals, decisions


def _enrich_cycles(original_cycles, episodes):
    by_id = {item.get("episode_id"): index for index, item in enumerate(episodes or [])}
    enriched = []
    for cycle in original_cycles or []:
        item = dict(cycle)
        index = by_id.get(item.get("focus_episode_id"))
        window = (episodes or [])[index:index + 4] if index is not None else []
        trigger = str(item.get("task_or_trigger") or "").strip() or (
            str(window[0].get("summary_text") or "").strip() if window else ""
        )
        action = None
        intervention = None
        followup = None
        action_pos = None
        for pos, episode in enumerate(window):
            if episode.get("decision_cues"):
                action = _first_sentence(
                    episode.get("summary_text") or "", episode.get("decision_cues") or []
                ) or str(episode.get("summary_text") or "")[:800]
                action_pos = pos
                break
        if action_pos is not None:
            for pos in range(action_pos + 1, len(window)):
                episode = window[pos]
                if episode.get("teacher_cues") or episode.get("error_cues"):
                    intervention = str(episode.get("summary_text") or "")[:800] or None
                    if pos + 1 < len(window):
                        followup = str(window[pos + 1].get("summary_text") or "")[:800] or None
                    break
        if intervention is None and len(window) >= 2:
            intervention = str(window[1].get("summary_text") or "")[:800] or None
            if len(window) >= 3:
                followup = str(window[2].get("summary_text") or "")[:800] or None
        item.update({
            "attribution_status": "available" if any(
                x.get("speaker") for x in window
            ) else "unavailable_without_speaker_labels",
            "role_neutral_sequence": {
                "trigger_context": trigger[:800] or None,
                "observed_action": action,
                "instructional_response": intervention,
                "observed_followup": followup,
            },
            "content_completeness": (
                "FULL" if action and intervention and followup else "PARTIAL"
            ),
        })
        enriched.append(item)
    return enriched


def _complete_deal(item: dict) -> bool:
    hands = item.get("hands") or {}
    four_hands = isinstance(hands, dict) and all(hands.get(seat) for seat in "NESW")
    return four_hands or any(item.get(key) not in (None, "", [], {}) for key in (
        "auction", "contract", "declarer", "opening_lead", "result"
    ))


def _complete_decision(item: dict) -> bool:
    action = item.get("action_taken") or {}
    return bool(
        item.get("observed_context")
        and action.get("text")
        and item.get("available_information")
        and item.get("reasoning")
        and item.get("evidence")
    )


def _complete_cycle(item: dict) -> bool:
    sequence = item.get("role_neutral_sequence") or {}
    return all(sequence.get(key) for key in (
        "observed_action", "instructional_response", "observed_followup"
    ))


def augment_quality_gate(master: dict, gate: dict) -> dict:
    deals = master.get("deals") or []
    decisions = master.get("decisions") or []
    cycles = master.get("learning_interactions") or []
    links = master.get("canon_links") or []
    excerpts = {
        str(item.get("canonical_excerpt") or "").strip()
        for item in links if str(item.get("canonical_excerpt") or "").strip()
    }
    quality = master.get("content_quality") or {}
    segments = max(1, int(quality.get("transcript_segments") or 0))
    unresolved = int(quality.get("semantic_unresolved_candidates") or 0)
    stats = {
        "candidateDeals": len(deals),
        "completeDeals": sum(_complete_deal(item) for item in deals),
        "candidateDecisions": len(decisions),
        "contentCompleteDecisions": sum(_complete_decision(item) for item in decisions),
        "actorAttributedDecisions": sum(bool(item.get("actor")) for item in decisions),
        "candidateLearningCycles": len(cycles),
        "contentCompleteLearningCycles": sum(_complete_cycle(item) for item in cycles),
        "actorAttributedLearningCycles": sum(
            item.get("attribution_status") == "available" for item in cycles
        ),
        "canonLinks": len(links),
        "uniqueCanonExcerpts": len(excerpts),
        "semanticUnresolvedRatio": round(unresolved / segments, 4),
    }
    issues = []
    if stats["candidateDeals"] and not stats["completeDeals"]:
        issues.append("hollow-deal-candidates")
    if stats["candidateDecisions"] and not stats["contentCompleteDecisions"]:
        issues.append("no-content-complete-decisions")
    if stats["candidateLearningCycles"] and not stats["contentCompleteLearningCycles"]:
        issues.append("no-content-complete-learning-cycles")
    if stats["semanticUnresolvedRatio"] > 0.25:
        issues.append("high-semantic-unresolved-ratio")
    episodes = int(quality.get("semantic_episodes") or len(master.get("episodes") or []))
    level = "ARCHIVE_ONLY" if episodes == 0 else ("PARTIAL" if issues else "FULL")
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
        },
        "reason": None if applied else "DATABASE_PERSISTENCE_DID_NOT_CONFIRM_COMMIT",
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _publish_knowledge_status(token_func, result: dict) -> dict:
    parent = ((result.get("original") or {}).get("parentFolderId") or "").strip()
    if not parent:
        raise RuntimeError("KNOWLEDGE_STATUS_PARENT_MISSING")
    receipt = _knowledge_status(result, _PERSISTENCE_STATE["result"])
    base.io.upload_json(
        token_func(), parent, f"KNOWLEDGE_STATUS_{result['job_id']}.json", receipt
    )
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
    effective_model = validate_model_contract()
    had_requested = "BRIDGE_REQUESTED_ALGORITHM_REVISION" in os.environ
    saved_requested = os.environ.get("BRIDGE_REQUESTED_ALGORITHM_REVISION")
    os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = stable.REVISION
    try:
        stable.install(token_func)
    finally:
        if had_requested:
            os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = saved_requested or ""
        else:
            os.environ.pop("BRIDGE_REQUESTED_ALGORITHM_REVISION", None)
    core.ALGORITHM_REVISION = REVISION
    base.ALGORITHM_REVISION = REVISION
    if _INSTALLED:
        return

    base.io.pmatrix = principal_free_permission_matrix
    original_qc = base.qc_transcript
    def qc_with_no_speech(video, work, duration, segments):
        qc, passed = original_qc(video, work, duration, segments)
        changed = normalize_no_speech_qc(qc)
        if changed and all(bool(item.get("ok")) for item in qc):
            passed = True
        return qc, passed
    base.qc_transcript = qc_with_no_speech

    original_cycles = core.learning_interaction_cycles
    def cycles_with_content(episodes, job_id):
        return _enrich_cycles(original_cycles(episodes, job_id), episodes)
    core.learning_interaction_cycles = cycles_with_content
    base.derive_deals_decisions = derive_deals_decisions

    original_payload = base.master_analysis_payload
    def payload_with_contract(**kwargs):
        master = original_payload(**kwargs)
        quality = master.setdefault("content_quality", {})
        quality["asr_model_requested"] = os.getenv("BRIDGE_REQUESTED_WHISPER_MODEL") or None
        quality["asr_model_effective"] = effective_model
        quality["asr_model_match"] = (
            not quality["asr_model_requested"]
            or quality["asr_model_requested"] == effective_model
        )
        labels_present = any(item.get("speaker") for item in kwargs.get("transcript") or [])
        quality["speaker_labels_present"] = labels_present
        quality["actor_attribution_status"] = (
            "available" if labels_present else "unavailable_without_speaker_labels"
        )
        master.setdefault("principles", {})[
            "derived_artifacts_exclude_permission_principals"
        ] = True
        master["principles"]["role_neutral_content_precedes_actor_attribution"] = True
        master["principles"]["canon_evidence_preserved_in_embedded_master"] = True
        return master
    base.master_analysis_payload = payload_with_contract

    original_gate = base.validate_r24_master
    base.validate_r24_master = lambda master: augment_quality_gate(
        master, original_gate(master)
    )

    original_persist = semantic.persist_completed_drive_job
    def tracked_persist(token):
        _PERSISTENCE_STATE.update({"attempted": True, "applied": False, "result": None})
        value = original_persist(token)
        _PERSISTENCE_STATE["result"] = value
        _PERSISTENCE_STATE["applied"] = bool(value and value.get("persisted"))
        return value
    semantic.persist_completed_drive_job = tracked_persist
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
