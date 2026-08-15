#!/usr/bin/env python3
"""Bridge Video 3.1 FREE quality and self-improvement contract r25.7."""
from __future__ import annotations

from collections import Counter
import os
import re
import time

import bridge_runtime_hardening_r25_6 as stable
import bridge_worker_3_1_free as core
import run_master_3_1_free as base
import run_master_3_1_free_semantic as semantic

REVISION = "3.1-free-r25.7"
ALLOWED_WHISPER_MODELS = {"small", "medium"}
_INSTALLED = False
_PERSISTENCE_STATE = {"attempted": False, "applied": False}


def validate_model_contract(requested: str | None = None, effective: str | None = None) -> str:
    requested = (requested if requested is not None else os.getenv("BRIDGE_REQUESTED_WHISPER_MODEL", "")).strip()
    effective = (effective if effective is not None else os.getenv("WHISPER_MODEL", "small")).strip()
    if effective not in ALLOWED_WHISPER_MODELS:
        raise RuntimeError(f"WHISPER_MODEL_UNSUPPORTED: {effective}")
    if requested and requested not in ALLOWED_WHISPER_MODELS:
        raise RuntimeError(f"REQUESTED_WHISPER_MODEL_UNSUPPORTED: {requested}")
    if requested and requested != effective:
        raise RuntimeError(
            f"WHISPER_MODEL_MISMATCH: requested={requested} effective={effective}"
        )
    return effective


def principal_free_permission_matrix(permissions) -> list[dict]:
    """Aggregate access roles without storing e-mail, domain, display name, or IDs."""
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
        {
            "type": kind,
            "role": role,
            "allowFileDiscovery": discovery,
            "count": count,
        }
        for (kind, role, discovery), count in sorted(
            counts.items(), key=lambda item: tuple(str(x) for x in item[0])
        )
    ]


def _repetition_artifact(text: str) -> bool:
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", text or "")
    if len(words) < 8:
        return False
    _, count = Counter(w.lower() for w in words).most_common(1)[0]
    return count >= 8 and count / len(words) >= 0.70


def is_proven_no_speech(record: dict) -> bool:
    """Recognize silence only when the primary ASR explicitly contains no words."""
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
        record.update(
            {
                "ok": True,
                "status": "NO_SPEECH",
                "similarity": None,
                "failureReasons": [],
                "estimatedErrorRisk": 0.0,
                "riskBand": "NO_SPEECH",
                "noSpeechControlArtifact": True,
            }
        )
        changed += 1
    return changed


def _complete_deal(item: dict) -> bool:
    return any(item.get(key) not in (None, "", [], {}) for key in (
        "hands", "auction", "contract", "declarer", "opening_lead", "result"
    ))


def _complete_decision(item: dict) -> bool:
    quality = str(item.get("decision_quality") or "")
    return bool(item.get("observed_context")) and bool(
        item.get("reasoning") or (quality and "not rated" not in quality.lower())
    )


def _complete_cycle(item: dict) -> bool:
    return all(item.get(key) for key in (
        "student_action", "teacher_intervention", "student_response", "outcome"
    ))


def augment_quality_gate(master: dict, gate: dict) -> dict:
    deals = master.get("deals") or []
    decisions = master.get("decisions") or []
    cycles = master.get("learning_interactions") or []
    links = master.get("canon_links") or []
    excerpts = {
        str(item.get("canonical_excerpt") or "").strip()
        for item in links
        if str(item.get("canonical_excerpt") or "").strip()
    }
    quality = master.get("content_quality") or {}
    segments = max(1, int(quality.get("transcript_segments") or 0))
    unresolved = int(quality.get("semantic_unresolved_candidates") or 0)
    stats = {
        "candidateDeals": len(deals),
        "completeDeals": sum(_complete_deal(item) for item in deals),
        "candidateDecisions": len(decisions),
        "completeDecisions": sum(_complete_decision(item) for item in decisions),
        "candidateLearningCycles": len(cycles),
        "completeLearningCycles": sum(_complete_cycle(item) for item in cycles),
        "canonLinks": len(links),
        "uniqueCanonExcerpts": len(excerpts),
        "semanticUnresolvedRatio": round(unresolved / segments, 4),
    }
    issues = []
    if stats["candidateDeals"] and not stats["completeDeals"]:
        issues.append("hollow-deal-candidates")
    if stats["candidateDecisions"] and not stats["completeDecisions"]:
        issues.append("hollow-decision-candidates")
    if stats["candidateLearningCycles"] and not stats["completeLearningCycles"]:
        issues.append("hollow-learning-cycles")
    if stats["canonLinks"] >= 10 and stats["uniqueCanonExcerpts"] / stats["canonLinks"] < 0.25:
        issues.append("excessive-canon-duplication")
    if stats["semanticUnresolvedRatio"] > 0.25:
        issues.append("high-semantic-unresolved-ratio")

    episodes = int(quality.get("semantic_episodes") or len(master.get("episodes") or []))
    level = "ARCHIVE_ONLY" if episodes == 0 else ("PARTIAL" if issues else "FULL")
    result = dict(gate or {})
    result.update(
        {
            "qualityContractRevision": REVISION,
            "analysisCompletenessLevel": level,
            "methodologyReady": level != "ARCHIVE_ONLY",
            "qualityIssues": issues,
            **stats,
        }
    )
    return result


def conservative_canon_links(links, episodes, minimum_score: float = 0.12):
    best = {}
    for item in links or []:
        excerpt = str(item.get("canonical_excerpt") or "").strip()
        score = float(item.get("score") or 0.0)
        if not excerpt or score < minimum_score:
            continue
        current = best.get(excerpt)
        if current is None or score > float(current.get("score") or 0.0):
            best[excerpt] = dict(item, status="подтверждённое тематическое совпадение")
    kept = list(best.values())
    kept_ids = {item.get("episode_id") for item in kept}
    for episode in episodes or []:
        if episode.get("episode_id") not in kept_ids:
            episode["course_link_status"] = "не подтверждено"
    return sorted(kept, key=lambda item: float(item.get("score") or 0.0), reverse=True)


def knowledge_status_payload(result: dict, applied: bool) -> dict:
    pdf = result.get("masterPdf") or {}
    return {
        "schema": "bridge-video-knowledge-status",
        "status": "KNOWLEDGE_APPLIED" if applied else "KNOWLEDGE_NOT_APPLIED",
        "job_id": result.get("job_id"),
        "algorithmRevision": REVISION,
        "masterPdfSha256": pdf.get("sha256"),
        "reason": None if applied else "DATABASE_NOT_CONFIGURED_OR_PERSISTENCE_RETURNED_NO_RESULT",
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


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
        master.setdefault("principles", {})["derived_artifacts_exclude_permission_principals"] = True
        return master
    base.master_analysis_payload = payload_with_contract

    original_gate = base.validate_r24_master
    base.validate_r24_master = lambda master: augment_quality_gate(
        master, original_gate(master)
    )

    original_links = base.course_link_candidates
    def links_with_conservative_gate(episodes, course_text, source_id=""):
        raw = original_links(episodes, course_text, source_id)
        return conservative_canon_links(raw, episodes)
    base.course_link_candidates = links_with_conservative_gate

    original_persist = semantic.persist_completed_drive_job
    def tracked_persist(token):
        _PERSISTENCE_STATE["attempted"] = True
        value = original_persist(token)
        _PERSISTENCE_STATE["applied"] = value is not None
        return value
    semantic.persist_completed_drive_job = tracked_persist
    _INSTALLED = True


def run(token_func):
    install(token_func)
    result = semantic.process_job(token_func())
    parent = ((result.get("original") or {}).get("parentFolderId") or "").strip()
    if not parent:
        raise RuntimeError("KNOWLEDGE_STATUS_PARENT_MISSING")
    receipt = knowledge_status_payload(result, _PERSISTENCE_STATE["applied"])
    base.io.upload_json(
        token_func(),
        parent,
        f"KNOWLEDGE_STATUS_{result['job_id']}.json",
        receipt,
    )
    base.io.safe(
        job_id=result.get("job_id"),
        stage=receipt["status"],
        exit_code=0,
    )
    return result
