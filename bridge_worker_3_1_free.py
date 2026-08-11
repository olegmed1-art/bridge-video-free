#!/usr/bin/env python3
"""Bridge Video Worker 3.1.3 — free cloud runner analysis core."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json, math, os, time, re
from typing import Optional, Iterable

ALGORITHM_VERSION = "3.1.3"
ALGORITHM_REVISION = "3.1.3-semantic-r1"
STAGES=["DISCOVERED","QUEUED","FETCHING","TRANSCRIPT_PRIMARY","ASR_QC","VISUAL_PASS_1","VISUAL_PASS_2","SEMANTIC_ANALYSIS","REPORT_BUILD","PDF_QC","AI_DONE","CLEANUP_ACK"]
BLOCKED_STAGES={"BLOCKED_FREE_GUARD","BLOCKED_FREE_CAPACITY","BLOCKED_FREE_ONLY","BLOCKED_IDENTITY","BLOCKED_ACCESS","BLOCKED_CORRUPT_INPUT","FAILED_UNRECOVERABLE"}
PAID_ENDPOINT_MARKERS=("api.openai.com","generativelanguage.googleapis.com","aiplatform.googleapis.com","assemblyai.com","deepgram.com","api.runpod")
ALLOWED_STANDARD_RUNNER_LABELS={"ubuntu-latest","ubuntu-24.04","ubuntu-22.04"}
BRIDGE_TERMS=["бридж","торговля","заявка","контракт","козырь","без козыря","первый ход","взятка","импас","экспас","форсинг","гейм","шлем","контра","реконтра","пас","открытие","ответ","ребид","мажор","минор","расклад","разыгрывающий","вистующий","болван","стейман","трансфер","бк"]
TEACHER_CUES=["правильно","неправильно","ошибка","почему","нужно","надо","обрати внимание","запомни","повтори","давай еще раз","лучше"]
DECISION_CUES=["пас","контра","реконтра","открытие","ответ","ребид","контракт","первый ход","импас","взятка","козыр","бк"]

def bridge_term_hits(text):
    low=(text or '').lower(); hits=set()
    for term in BRIDGE_TERMS:
        if re.search(r"(?<![A-Za-zА-Яа-яЁё0-9])"+re.escape(term)+r"(?![A-Za-zА-Яа-яЁё0-9])",low): hits.add(term)
    return sorted(hits)

def cue_hits(text,cues):
    low=(text or '').lower(); return [x for x in cues if x in low]

@dataclass
class FreeGuardResult:
    ok: bool; stage: str; reasons: list[str]

def free_guard(*,repository_private,runner_label,larger_runner=False,paid_cloud_resources=False,paid_ai_endpoints=None,billing_fallback=False):
    reasons=[]
    if repository_private: reasons.append('repository is private')
    if runner_label not in ALLOWED_STANDARD_RUNNER_LABELS: reasons.append('runner label is not an approved standard runner')
    if larger_runner: reasons.append('larger runner is forbidden')
    if paid_cloud_resources: reasons.append('paid cloud resource flag is true')
    if billing_fallback: reasons.append('billing fallback is forbidden')
    for endpoint in paid_ai_endpoints or []:
        if any(x in endpoint.lower() for x in PAID_ENDPOINT_MARKERS): reasons.append('paid/tariffed AI endpoint forbidden')
    return FreeGuardResult(not reasons,'QUEUED' if not reasons else 'BLOCKED_FREE_GUARD',reasons)

def stable_job_id(source_kind,source_id):
    return hashlib.sha256(f"{ALGORITHM_REVISION}|{source_kind.strip().lower()}|{source_id.strip()}".encode()).hexdigest()[:32]

def autonomous_qc_indices(block_count,term_rich_indices=()):
    if block_count<=0:return []
    minimum=min(block_count,max(3,math.ceil(block_count*.10))); required={0,block_count//2,block_count-1}
    for i in range(minimum): required.add(round(i*(block_count-1)/max(1,minimum-1)))
    required.update(int(x) for x in term_rich_indices if 0<=int(x)<block_count)
    return sorted(required)

def transcript_status(auto_qc_pass,unreliable_blocks,human_checked=False):
    if not auto_qc_pass:return 'QC_FAILED'
    if human_checked:return 'HUMAN-CHECKED TRANSCRIPT'
    return 'AUTO-VERIFIED TRANSCRIPT WITH WARNINGS' if unreliable_blocks else 'AUTO-VERIFIED TRANSCRIPT'

def next_stage(current):
    if current in BLOCKED_STAGES:return current
    i=STAGES.index(current); return STAGES[min(i+1,len(STAGES)-1)]

def checkpoint_payload(job_id,stage,attempt,last_successful_unit='',error_class=''):
    return {'schema':'bridge-video-checkpoint','schemaVersion':2,'algorithmVersion':ALGORITHM_VERSION,'algorithmRevision':ALGORITHM_REVISION,'job_id':job_id,'stage':stage,'attempt':int(attempt),'timestamp':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'last_successful_unit':last_successful_unit,'error_class':error_class}

def should_skip_full_run(existing_done,job_id):
    return bool(existing_done and existing_done.get('status')=='AI_DONE' and existing_done.get('job_id')==job_id and existing_done.get('algorithmRevision')==ALGORITHM_REVISION and existing_done.get('reportSha256'))

def visual_pass1_plan(duration_seconds,scene_change_times,phash_change_times):
    anchors={0.0,max(0.0,duration_seconds-.05)}; t=60.
    while t<duration_seconds: anchors.add(round(t,3)); t+=60
    for seq in (scene_change_times,phash_change_times):
        for x in seq:
            if 0<=float(x)<duration_seconds: anchors.add(round(float(x),3))
    return {'status':'VISUAL_PASS_1_COMPLETE','anchors':sorted(anchors),'coverage_start':0.,'coverage_end':duration_seconds,'semantic_complete':False}

def visual_pass2_requirements(pass1,critical_speech_times):
    targets=set(pass1.get('anchors') or []); targets.update(round(float(x),3) for x in critical_speech_times)
    return {'status':'VISUAL_PASS_2_PENDING','targets':sorted(targets),'requires_gap_check':True,'semantic_complete':False}

def semantic_episode_plan(blocks):
    """Create candidate semantic episodes; never treat 5-minute ASR boundaries as pedagogical boundaries."""
    candidates=[]; active=None
    for b in blocks:
        text=b.get('text',''); terms=bridge_term_hits(text); teacher=cue_hits(text,TEACHER_CUES); decisions=cue_hits(text,DECISION_CUES)
        significant=bool(terms or teacher or decisions)
        if not significant: continue
        signature=set(terms+decisions)
        if active and b['start']-active['end']<=8 and (signature & set(active['signature'])):
            active['end']=b['end']; active['block_indices'].append(b['index']); active['terms']=sorted(set(active['terms'])|set(terms)); active['teacher_cues']=sorted(set(active['teacher_cues'])|set(teacher)); active['decision_cues']=sorted(set(active['decision_cues'])|set(decisions)); active['signature']=sorted(set(active['signature'])|signature)
        else:
            if active: candidates.append(active)
            active={'start':b['start'],'end':b['end'],'block_indices':[b['index']],'terms':terms,'teacher_cues':teacher,'decision_cues':decisions,'signature':sorted(signature),'unreliable':bool(b.get('unreliable'))}
    if active:candidates.append(active)
    for i,e in enumerate(candidates,1):
        e['episode_id']=i; e['type']='методический эпизод'; e['student_action']=None; e['teacher_action']=None; e['error_or_branch']=None; e['course_link_status']='не установлено'; e['confidence']='medium' if not e['unreliable'] else 'low'; e['warnings']=['ASR unreliable'] if e['unreliable'] else []
    return candidates

def semantic_analysis_payload(passport,blocks,episodes,course_links=None,screenshots=None,transcript_qc=None,visual_qc=None):
    return {'schema':'bridge-video-semantic-analysis','schemaVersion':1,'algorithmVersion':ALGORITHM_VERSION,'algorithmRevision':ALGORITHM_REVISION,'source':passport,'session_summary':{'episode_count':len(episodes),'topics':sorted({t for e in episodes for t in e.get('terms',[])})},'timeline':[{'start':e['start'],'end':e['end'],'episode_id':e['episode_id']} for e in episodes],'episodes':episodes,'course_links':course_links or [],'screenshots':screenshots or [],'transcript_qc':transcript_qc or {},'visual_qc':visual_qc or {},'warnings':[]}

def sanitize_public_log(data):
    allowed={'job_id','stage','attempt','exit_code','size_bytes','sha256','duration_seconds','unit_index','error_class','qc_block','qc_ok','qc_retry','qc_similarity','qc_failed','qc_total','qc_anchor_passed','episode_count'}
    return {k:data[k] for k in allowed if k in data}

def main():
    g=free_guard(repository_private=os.environ.get('BRIDGE_REPOSITORY_PRIVATE','true').lower()=='true',runner_label=os.environ.get('BRIDGE_RUNNER_LABEL',''),larger_runner=os.environ.get('BRIDGE_LARGER_RUNNER','false').lower()=='true',paid_cloud_resources=os.environ.get('BRIDGE_PAID_CLOUD','false').lower()=='true',billing_fallback=os.environ.get('BRIDGE_BILLING_FALLBACK','false').lower()=='true')
    print(json.dumps(sanitize_public_log({'job_id':os.environ.get('BRIDGE_JOB_ID',''),'stage':g.stage,'exit_code':0 if g.ok else 78})))
    if not g.ok: raise SystemExit(78)
if __name__=='__main__':main()
