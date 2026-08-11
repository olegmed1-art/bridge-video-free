#!/usr/bin/env python3
"""Bridge Video 3.1 FREE — semantic core for one-file master analysis."""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
import hashlib, json, math, os, re, time

ALGORITHM_VERSION="3.1 FREE"
ALGORITHM_REVISION="3.1-free-master-analysis-r4"
MASTER_SCHEMA_VERSION=2
ALLOWED_STANDARD_RUNNER_LABELS={"ubuntu-latest","ubuntu-24.04","ubuntu-22.04"}
PAID_ENDPOINT_MARKERS=("api.openai.com","generativelanguage.googleapis.com","aiplatform.googleapis.com","assemblyai.com","deepgram.com","api.runpod")
BRIDGE_TERMS=["бридж","сдача","раздача","дилер","торговля","заявка","контракт","уровень контракта","козырь","без козыря","без козырей","бк","взятка","первый ход","разыгрывающий","вистующий","вистующие","защитник","защита","болван","стол","мажор","минор","онерные пункты","онеры","баланс","гейм","шлем","фит","расклад","пас","контра","реконтра","открытие","ответ","ребид","интервенция","призывная контра","конкурентная торговля","стейман","трансфер","кюбид","импас","экспас","форсинг","биддинг-бокс","оверколл","выход","снос","убитка"]
TEACHER_CUES=["правильно","неправильно","ошибка","почему","нужно","надо","обрати внимание","обратите внимание","запомни","запомните","повтори","давай еще раз","давайте еще раз","лучше","смотри","смотрите","важно","идея","правило","представьте","например"]
DECISION_CUES=["пас","контра","реконтра","открытие","ответ","ребид","контракт","первый ход","импас","взятка","козыр","бк","фит","кюбид","интервенция","разыгрывать","ходить","снести","взять","положить"]
ERROR_CUES=["ошибка","неправильно","неверно","зря","не надо было","почему ты","почему вы","не увидел","не заметил","не посчитал","забыл"]
QUESTION_CUES=["почему","как","что будет","что делать","а если","можно ли","зачем"]
ANALOGY_CUES=["как будто","представьте","представь","это как","например"]
FACT="FACT"; INFERENCE="INFERENCE"; RECOMMENDATION="RECOMMENDATION"; UNCERTAIN="UNCERTAIN"

def _norm(x): return re.sub(r"\s+"," ",(x or "").strip())
def words(x): return re.findall(r"[A-Za-zА-Яа-яЁё0-9]+",(x or "").lower())
def cue_hits(text,cues):
    low=(text or "").lower(); return [x for x in cues if x in low]
def bridge_term_hits(text):
    low=(text or "").lower(); out=set()
    for term in BRIDGE_TERMS:
        if re.search(r"(?<![A-Za-zА-Яа-яЁё0-9])"+re.escape(term)+r"(?![A-Za-zА-Яа-яЁё0-9])",low): out.add(term)
    return sorted(out)
def stable_entity_id(kind,job_id,seed): return f"{kind.lower()}_{hashlib.sha256(f'{kind.lower()}|{job_id}|{seed}'.encode()).hexdigest()[:16]}"
def stable_job_id(source_kind,source_id): return hashlib.sha256(f"bridge-video|{source_kind.strip().lower()}|{source_id.strip()}".encode()).hexdigest()[:32]
def legacy_job_id(source_kind,source_id,revision): return hashlib.sha256(f"{revision}|{source_kind.strip().lower()}|{source_id.strip()}".encode()).hexdigest()[:32]

@dataclass
class FreeGuardResult:
    ok: bool; stage: str; reasons: list[str]
def free_guard(*,repository_private,runner_label,larger_runner=False,paid_cloud_resources=False,paid_ai_endpoints=None,billing_fallback=False):
    reasons=[]
    if repository_private: reasons.append("repository is private")
    if runner_label not in ALLOWED_STANDARD_RUNNER_LABELS: reasons.append("runner label is not an approved standard runner")
    if larger_runner: reasons.append("larger runner is forbidden")
    if paid_cloud_resources: reasons.append("paid cloud resource flag is true")
    if billing_fallback: reasons.append("billing fallback is forbidden")
    for ep in paid_ai_endpoints or []:
        if any(x in ep.lower() for x in PAID_ENDPOINT_MARKERS): reasons.append("paid/tariffed AI endpoint forbidden")
    return FreeGuardResult(not reasons,"QUEUED" if not reasons else "BLOCKED_FREE_GUARD",reasons)

def autonomous_qc_indices(block_count,term_rich_indices=()):
    if block_count<=0:return []
    minimum=min(block_count,max(3,math.ceil(block_count*.10))); req={0,block_count//2,block_count-1}
    for i in range(minimum): req.add(round(i*(block_count-1)/max(1,minimum-1)))
    req.update(int(x) for x in term_rich_indices if 0<=int(x)<block_count)
    return sorted(req)
def transcript_status(auto_qc_pass,unreliable_blocks,human_checked=False,primary_source="ASR"):
    if not auto_qc_pass:return "QC_FAILED"
    if human_checked:return "HUMAN-CHECKED TRANSCRIPT"
    return f"AUTO-VERIFIED {primary_source.upper()} TRANSCRIPT"+(" WITH WARNINGS" if unreliable_blocks else "")
def visual_pass1_plan(duration_seconds,scene_change_times,phash_change_times):
    anchors={0.0,max(0.0,duration_seconds-.05)}; t=60.0
    while t<duration_seconds: anchors.add(round(t,3)); t+=60
    for seq in (scene_change_times,phash_change_times):
        for x in seq:
            if 0<=float(x)<duration_seconds: anchors.add(round(float(x),3))
    return {"status":"VISUAL_PASS_1_COMPLETE","anchors":sorted(anchors),"coverage_start":0.0,"coverage_end":duration_seconds,"semantic_complete":False}
def visual_pass2_requirements(pass1,critical_speech_times):
    targets=set(pass1.get("anchors") or []); targets.update(round(float(x),3) for x in critical_speech_times)
    return {"status":"VISUAL_PASS_2_PENDING","targets":sorted(targets),"requires_gap_check":True,"semantic_complete":False}

def _episode_kind(text):
    low=(text or "").lower()
    if cue_hits(low,ERROR_CUES): return "ошибка/коррекция"
    if any(x in low for x in ("торгов","заявк","контр","пас","открыт","ребид","фит")): return "торговля"
    if any(x in low for x in ("первый ход","защит","вист","снос")): return "защита"
    if any(x in low for x in ("разыг","импас","взятк","козыр")): return "розыгрыш"
    if cue_hits(low,QUESTION_CUES): return "вопрос/обсуждение"
    if cue_hits(low,TEACHER_CUES): return "объяснение"
    return "методический эпизод"
def semantic_episode_plan(segments,job_id=""):
    out=[]; active=None
    for s in sorted(segments,key=lambda x:(float(x.get("start",0)),float(x.get("end",0)))):
        text=_norm(s.get("text","")); terms=bridge_term_hits(text); teacher=cue_hits(text,TEACHER_CUES); decisions=cue_hits(text,DECISION_CUES); errors=cue_hits(text,ERROR_CUES); questions=cue_hits(text,QUESTION_CUES)
        if not text or not (terms or teacher or decisions or errors or questions): continue
        sig=set(terms+decisions); start=float(s.get("start",0)); end=float(s.get("end",start)); merge=False
        if active:
            gap=start-active["end"]; overlap=sig & set(active["signature"]); same=bool(s.get("speaker")) and s.get("speaker")==active.get("speaker")
            merge=gap<=12 and bool(overlap or same or (teacher and active.get("teacher_cues")))
        if merge:
            active["end"]=max(active["end"],end); active["segment_ids"].append(s.get("segment_id")); active["texts"].append(text)
            for k,v in (("terms",terms),("teacher_cues",teacher),("decision_cues",decisions),("error_cues",errors),("question_cues",questions),("signature",list(sig))): active[k]=sorted(set(active[k])|set(v))
            active["unreliable"]|=bool(s.get("unreliable"))
        else:
            if active: out.append(active)
            active={"start":start,"end":end,"segment_ids":[s.get("segment_id")],"texts":[text],"terms":terms,"teacher_cues":teacher,"decision_cues":decisions,"error_cues":errors,"question_cues":questions,"signature":sorted(sig),"speaker":s.get("speaker"),"unreliable":bool(s.get("unreliable"))}
    if active: out.append(active)
    for i,e in enumerate(out,1):
        joined=_norm(" ".join(e.pop("texts",[]))); seed=f"{e['start']:.3f}|{e['end']:.3f}|{joined[:160]}"
        e.update({"episode_id":stable_entity_id("episode",job_id or "unknown",seed),"ordinal":i,"type":_episode_kind(joined),"summary_text":joined[:1200],"confidence":"low" if e["unreliable"] else "medium","evidence":[x for x in e["segment_ids"] if x],"facts":[],"inferences":[],"recommendations":[],"warnings":["ASR/primary transcript fragment marked unreliable"] if e["unreliable"] else []})
    return out

def _overlap(a,b):
    aa,bb=set(words(a)),set(words(b)); return len(aa&bb)/max(1,len(aa|bb)) if aa and bb else 0.0
def course_link_candidates(episodes,course_text,source_id=""):
    paras=[_norm(x) for x in (course_text or "").splitlines() if len(_norm(x))>=30]; links=[]
    for e in episodes:
        scored=sorted(((_overlap(e.get("summary_text",""),p),p) for p in paras),reverse=True); score,best=scored[0] if scored else (0.0,"")
        status="вероятное тематическое совпадение" if score>=.08 else "слабое тематическое совпадение" if score>=.035 else "не найдено"
        links.append({"episode_id":e["episode_id"],"source_id":source_id,"status":status,"score":round(score,3),"canonical_excerpt":best[:800] if status!="не найдено" else None,"statement_type":INFERENCE}); e["course_link_status"]=status; e["course_link_score"]=round(score,3)
    return links
def attach_visual_evidence(episodes,screenshots,max_per_episode=2):
    for e in episodes:
        mid=(float(e["start"])+float(e["end"]))/2; ranked=sorted(screenshots or [],key=lambda s:abs(float(s.get("time",0))-mid)); selected=[x for x in ranked if float(e["start"])-20<=float(x.get("time",0))<=float(e["end"])+20][:max_per_episode]; e["visual_evidence"]=[x.get("evidence_id") or x.get("sha256") for x in selected]
    return episodes

def _derive(episodes,job_id):
    errors=[]; teacher=[]; best=[]; counts=Counter()
    for e in episodes:
        text=e.get("summary_text",""); low=text.lower(); terms=e.get("terms",[])
        if e.get("error_cues"):
            cat="требует контекстной диагностики"
            if any(x in low for x in ("не знал","не знаете","не знаешь","забыл правило")): cat="возможное отсутствие знания правила"
            elif any(x in low for x in ("не увидел","не заметил","не распознал")): cat="возможное нераспознавание ситуации"
            elif any(x in low for x in ("почему","рассужд","логик")): cat="возможная ошибка рассуждения"
            errors.append({"error_id":stable_entity_id("error",job_id,e["episode_id"]),"episode_id":e["episode_id"],"category":cat,"severity":"контекстная оценка требуется","topics":terms,"statement_type":INFERENCE,"evidence":e.get("evidence",[]),"note":"Языковой маркер ошибки не доказывает, кто именно ошибся; требуется контекст."}); counts.update(terms)
        if e.get("teacher_cues"):
            teacher.append({"observation_id":stable_entity_id("teacher",job_id,e["episode_id"]),"episode_id":e["episode_id"],"method":"объяснение/коррекция/вопрос — требуется уточнение по контексту","statement_type":INFERENCE,"evidence":e.get("evidence",[]),"note":"Качество оценивается с учётом реакции учеников, уровня и возможной отложенной коррекции."})
        if e.get("teacher_cues") and cue_hits(text,ANALOGY_CUES):
            best.append({"explanation_id":stable_entity_id("explanation",job_id,e["episode_id"]),"episode_id":e["episode_id"],"topics":terms,"candidate_reason":"обнаружен пример/аналогия или явное пояснение","statement_type":INFERENCE,"status":"кандидат — требуется оценка по реакции учеников","evidence":e.get("evidence",[])})
    rec=[]
    for topic,count in counts.most_common():
        if count>=2: rec.append({"recommendation_id":stable_entity_id("recommendation",job_id,f"repeat|{topic}"),"scope":"group_or_lesson","topic":topic,"text":f"Повторно проверить тему «{topic}»: признаки затруднений встретились в {count} смысловых эпизодах.","statement_type":RECOMMENDATION,"basis":"повторяемость маркеров затруднений; итоговая оценка зависит от уровня и контекста"})
    return errors,teacher,best,rec

def master_analysis_payload(*,job_id,passport,transcript,transcript_qc,visual_qc,episodes,course_links=None,screenshots=None,participants=None,methodology_source=None,extra_warnings=None):
    errors,teacher,best,rec=_derive(episodes,job_id); gaps=[]
    for e in episodes:
        if e.get("question_cues") and e.get("course_link_status")=="не найдено": gaps.append({"gap_id":stable_entity_id("gap",job_id,e["episode_id"]),"episode_id":e["episode_id"],"question_context":e.get("summary_text","")[:800],"status":"кандидат в пробел канона","statement_type":INFERENCE,"next_action":"сопоставить с базой школы; при отсутствии ответа собрать внешние варианты и вынести подготовленный вопрос преподавателю"})
    tc=Counter(t for e in episodes for t in e.get("terms",[])); links=course_links or []
    quality={"transcript_segments":len(transcript),"semantic_episodes":len(episodes),"visual_evidence_items":len(screenshots or []),"error_candidates":len(errors),"teacher_observations":len(teacher),"best_explanation_candidates":len(best),"canon_links_found":sum(x.get("status")!="не найдено" for x in links),"knowledge_gap_candidates":len(gaps),"semantic_coverage_warning":len(episodes)==0}
    return {"schema":"bridge-video-master-analysis","schemaVersion":MASTER_SCHEMA_VERSION,"algorithmVersion":ALGORITHM_VERSION,"algorithmRevision":ALGORITHM_REVISION,"job_id":job_id,"createdAt":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"principles":{"source_immutable":True,"decision_quality_independent_of_single_deal_result":True,"beginner_simplicity_first":True,"advanced_players_receive_exceptions_and_alternatives":True,"error_can_be_deliberately_left_until_end_for_pedagogical_effect":True,"topics_are_revisited_when_errors_return":True,"facts_inferences_recommendations_separated":True,"unknown_is_not_invented":True},"source":passport,"participants":participants or [],"session_summary":{"episode_count":len(episodes),"topics":[x for x,_ in tc.most_common()],"top_topic_counts":tc.most_common(20)},"timeline":[{"episode_id":e["episode_id"],"ordinal":e.get("ordinal"),"start":e["start"],"end":e["end"],"type":e.get("type"),"topics":e.get("terms",[]),"confidence":e.get("confidence")} for e in episodes],"episodes":episodes,"deals":[],"decisions":[],"errors":errors,"strengths":[],"teacher_analysis":teacher,"best_explanations":best,"canon_links":links,"knowledge_gaps":gaps,"student_profile_updates":[],"group_profile_update":{"status":"requires stable participant identification across lessons","statement_type":UNCERTAIN},"recommendations":rec,"homework_candidates":[],"next_lesson_candidates":rec[:],"transcript":transcript,"screenshots":screenshots or [],"technical_qc":{"transcript":transcript_qc or {},"visual":visual_qc or {},"methodology_source":methodology_source or {}},"content_quality":quality,"warnings":extra_warnings or []}
def sanitize_public_log(data):
    allowed={"job_id","stage","attempt","exit_code","size_bytes","sha256","duration_seconds","unit_index","error_class","qc_block","qc_ok","qc_retry","qc_similarity","qc_failed","qc_total","qc_anchor_passed","episode_count","transcript_source","master_embedded","content_warning_count"}; return {k:data[k] for k in allowed if k in data}
def main():
    g=free_guard(repository_private=os.environ.get("BRIDGE_REPOSITORY_PRIVATE","true").lower()=="true",runner_label=os.environ.get("BRIDGE_RUNNER_LABEL",""),larger_runner=os.environ.get("BRIDGE_LARGER_RUNNER","false").lower()=="true",paid_cloud_resources=os.environ.get("BRIDGE_PAID_CLOUD","false").lower()=="true",billing_fallback=os.environ.get("BRIDGE_BILLING_FALLBACK","false").lower()=="true")
    print(json.dumps(sanitize_public_log({"job_id":os.environ.get("BRIDGE_JOB_ID",""),"stage":g.stage,"exit_code":0 if g.ok else 78})))
    if not g.ok: raise SystemExit(78)
if __name__=="__main__": main()
