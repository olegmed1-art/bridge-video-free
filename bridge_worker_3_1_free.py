#!/usr/bin/env python3
"""Bridge Video 3.1 FREE — semantic core for one-file master analysis."""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
import hashlib, json, math, os, re, time

ALGORITHM_VERSION="3.1 FREE"
ALGORITHM_REVISION="3.1-free-r25"
MASTER_SCHEMA_VERSION=3
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
def _repair_mojibake(text):
    """Repair the common UTF-8-as-Latin-1/CP1252 corruption, conservatively."""
    value=text or ""
    if not any(marker in value for marker in ("Ð", "Ñ", "Р°", "СЂ")):
        return value
    for codec in ("latin1", "cp1252"):
        try:
            repaired=value.encode(codec).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if repaired.count("Ð") + repaired.count("Ñ") < value.count("Ð") + value.count("Ñ"):
            return repaired
    return value
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

def _speaker_role_hint(text):
    low=(text or "").lower()
    student=any(x in low for x in ("не знаю","не понимаю","я думаю","я вижу","я посчитал","я посчитала","мне кажется","забыла","забыл","я не "))
    teacher=any(x in low for x in ("как ты думаешь","как вы думаете","почему ты","почему вы","правильно","абсолютно верно","обрати внимание","обратите внимание","запомни","давай посмотрим"))
    if teacher and not student:return "teacher"
    if student and not teacher:return "student"
    return "unknown"

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
        text=_norm(s.get("text","")); terms=bridge_term_hits(text); teacher=cue_hits(text,TEACHER_CUES); decisions=cue_hits(text,DECISION_CUES); errors=cue_hits(text,ERROR_CUES); questions=cue_hits(text,QUESTION_CUES); role_hint=_speaker_role_hint(text)
        if not text or not (terms or teacher or decisions or errors or questions): continue
        sig=set(terms+decisions); start=float(s.get("start",0)); end=float(s.get("end",start)); merge=False
        if active:
            gap=start-active["end"]; overlap=sig & set(active["signature"]); same=bool(s.get("speaker")) and s.get("speaker")==active.get("speaker")
            role_change=role_hint!="unknown" and active.get("role_hint")!="unknown" and role_hint!=active.get("role_hint")
            merge=not role_change and gap<=12 and bool(overlap or same or (teacher and active.get("teacher_cues")))
        if merge:
            active["end"]=max(active["end"],end); active["segment_ids"].append(s.get("segment_id")); active["texts"].append(text)
            for k,v in (("terms",terms),("teacher_cues",teacher),("decision_cues",decisions),("error_cues",errors),("question_cues",questions),("signature",list(sig))): active[k]=sorted(set(active[k])|set(v))
            active["unreliable"]|=bool(s.get("unreliable"))
        else:
            if active: out.append(active)
            active={"start":start,"end":end,"segment_ids":[s.get("segment_id")],"texts":[text],"terms":terms,"teacher_cues":teacher,"decision_cues":decisions,"error_cues":errors,"question_cues":questions,"signature":sorted(sig),"speaker":s.get("speaker"),"role_hint":role_hint,"unreliable":bool(s.get("unreliable"))}
    if active: out.append(active)
    for i,e in enumerate(out,1):
        joined=_norm(" ".join(e.pop("texts",[]))); seed=f"{e['start']:.3f}|{e['end']:.3f}|{joined[:160]}"
        e.update({"episode_id":stable_entity_id("episode",job_id or "unknown",seed),"ordinal":i,"type":_episode_kind(joined),"summary_text":joined[:1200],"confidence":"low" if e["unreliable"] else "medium","evidence":[x for x in e["segment_ids"] if x],"facts":[],"inferences":[],"recommendations":[],"warnings":["ASR/primary transcript fragment marked unreliable"] if e["unreliable"] else []})
    return out

def _overlap(a,b):
    aa,bb=set(words(a)),set(words(b)); return len(aa&bb)/max(1,len(aa|bb)) if aa and bb else 0.0
def course_link_candidates(episodes,course_text,source_id=""):
    course_text=_repair_mojibake(course_text)
    paras=[_norm(x) for x in (course_text or "").splitlines() if len(_norm(x))>=30]; links=[]
    for e in episodes:
        scored=sorted(((_overlap(e.get("summary_text",""),p),p) for p in paras),reverse=True); score,best=scored[0] if scored else (0.0,"")
        status="вероятное тематическое совпадение" if score>=.08 else "слабое тематическое совпадение" if score>=.035 else "не найдено"
        links.append({"episode_id":e["episode_id"],"source_id":source_id,"status":status,"score":round(score,3),"canonical_excerpt":best[:800] if status!="не найдено" else None,"statement_type":INFERENCE}); e["course_link_status"]=status; e["course_link_score"]=round(score,3)
    return links
def _important_episode(e):
    return bool(e.get("error_cues") or (e.get("teacher_cues") and e.get("question_cues")) or
                (e.get("decision_cues") and e.get("question_cues")))

def attach_visual_evidence(episodes,screenshots,max_per_episode=3,max_total=30):
    """Select event-driven BEFORE/ACTION/AFTER evidence with global de-duplication."""
    available=list(screenshots or [])
    used=set(); total=0
    for e in episodes:
        e["visual_evidence"]=[]
        e["visual_sequence"]=[]
        if not _important_episode(e) or total>=max_total:
            continue
        anchors=[("BEFORE",max(0.0,float(e["start"])-3.0)),("ACTION",(float(e["start"])+float(e["end"]))/2),
                 ("AFTER",float(e["end"])+3.0)]
        for role,target in anchors[:max_per_episode]:
            candidates=sorted(available,key=lambda s:abs(float(s.get("time",0))-target))
            shot=next((x for x in candidates if (x.get("evidence_id") or x.get("sha256")) not in used and
                       abs(float(x.get("time",0))-target)<=30),None)
            if shot is None or total>=max_total: continue
            evid=shot.get("evidence_id") or shot.get("sha256");used.add(evid);total+=1
            e["visual_evidence"].append(evid)
            e["visual_sequence"].append({"role":role,"evidence_id":evid,"time":shot.get("time"),
                "caption":f"{role}: состояние стола около учебного события; используется только как визуальное доказательство вместе с репликами."})
    return episodes

def _role(text):
    return _speaker_role_hint(text)

def _intervention_type(text):
    low=(text or "").lower()
    if any(x in low for x in ("почему","как ты думаешь","что будем","что делать")):return "наводящий вопрос"
    if any(x in low for x in ("правильно","абсолютно верно")):return "подтверждение"
    if any(x in low for x in ("неправильно","ошибка","не надо")):return "коррекция"
    if any(x in low for x in ("правило","запомни","обрати внимание","идея")):return "объяснение правила"
    return "объяснение"

def learning_interaction_cycles(episodes,job_id):
    """Build evidence-linked teaching cycles without inventing speaker identities."""
    cycles=[]
    for i,e in enumerate(episodes):
        if not _important_episode(e):continue
        before=episodes[i-1] if i else None; after=episodes[i+1] if i+1<len(episodes) else None
        role=_role(e.get("summary_text","")); student_event=None; intervention=None; response=None
        if role=="teacher" and after and _role(after.get("summary_text",""))=="student":
            student_event=after
            candidate=episodes[i+2] if i+2<len(episodes) else None
            if candidate and _role(candidate.get("summary_text",""))=="teacher":
                intervention=candidate
                response=episodes[i+3] if i+3<len(episodes) and _role(episodes[i+3].get("summary_text","")) in ("student","unknown") else None
        elif role=="student":
            student_event=e
            if after and _role(after.get("summary_text",""))=="teacher":
                intervention=after
                response=episodes[i+2] if i+2<len(episodes) else None
        else:
            intervention=e if e.get("teacher_cues") else None
            response=after if intervention and after and float(after["start"])-float(e["end"])<=45 else None
        autonomy="не установлена"
        outcome="требует проверки"
        if response:
            rlow=response.get("summary_text","").lower()
            if any(x in rlow for x in ("поняла","понял","правильно","тогда","значит")):
                autonomy="после вмешательства преподавателя";outcome="есть непосредственный признак понимания"
            elif response.get("error_cues"):
                outcome="затруднение сохраняется"
        cycles.append({"cycle_id":stable_entity_id("cycle",job_id,e["episode_id"]),
            "focus_episode_id":e["episode_id"],"task_or_trigger":(before or e).get("summary_text","")[:600],
            "student_action":student_event.get("summary_text","")[:600] if student_event else None,
            "teacher_intervention":intervention.get("summary_text","")[:600] if intervention else None,
            "intervention_type":_intervention_type(intervention.get("summary_text","")) if intervention else None,
            "student_response":response.get("summary_text","")[:600] if response else None,
            "outcome":outcome,"autonomy":autonomy,"transfer":"не проверен в пределах выделенного цикла",
            "confidence":"low" if e.get("unreliable") else "medium",
            "evidence":list(dict.fromkeys((before or {}).get("evidence",[])+e.get("evidence",[])+(student_event or {}).get("evidence",[])+(intervention or {}).get("evidence",[])+(response or {}).get("evidence",[]))),
            "visual_evidence":e.get("visual_sequence",[])})
    return cycles

def _derive(episodes,job_id):
    errors=[]; teacher=[]; best=[]; strengths=[]; counts=Counter();cycles=learning_interaction_cycles(episodes,job_id)
    for e in episodes:
        text=e.get("summary_text",""); low=text.lower(); terms=e.get("terms",[])
        if e.get("error_cues"):
            cat="требует контекстной диагностики"
            if any(x in low for x in ("не знал","не знаете","не знаешь","забыл правило")): cat="возможное отсутствие знания правила"
            elif any(x in low for x in ("не увидел","не заметил","не распознал")): cat="возможное нераспознавание ситуации"
            elif any(x in low for x in ("почему","рассужд","логик")): cat="возможная ошибка рассуждения"
            errors.append({"error_id":stable_entity_id("error",job_id,e["episode_id"]),"episode_id":e["episode_id"],"category":cat,"severity":"контекстная оценка требуется","topics":terms,"statement_type":INFERENCE,"evidence":e.get("evidence",[]),"note":"Языковой маркер ошибки не доказывает, кто именно ошибся; требуется контекст."}); counts.update(terms)
        if e.get("teacher_cues"):
            teacher.append({"observation_id":stable_entity_id("teacher",job_id,e["episode_id"]),"episode_id":e["episode_id"],"method":_intervention_type(text),"statement_type":INFERENCE,"evidence":e.get("evidence",[]),"note":"Эффект оценивается только по связанной последующей реакции; без неё результат не подтверждён."})
        if e.get("teacher_cues") and cue_hits(text,ANALOGY_CUES):
            best.append({"explanation_id":stable_entity_id("explanation",job_id,e["episode_id"]),"episode_id":e["episode_id"],"topics":terms,"candidate_reason":"обнаружен пример/аналогия или явное пояснение","statement_type":INFERENCE,"status":"кандидат — требуется оценка по реакции учеников","evidence":e.get("evidence",[])})
    for i,e in enumerate(episodes[:-1]):
        nxt=episodes[i+1]; low=nxt.get("summary_text","").lower()
        if e.get("decision_cues") and any(x in low for x in ("правильно","абсолютно верно","верно")):
            strengths.append({"strength_id":stable_entity_id("strength",job_id,e["episode_id"]),"episode_id":e["episode_id"],"topics":e.get("terms",[]),"description":"Решение получило непосредственное подтверждение в следующей реплике.","autonomy":"самостоятельность требует проверки по атрибуции говорящих","statement_type":INFERENCE,"evidence":e.get("evidence",[])+nxt.get("evidence",[])})
    rec=[]
    for topic,count in counts.most_common():
        if count>=2: rec.append({"recommendation_id":stable_entity_id("recommendation",job_id,f"repeat|{topic}"),"scope":"group_or_lesson","topic":topic,"text":f"Повторно проверить тему «{topic}»: признаки затруднений встретились в {count} смысловых эпизодах.","statement_type":RECOMMENDATION,"basis":"повторяемость маркеров затруднений; итоговая оценка зависит от уровня и контекста"})
    if errors and not rec:
        rec.append({"recommendation_id":stable_entity_id("recommendation",job_id,"review-errors"),"scope":"next_lesson","topic":"подтверждённые затруднения","text":"На следующем занятии повторно проверить выделенные затруднения на новых раздачах без предварительной подсказки.","statement_type":RECOMMENDATION,"basis":"в анализе найдены кандидаты на ошибки, но тематическая классификация недостаточна"})
    if cycles and not rec:
        rec.append({"recommendation_id":stable_entity_id("recommendation",job_id,"verify-transfer"),"scope":"next_lesson","topic":"самостоятельность и перенос","text":"На следующем занятии повторить ключевые ситуации на новых раздачах без предварительной подсказки и отдельно зафиксировать самостоятельный ответ и перенос.","statement_type":RECOMMENDATION,"basis":"учебные циклы выделены, но перенос знания внутри записи не подтверждён"})
    return errors,teacher,best,strengths,cycles,rec

def _student_analysis(cycles,strengths,errors):
    observations=[]
    for cycle in cycles:
        if not cycle.get("student_action"):continue
        observations.append({"observation_id":cycle["cycle_id"].replace("cycle_","student_"),
            "learning_interaction_id":cycle["cycle_id"],"task":cycle.get("task_or_trigger"),
            "student_action":cycle.get("student_action"),"support_state":cycle.get("autonomy"),
            "result":cycle.get("outcome"),"transfer":cycle.get("transfer"),
            "confidence":cycle.get("confidence"),"statement_type":INFERENCE,
            "evidence":cycle.get("evidence",[])})
    return {"observations":observations,"strengths":strengths,"difficulties":errors,
        "scope":"single_lesson","cross_lesson_pattern":"not evaluated without prior compatible lessons"}

def master_analysis_payload(*,job_id,passport,transcript,transcript_qc,visual_qc,episodes,course_links=None,screenshots=None,participants=None,methodology_source=None,extra_warnings=None):
    errors,teacher,best,strengths,cycles,rec=_derive(episodes,job_id); student=_student_analysis(cycles,strengths,errors); gaps=[]
    for e in episodes:
        if e.get("question_cues") and e.get("terms") and e.get("course_link_status")=="не найдено": gaps.append({"gap_id":stable_entity_id("gap",job_id,e["episode_id"]),"episode_id":e["episode_id"],"question_context":e.get("summary_text","")[:800],"status":"кандидат в пробел канона","statement_type":INFERENCE,"next_action":"сопоставить с базой школы; при отсутствии ответа вынести подготовленный вопрос преподавателю"})
    tc=Counter(t for e in episodes for t in e.get("terms",[])); links=course_links or []
    selected_visual=sum(len(e.get("visual_evidence",[])) for e in episodes)
    quality={"transcript_segments":len(transcript),"semantic_episodes":len(episodes),"visual_evidence_items":len(screenshots or []),"selected_report_visuals":selected_visual,"error_candidates":len(errors),"teacher_observations":len(teacher),"learning_interaction_cycles":len(cycles),"strength_candidates":len(strengths),"best_explanation_candidates":len(best),"canon_links_found":sum(x.get("status")!="не найдено" for x in links),"knowledge_gap_candidates":len(gaps),"semantic_coverage_warning":len(episodes)==0}
    return {"schema":"bridge-video-master-analysis","schemaVersion":MASTER_SCHEMA_VERSION,"algorithmVersion":ALGORITHM_VERSION,"algorithmRevision":ALGORITHM_REVISION,"job_id":job_id,"createdAt":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"principles":{"source_immutable":True,"decision_quality_independent_of_single_deal_result":True,"beginner_simplicity_first":True,"advanced_players_receive_exceptions_and_alternatives":True,"error_can_be_deliberately_left_until_end_for_pedagogical_effect":True,"topics_are_revisited_when_errors_return":True,"facts_inferences_recommendations_separated":True,"unknown_is_not_invented":True},"source":passport,"participants":participants or [],"session_summary":{"episode_count":len(episodes),"topics":[x for x,_ in tc.most_common()],"top_topic_counts":tc.most_common(20)},"timeline":[{"episode_id":e["episode_id"],"ordinal":e.get("ordinal"),"start":e["start"],"end":e["end"],"type":e.get("type"),"topics":e.get("terms",[]),"confidence":e.get("confidence")} for e in episodes],"episodes":episodes,"learning_interactions":cycles,"student_analysis":student,"deals":[],"decisions":[],"errors":errors,"strengths":strengths,"teacher_analysis":teacher,"best_explanations":best,"canon_links":links,"knowledge_gaps":gaps,"student_profile_updates":[],"group_profile_update":{"status":"requires stable participant identification across lessons","statement_type":UNCERTAIN},"recommendations":rec,"homework_candidates":[],"next_lesson_candidates":rec[:],"transcript":transcript,"screenshots":screenshots or [],"technical_qc":{"transcript":transcript_qc or {},"visual":visual_qc or {},"methodology_source":methodology_source or {}},"content_quality":quality,"warnings":extra_warnings or []}

def validate_r24_master(master):
    """Fail closed when an r24-labelled PDF lacks its mandatory pedagogical layer."""
    issues=[]
    episodes=master.get("episodes") or []
    important=sum(_important_episode(e) for e in episodes)
    cycles=master.get("learning_interactions") or []
    if important and not cycles:issues.append("missing-learning-interactions")
    student=master.get("student_analysis") or {}
    if important and not student.get("observations"):issues.append("missing-student-analysis")
    if master.get("errors") and not master.get("recommendations"):issues.append("empty-recommendations-with-errors")
    methods=[x.get("method","") for x in master.get("teacher_analysis") or []]
    if any("требуется уточнение по контексту" in x for x in methods):issues.append("placeholder-teacher-analysis")
    refs=[x for e in episodes for x in e.get("visual_evidence",[])]
    if len(refs)>30:issues.append("too-many-report-screenshots")
    if len(refs)!=len(set(refs)):issues.append("duplicate-report-screenshots")
    if any(any(marker in (x.get("canonical_excerpt") or "") for marker in ("Ð","Ñ")) for x in master.get("canon_links") or []):issues.append("canonical-text-mojibake")
    if master.get("algorithmRevision")!=ALGORITHM_REVISION:issues.append("version-conformity-failure")
    return {"ok":not issues,"issues":issues,"importantEpisodes":important,"learningCycles":len(cycles),"selectedVisuals":len(refs)}
def sanitize_public_log(data):
    allowed={"job_id","stage","attempt","exit_code","size_bytes","sha256","duration_seconds","unit_index","error_class","qc_block","qc_ok","qc_retry","qc_similarity","qc_failed","qc_total","qc_anchor_passed","episode_count","transcript_source","master_embedded","content_warning_count"}; return {k:data[k] for k in allowed if k in data}
def main():
    g=free_guard(repository_private=os.environ.get("BRIDGE_REPOSITORY_PRIVATE","true").lower()=="true",runner_label=os.environ.get("BRIDGE_RUNNER_LABEL",""),larger_runner=os.environ.get("BRIDGE_LARGER_RUNNER","false").lower()=="true",paid_cloud_resources=os.environ.get("BRIDGE_PAID_CLOUD","false").lower()=="true",billing_fallback=os.environ.get("BRIDGE_BILLING_FALLBACK","false").lower()=="true")
    print(json.dumps(sanitize_public_log({"job_id":os.environ.get("BRIDGE_JOB_ID",""),"stage":g.stage,"exit_code":0 if g.ok else 78})))
    if not g.ok: raise SystemExit(78)
if __name__=="__main__": main()
