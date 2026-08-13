#!/usr/bin/env python3
"""3.1 FREE production adapter with semantic post-ASR QC.

This revision adds lessons learned from the real «Диана 10» run:
- every 5-minute speech window receives independent QC and an explicit risk score;
- failed/borderline QC preserves the control recognizer evidence and failure reason;
- high-risk windows receive up to three independent checks, the third from a fresh WAV;
- repeated execution of the same job/revision becomes a no-op instead of creating duplicates.
"""
import json
import os

import bridge_worker_3_1_free as core
import run_master_3_1_free as base
from bridge_neon_persistence import persist_completed_drive_job
from bridge_semantic_qc import SEMANTIC_QC_REVISION, semantic_normalize_segments

# Product name stays 3.1 FREE; only the internal implementation revision changes.
core.ALGORITHM_REVISION = "3.1-free-master-analysis-r6"
base.ALGORITHM_REVISION = core.ALGORITHM_REVISION

# Give Whisper bridge-specific acoustic/lexical anchors seen in real lessons.
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


def _risk_score(similarity, term_ok, check_present, attempts, final_ok):
    """Heuristic triage score, deliberately NOT a calibrated probability."""
    if not check_present:
        score = 0.95
    else:
        score = max(0.02, min(0.95, (0.95 - float(similarity)) * 2.0))
    if not term_ok:
        score = max(score, 0.70)
    if attempts >= 2:
        score = max(score, 0.25)
    if attempts >= 3:
        score = max(score, 0.35)
    if not final_ok:
        score = max(score, 0.80)
    return round(min(score, 0.99), 3)


def _risk_band(score):
    if score >= 0.80:
        return "CRITICAL"
    if score >= 0.50:
        return "HIGH"
    if score >= 0.20:
        return "MEDIUM"
    return "LOW"


def _compare_qc(primary, check):
    similarity = base._similarity(primary, check)
    primary_terms = sorted(set(base.bridge_term_hits(primary)))
    check_terms = sorted(set(base.bridge_term_hits(check)))
    term_ok = (not primary_terms) or bool(set(primary_terms) & set(check_terms))
    reasons = []
    if not check:
        reasons.append("EMPTY_CONTROL_ASR")
    if similarity < 0.80:
        reasons.append("LOW_TEXT_SIMILARITY")
    elif similarity < 0.88:
        reasons.append("BORDERLINE_TEXT_SIMILARITY")
    if not term_ok:
        reasons.append("BRIDGE_TERM_MISMATCH")
    clean = bool(check) and similarity >= 0.88 and term_ok
    acceptable = bool(check) and similarity >= 0.80 and term_ok
    return {
        "similarity": float(similarity),
        "primaryTerms": primary_terms,
        "checkTerms": check_terms,
        "termOk": bool(term_ok),
        "clean": bool(clean),
        "acceptable": bool(acceptable),
        "reasons": reasons,
    }


def _choose_best(candidates):
    return max(
        candidates,
        key=lambda x: (1 if x["compare"]["clean"] else 0,
                       1 if x["compare"]["acceptable"] else 0,
                       x["compare"]["similarity"]),
    )


def qc_transcript_r6(video, work, dur, segs):
    """Full-window QC with evidence-preserving retries and per-block risk."""
    windows = base._windows(segs, dur)
    qc = []

    # Diana 10 showed that sampling 21/23 windows can leave blind spots.  r6 checks all.
    for i, b in enumerate(windows):
        wav1 = work / f"q{i:03d}.wav"
        base.io.wav(video, wav1, b["start"], b["end"] - b["start"])

        strict_text = base.asr_text(wav1, True)
        attempts = [{"name": "strict", "text": strict_text,
                     "compare": _compare_qc(b["text"], strict_text)}]

        # Borderline similarity or any bridge-term disagreement requires a second view.
        if not attempts[-1]["compare"]["clean"]:
            retry_text = base.asr_text(wav1, qc_retry=True)
            attempts.append({"name": "retry", "text": retry_text,
                             "compare": _compare_qc(b["text"], retry_text)})

        # A third attempt is generated from a newly extracted WAV from the canonical video.
        if not any(x["compare"]["clean"] for x in attempts):
            fresh = work / f"q{i:03d}-fresh.wav"
            base.io.wav(video, fresh, b["start"], b["end"] - b["start"])
            fresh_text = base.asr_text(fresh, True)
            attempts.append({"name": "fresh", "text": fresh_text,
                             "compare": _compare_qc(b["text"], fresh_text)})

        best = _choose_best(attempts)
        cmp = best["compare"]
        final_ok = bool(cmp["acceptable"])
        risk = _risk_score(
            cmp["similarity"], cmp["termOk"], bool(best["text"]), len(attempts), final_ok
        )

        if not final_ok:
            for s in segs:
                if s["end"] > b["start"] and s["start"] < b["end"]:
                    s["unreliable"] = True

        record = {
            "block": i,
            "start": b["start"],
            "end": b["end"],
            "ok": final_ok,
            "similarity": round(cmp["similarity"], 3),
            "attempts": len(attempts),
            "selectedAttempt": best["name"],
            "primaryTerms": cmp["primaryTerms"],
            "controlTerms": cmp["checkTerms"],
            "termOk": cmp["termOk"],
            "failureReasons": cmp["reasons"],
            "estimatedErrorRisk": risk,
            "riskBand": _risk_band(risk),
            "riskCalibrated": False,
        }

        # Keep diagnostic recognizer text only where it materially helps review.
        if len(attempts) > 1 or not final_ok or risk >= 0.20:
            record["qcEvidence"] = [
                {
                    "attempt": x["name"],
                    "similarity": round(x["compare"]["similarity"], 3),
                    "termOk": x["compare"]["termOk"],
                    "reasons": x["compare"]["reasons"],
                    "text": x["text"],
                }
                for x in attempts
            ]

        qc.append(record)
        base.io.safe(
            stage="ASR_QC",
            unit_index=i,
            qc_block=i,
            qc_ok=final_ok,
            qc_similarity=round(cmp["similarity"], 3),
            qc_retry=len(attempts) > 1,
            qc_risk=risk,
        )

    failed = sum(not x["ok"] for x in qc)
    critical = sum(x["riskBand"] == "CRITICAL" for x in qc)
    anchors = {0, len(windows) // 2, max(0, len(windows) - 1)} if windows else set()
    anchor_passed = sum(qc[i]["ok"] for i in anchors if i < len(qc))
    anchor_required = max(1, len(anchors) - 1) if anchors else 0
    allowed_failures = max(1, int(len(qc) * 0.20)) if qc else 0
    passed = bool(qc) and failed <= allowed_failures and anchor_passed >= anchor_required

    base.io.safe(
        stage="ASR_QC",
        qc_failed=failed,
        qc_total=len(qc),
        qc_anchor_passed=anchor_passed,
        qc_critical=critical,
        exit_code=0 if passed else 1,
    )
    return qc, passed


# r6 replaces the older sampling QC before obtain_transcript is called.
base.qc_transcript = qc_transcript_r6


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
    risks = [float(x.get("estimatedErrorRisk", 0.0)) for x in tinfo.get("qc", [])]
    tinfo["riskSummary"] = {
        "allWindowsChecked": bool(tinfo.get("qc")) and len(tinfo.get("qc", [])) == len(base._windows(segs, dur)),
        "maxEstimatedErrorRisk": max(risks) if risks else None,
        "mediumOrHigherBlocks": sum(float(x.get("estimatedErrorRisk", 0.0)) >= 0.20 for x in tinfo.get("qc", [])),
        "highOrCriticalBlocks": sum(float(x.get("estimatedErrorRisk", 0.0)) >= 0.50 for x in tinfo.get("qc", [])),
        "calibratedProbability": False,
        "note": "Risk scores are heuristic triage estimates, not statistically calibrated probabilities.",
    }
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
    if tinfo["riskSummary"]["highOrCriticalBlocks"]:
        warnings = list(warnings) + [
            f"ASR risk QC found {tinfo['riskSummary']['highOrCriticalBlocks']} high/critical speech blocks; facts from them require independent evidence."
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
    transcript_qc = kwargs.get("transcript_qc") or {}
    semantic = transcript_qc.get("semanticQc") or {}
    quality = master.setdefault("content_quality", {})
    quality.update({
        "semantic_qc_revision": semantic.get("revision", SEMANTIC_QC_REVISION),
        "semantic_auto_corrections": int(semantic.get("auto_corrections", 0)),
        "semantic_critical_auto_corrections": int(semantic.get("critical_auto_corrections", 0)),
        "semantic_unresolved_candidates": int(semantic.get("unresolved_candidates", 0)),
        "semantic_critical_unresolved": int(semantic.get("critical_unresolved", 0)),
        "semantic_qc_status": semantic.get("status", "NOT_RUN"),
        "asr_risk_summary": transcript_qc.get("riskSummary") or {},
    })
    master.setdefault("principles", {})["raw_asr_preserved_before_semantic_normalization"] = True
    master["principles"]["similarity_qc_does_not_prove_bridge_semantic_correctness"] = True
    master["principles"]["asr_risk_score_is_not_calibrated_probability"] = True
    master["principles"]["all_five_minute_windows_receive_independent_qc"] = True
    return master


# Patch extension points used by process_job.
base.obtain_transcript = obtain_transcript_with_semantic_qc
base.master_analysis_payload = master_payload_with_semantic_qc


def _existing_same_revision_done(token, job_id):
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
            and payload.get("algorithmRevision") == core.ALGORITHM_REVISION
        ):
            return payload
    return None


def process_job(token):
    job_id = os.environ["BRIDGE_JOB_ID"]
    existing = _existing_same_revision_done(token, job_id)
    if existing is not None:
        base.io.safe(job_id=job_id, stage="ALREADY_DONE", exit_code=0)
        return existing

    result = base.process_job(token)
    persist_completed_drive_job(token)
    return result
