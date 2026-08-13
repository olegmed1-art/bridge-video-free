#!/usr/bin/env python3
"""3.1 FREE production adapter with semantic post-ASR QC.

Keeps the proven master-analysis runner intact and adds an evidence-preserving
normalization layer learned from real school transcripts.
"""
import bridge_worker_3_1_free as core
import run_master_3_1_free as base
from bridge_neon_persistence import persist_completed_drive_job
from bridge_semantic_qc import SEMANTIC_QC_REVISION, semantic_normalize_segments

# Product name stays 3.1 FREE; only the internal revision changes.
core.ALGORITHM_REVISION = "3.1-free-master-analysis-r5"
base.ALGORITHM_REVISION = core.ALGORITHM_REVISION

# Give Whisper more bridge-specific acoustic/lexical anchors seen in real lessons.
base.PROMPT = (
    "Спортивный бридж. Термины и обозначения: сдача, раздача, дилер, торговля, заявка, "
    "контракт, мажор, минор, трефа, бубна, черва, пика, БК, 3БК, без козыря, козырь, "
    "фит, баланс, гейм, шлем, пас, пасуем, контра, реконтра, открытие, открывающий, "
    "отвечающий, ответ, ребид, интервенция, призывная контра, конкурентная торговля, "
    "Стейман, трансфер, кюбид, инвит, импас, двойной импас, экспас, разыгрывающий, "
    "защитник, вистующий, стол, болван, первый ход, взятка, фоска, туз, король, дама, "
    "валет, десятка, туз-король, синглет, ренонс, снос, убитка. "
    "Не добавляй неслышанные слова и не меняй смысл по догадке."
)

_original_obtain_transcript = base.obtain_transcript
_original_master_payload = base.master_analysis_payload


def obtain_transcript_with_semantic_qc(t, parent, name, video, work, dur, job):
    segs, tinfo, warnings = _original_obtain_transcript(t, parent, name, video, work, dur, job)
    segs, semantic = semantic_normalize_segments(segs)

    # Human-readable PDF and semantic analysis use deterministic normalized text.
    # Original recognizer output remains in raw_text in the embedded master JSON.
    for segment in segs:
        if segment.get("semantic_corrections"):
            segment["text"] = segment.get("analysis_text", segment.get("text", ""))

    tinfo = dict(tinfo)
    tinfo["semanticQc"] = semantic
    suffix = " / SEMANTIC-QC PASS" if semantic.get("critical_unresolved", 0) == 0 else " / SEMANTIC-QC WARNINGS"
    tinfo["status"] = (tinfo.get("status") or "TRANSCRIPT") + suffix

    if semantic.get("auto_corrections"):
        warnings = list(warnings) + [
            f"Semantic post-ASR QC applied {semantic['auto_corrections']} recorded corrections; raw ASR is preserved in master_analysis.json."
        ]
    if semantic.get("critical_unresolved"):
        warnings = list(warnings) + [
            f"Semantic QC left {semantic['critical_unresolved']} critical bridge-language candidates unresolved; they must not be treated as FACT."
        ]
    base.io.safe(
        job_id=job,
        stage="SEMANTIC_QC",
        exit_code=0,
        content_warning_count=int(semantic.get("critical_unresolved", 0)),
    )
    return segs, tinfo, warnings


def master_payload_with_semantic_qc(**kwargs):
    master = _original_master_payload(**kwargs)
    semantic = (kwargs.get("transcript_qc") or {}).get("semanticQc") or {}
    quality = master.setdefault("content_quality", {})
    quality.update({
        "semantic_qc_revision": semantic.get("revision", SEMANTIC_QC_REVISION),
        "semantic_auto_corrections": int(semantic.get("auto_corrections", 0)),
        "semantic_critical_auto_corrections": int(semantic.get("critical_auto_corrections", 0)),
        "semantic_unresolved_candidates": int(semantic.get("unresolved_candidates", 0)),
        "semantic_critical_unresolved": int(semantic.get("critical_unresolved", 0)),
        "semantic_qc_status": semantic.get("status", "NOT_RUN"),
    })
    master.setdefault("principles", {})["raw_asr_preserved_before_semantic_normalization"] = True
    master["principles"]["similarity_qc_does_not_prove_bridge_semantic_correctness"] = True
    return master


# Patch only the extension points used by process_job; all Drive/source-integrity
# behavior remains in the already-tested production runner.
base.obtain_transcript = obtain_transcript_with_semantic_qc
base.master_analysis_payload = master_payload_with_semantic_qc


def process_job(token):
    result = base.process_job(token)
    persist_completed_drive_job(token)
    return result
