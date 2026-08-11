#!/usr/bin/env python3
"""
Bridge Video Worker 3.1 FREE — cloud runner implementation core.

This file is designed for a STANDARD GitHub-hosted Linux runner in a PUBLIC
technical repository. It never enables paid fallbacks.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json, math, os, time, re
from typing import Optional, Iterable

ALGORITHM_VERSION = "3.1"
ALGORITHM_REVISION = "3.1-free-r2"

STAGES = ["DISCOVERED","QUEUED","FETCHING","TRANSCRIPT_PRIMARY","ASR_QC","VISUAL_PASS_1","VISUAL_PASS_2","REPORT_BUILD","PDF_QC","AI_DONE","CLEANUP_ACK"]
BLOCKED_STAGES = {"BLOCKED_FREE_GUARD","BLOCKED_FREE_CAPACITY","BLOCKED_FREE_ONLY","BLOCKED_IDENTITY","BLOCKED_ACCESS","BLOCKED_CORRUPT_INPUT","FAILED_UNRECOVERABLE"}
PAID_ENDPOINT_MARKERS = ("api.openai.com","generativelanguage.googleapis.com","aiplatform.googleapis.com","assemblyai.com","deepgram.com","api.runpod")
ALLOWED_STANDARD_RUNNER_LABELS = {"ubuntu-latest","ubuntu-24.04","ubuntu-22.04"}
BRIDGE_TERMS = ["бридж","торговля","заявка","контракт","козырь","без козыря","первый ход","взятка","импас","экспас","форсинг","гейм","шлем","контра","реконтра","пас","открытие","ответ","ребид","мажор","минор","расклад","разыгрывающий","вистующий","болван","стейман","трансфер","бк"]

def bridge_term_hits(text: str) -> list[str]:
    low=(text or "").lower(); hits=set()
    for term in BRIDGE_TERMS:
        pattern=r"(?<![A-Za-zА-Яа-яЁё0-9])"+re.escape(term.lower())+r"(?![A-Za-zА-Яа-яЁё0-9])"
        if re.search(pattern,low): hits.add(term)
    return sorted(hits)

@dataclass
class FreeGuardResult:
    ok: bool
    stage: str
    reasons: list[str]

def free_guard(*,repository_private: bool,runner_label: str,larger_runner: bool=False,paid_cloud_resources: bool=False,paid_ai_endpoints: Optional[Iterable[str]]=None,billing_fallback: bool=False) -> FreeGuardResult:
    reasons=[]
    if repository_private: reasons.append("repository is private")
    if runner_label not in ALLOWED_STANDARD_RUNNER_LABELS: reasons.append(f"runner label is not an approved standard runner: {runner_label}")
    if larger_runner: reasons.append("larger runner is forbidden")
    if paid_cloud_resources: reasons.append("paid cloud resource flag is true")
    if billing_fallback: reasons.append("billing fallback is forbidden")
    for endpoint in paid_ai_endpoints or []:
        low=endpoint.lower()
        if any(marker in low for marker in PAID_ENDPOINT_MARKERS): reasons.append(f"paid/tariffed AI endpoint forbidden: {endpoint}")
    return FreeGuardResult(ok=not reasons,stage="QUEUED" if not reasons else "BLOCKED_FREE_GUARD",reasons=reasons)

def stable_job_id(source_kind: str,source_id: str) -> str:
    if not source_kind or not source_id: raise ValueError("source_kind and source_id are required")
    raw=f"{ALGORITHM_REVISION}|{source_kind.strip().lower()}|{source_id.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

def choose_primary_transcript(companion_files: list[dict]) -> dict:
    ranked=[]
    for f in companion_files or []:
        ftype=str(f.get("file_type") or "").upper(); ext=str(f.get("file_extension") or "").upper(); rtype=str(f.get("recording_type") or "").lower(); has_download=bool(f.get("download_url"))
        is_text=ftype in {"TRANSCRIPT","CC","VTT"} or ext in {"VTT","SRT","TXT"} or "transcript" in rtype
        if is_text and has_download: ranked.append((0 if (ftype=="TRANSCRIPT" or ext=="VTT") else 1,f))
    if ranked:
        ranked.sort(key=lambda x:x[0]); return {"mode":"ZOOM_PRIMARY","file":ranked[0][1],"needs_full_asr":False}
    return {"mode":"ASR_FALLBACK","file":None,"needs_full_asr":True}

def autonomous_qc_indices(block_count: int,term_rich_indices: Iterable[int]=()) -> list[int]:
    if block_count<=0: return []
    minimum=min(block_count,max(3,math.ceil(block_count*0.10))); required={0,block_count//2,block_count-1}
    if minimum>len(required):
        for i in range(minimum):
            idx=round(i*(block_count-1)/max(1,minimum-1)); required.add(int(idx))
            if len(required)>=minimum: break
    for idx in term_rich_indices:
        if 0<=int(idx)<block_count: required.add(int(idx))
    return sorted(required)

def transcript_status(auto_qc_pass: bool,unreliable_blocks: int,human_checked: bool=False) -> str:
    if not auto_qc_pass: return "QC_FAILED"
    if human_checked: return "HUMAN-CHECKED TRANSCRIPT"
    if unreliable_blocks: return "AUTO-VERIFIED TRANSCRIPT WITH WARNINGS"
    return "AUTO-VERIFIED TRANSCRIPT"

def next_stage(current: str) -> str:
    if current in BLOCKED_STAGES: return current
    if current not in STAGES: raise ValueError(f"unknown stage: {current}")
    i=STAGES.index(current); return STAGES[min(i+1,len(STAGES)-1)]

def checkpoint_payload(job_id: str,stage: str,attempt: int,last_successful_unit: str="",error_class: str="") -> dict:
    if stage not in STAGES and stage not in BLOCKED_STAGES and not stage.startswith("RETRYING_"): raise ValueError(f"invalid stage: {stage}")
    return {"schema":"bridge-video-checkpoint","schemaVersion":1,"algorithmVersion":ALGORITHM_VERSION,"algorithmRevision":ALGORITHM_REVISION,"job_id":job_id,"stage":stage,"attempt":int(attempt),"timestamp":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"last_successful_unit":last_successful_unit,"error_class":error_class}

def should_skip_full_run(existing_done: Optional[dict],job_id: str) -> bool:
    return bool(existing_done and existing_done.get("status")=="AI_DONE" and existing_done.get("job_id")==job_id and existing_done.get("algorithmRevision")==ALGORITHM_REVISION and existing_done.get("reportSha256"))

def visual_pass1_plan(duration_seconds: float,scene_change_times: Iterable[float],phash_change_times: Iterable[float]) -> dict:
    if duration_seconds<=0: raise ValueError("duration_seconds must be positive")
    anchors={0.0,max(0.0,duration_seconds-0.05)}; t=60.0
    while t<duration_seconds: anchors.add(round(t,3)); t+=60.0
    for seq in (scene_change_times,phash_change_times):
        for x in seq:
            x=float(x)
            if 0<=x<duration_seconds: anchors.add(round(x,3))
    return {"status":"VISUAL_PASS_1_COMPLETE","anchors":sorted(anchors),"coverage_start":0.0,"coverage_end":duration_seconds,"semantic_complete":False}

def visual_pass2_requirements(pass1: dict,critical_speech_times: Iterable[float]) -> dict:
    if pass1.get("status")!="VISUAL_PASS_1_COMPLETE": raise ValueError("pass 1 incomplete")
    targets=set(pass1.get("anchors") or [])
    for x in critical_speech_times: targets.add(round(float(x),3))
    return {"status":"VISUAL_PASS_2_PENDING","targets":sorted(targets),"requires_gap_check":True,"semantic_complete":False}

def parse_vtt_timestamp(value: str) -> float:
    value=value.strip().replace(",","."); parts=value.split(":")
    if len(parts)==3: h,m,sec=parts
    elif len(parts)==2: h,m,sec="0",parts[0],parts[1]
    else: raise ValueError(f"invalid VTT timestamp: {value}")
    return int(h)*3600+int(m)*60+float(sec)

def parse_vtt_text(vtt_text: str) -> list[dict]:
    cues=[]; blocks=re.split(r"\n\s*\n",(vtt_text or "").replace("\r\n","\n").strip()); time_re=re.compile(r"(?P<a>\d{1,2}:\d{2}(?::\d{2})?[\.,]\d{3})\s+-->\s+(?P<b>\d{1,2}:\d{2}(?::\d{2})?[\.,]\d{3})")
    for block in blocks:
        lines=[x.strip() for x in block.splitlines() if x.strip()]; match=None; time_idx=None
        for i,line in enumerate(lines):
            m=time_re.search(line)
            if m: match=m; time_idx=i; break
        if match: cues.append({"start":parse_vtt_timestamp(match.group("a")),"end":parse_vtt_timestamp(match.group("b")),"text":" ".join(lines[time_idx+1:]).strip()})
    return cues

def qc_zoom_vtt(vtt_text: str,expected_duration_seconds: float) -> dict:
    reasons=[]; cues=parse_vtt_text(vtt_text)
    if not cues: return {"ok":False,"reasons":["no VTT cues"],"cue_count":0,"coverage_ratio":0.0}
    previous_end=-1.0; text_tokens=0; normalized=[]
    for cue in cues:
        if cue["end"]<cue["start"]: reasons.append("cue end before start")
        if cue["start"]<previous_end-2.0: reasons.append("non-monotonic cue chronology")
        previous_end=max(previous_end,cue["end"]); toks=re.findall(r"[A-Za-zА-Яа-яЁё0-9]+",cue["text"]); text_tokens+=len(toks); norm=" ".join(toks).lower()
        if norm: normalized.append(norm)
    if text_tokens<5: reasons.append("VTT text nearly empty")
    duration=max(float(expected_duration_seconds or 0),0.0); first=max(0.0,cues[0]["start"]); last=max(c["end"] for c in cues); coverage_ratio=min(1.0,max(0.0,(last-first)/duration)) if duration>0 else 0.0
    if duration>120 and last<duration*0.60: reasons.append("VTT ends too early relative to recording")
    if duration>120 and first>duration*0.20: reasons.append("VTT starts too late relative to recording")
    tail=normalized[-20:]
    if any(x and tail.count(x)>=6 for x in set(tail)): reasons.append("possible repeated-loop VTT")
    return {"ok":not reasons,"reasons":sorted(set(reasons)),"cue_count":len(cues),"coverage_ratio":round(coverage_ratio,4),"first_cue":first,"last_cue":last,"bridge_term_hits":bridge_term_hits(" ".join(c["text"] for c in cues))}

def transcript_primary_decision(companion_files: list[dict],vtt_text: Optional[str],duration_seconds: float) -> dict:
    selected=choose_primary_transcript(companion_files)
    if selected["mode"]!="ZOOM_PRIMARY": return {"mode":"ASR_FALLBACK","reason":"no usable Zoom transcript companion","needs_full_asr":True,"vtt_qc":None}
    qc=qc_zoom_vtt(vtt_text or "",duration_seconds)
    if not qc["ok"]: return {"mode":"ASR_FALLBACK","reason":"Zoom transcript failed QC","needs_full_asr":True,"vtt_qc":qc}
    return {"mode":"ZOOM_PRIMARY","reason":"Zoom transcript passed primary QC","needs_full_asr":False,"vtt_qc":qc,"file":selected["file"]}

def vtt_independent_asr_plan(vtt_text: str,duration_seconds: float) -> dict:
    cues=parse_vtt_text(vtt_text)
    if not cues: return {"intervals":[],"minimum":0,"reason":"no cues"}
    block_count=max(1,math.ceil(float(duration_seconds)/300.0)); term_rich=set()
    for cue in cues:
        if bridge_term_hits(cue["text"]): term_rich.add(min(block_count-1,max(0,int(cue["start"]//300))))
    indices=autonomous_qc_indices(block_count,term_rich)
    return {"intervals":[{"block_index":idx,"start":idx*300.0,"end":min(float(duration_seconds),(idx+1)*300.0)} for idx in indices],"minimum":min(block_count,max(3,math.ceil(block_count*.10))),"term_rich_block_indices":sorted(term_rich),"human_check_required":False}

def sanitize_public_log(data: dict) -> dict:
    allowed={"job_id","stage","attempt","exit_code","size_bytes","sha256","duration_seconds","unit_index","error_class","qc_block","qc_ok","qc_retry","qc_similarity","qc_failed","qc_total","qc_anchor_passed"}
    return {k:data[k] for k in allowed if k in data}

def main():
    repo_private=os.environ.get("BRIDGE_REPOSITORY_PRIVATE","true").lower()=="true"; runner_label=os.environ.get("BRIDGE_RUNNER_LABEL","")
    g=free_guard(repository_private=repo_private,runner_label=runner_label,larger_runner=os.environ.get("BRIDGE_LARGER_RUNNER","false").lower()=="true",paid_cloud_resources=os.environ.get("BRIDGE_PAID_CLOUD","false").lower()=="true",paid_ai_endpoints=[],billing_fallback=os.environ.get("BRIDGE_BILLING_FALLBACK","false").lower()=="true")
    print(json.dumps(sanitize_public_log({"job_id":os.environ.get("BRIDGE_JOB_ID",""),"stage":g.stage,"exit_code":0 if g.ok else 78})))
    if not g.ok: raise SystemExit(78)

if __name__=="__main__": main()
