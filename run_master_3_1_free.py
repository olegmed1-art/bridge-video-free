#!/usr/bin/env python3
"""Production master-analysis runner for Bridge Video 3.1 FREE."""
from collections import Counter
from pathlib import Path
import hashlib, html, json, math, os, re, tempfile, time
import requests
import run_drive_3_1_free as io
from bridge_vision.deal_review_pdf import append_deal_review_pages
from bridge_worker_3_1_free import (
    ALGORITHM_VERSION, ALGORITHM_REVISION, INFERENCE, UNCERTAIN,
    autonomous_qc_indices, attach_visual_evidence, bridge_term_hits,
    course_link_candidates, legacy_job_id, master_analysis_payload,
    semantic_episode_plan, stable_entity_id, stable_job_id, validate_r24_master,
    transcript_status, visual_pass1_plan, visual_pass2_requirements,
)
DRIVE='https://www.googleapis.com/drive/v3'
PROMPT=('Спортивный бридж: сдача, раздача, дилер, торговля, заявка, контракт, мажор, минор, БК, фит, баланс, гейм, шлем, пас, контра, реконтра, открытие, ответ, ребид, интервенция, призывная контра, конкурентная торговля, Стейман, трансфер, кюбид, импас, экспас, разыгрывающий, защитник, болван, первый ход, взятка. Не добавляй неслышанные слова и не исправляй смысл по догадке.')
MODEL=None

def _words(x): return re.findall(r'[A-Za-zА-Яа-яЁё0-9]+',(x or '').lower())
def _similarity(a,b):
    aa,bb=_words(a),_words(b)
    if not aa or not bb:return 0.0
    ca,cb=Counter(aa),Counter(bb); common=sum((ca&cb).values()); p=common/len(bb); r=common/len(aa)
    return 2*p*r/(p+r) if p+r else 0.0

def asr_detail(path,strict=False,qc_retry=False):
    global MODEL
    if MODEL is None:
        from faster_whisper import WhisperModel
        MODEL=WhisperModel(os.getenv('WHISPER_MODEL','small'),device='cpu',compute_type='int8')
    kw={'language':None,'condition_on_previous_text':False,'initial_prompt':PROMPT,'beam_size':3 if strict else 5,'vad_filter':not qc_retry}
    if strict and not qc_retry: kw['vad_parameters']={'threshold':.65,'min_speech_duration_ms':300,'min_silence_duration_ms':800}
    if qc_retry: kw['beam_size']=5
    segs,info=MODEL.transcribe(str(path),**kw); out=[]
    for s in segs:
        text=(s.text or '').strip()
        if text: out.append({'start':float(s.start),'end':float(s.end),'text':text})
    return out,getattr(info,'language',None)
def asr_text(path,strict=False,qc_retry=False): return ' '.join(x['text'] for x in asr_detail(path,strict,qc_retry)[0])
def _qc_match(primary,check):
    sim=_similarity(primary,check); a=set(bridge_term_hits(primary)); b=set(bridge_term_hits(check)); return bool(check) and sim>=.35 and ((not a) or bool(a&b)),sim

def _clock(x):
    try: p=[float(y) for y in x.strip().replace(',','.').split(':')]
    except ValueError:return None
    return p[0]*3600+p[1]*60+p[2] if len(p)==3 else p[0]*60+p[1] if len(p)==2 else None
def _clean_caption(x):
    x=re.sub(r'<v\s+([^>]+)>(.*?)</v>',lambda m:f'{m.group(1)}: {m.group(2)}',x,flags=re.I|re.S); x=re.sub(r'<[^>]+>',' ',x); return html.unescape(re.sub(r'\s+',' ',x)).strip()
def parse_timed_transcript(text):
    lines=(text or '').replace('\r','').split('\n'); out=[]; i=0; rx=re.compile(r'(?P<a>\d{1,2}:\d{2}(?::\d{2})?[\.,]\d{3})\s*-->\s*(?P<b>\d{1,2}:\d{2}(?::\d{2})?[\.,]\d{3})')
    while i<len(lines):
        m=rx.search(lines[i])
        if not m:i+=1;continue
        a,b=_clock(m.group('a')),_clock(m.group('b'));i+=1;body=[]
        while i<len(lines) and lines[i].strip():
            if not lines[i].strip().isdigit():body.append(lines[i].strip())
            i+=1
        raw=_clean_caption(' '.join(body)); speaker=None
        sm=re.match(r'^([^:]{1,80}):\s+(.+)$',raw)
        if sm and not re.match(r'^\d',sm.group(1)):speaker=sm.group(1).strip();raw=sm.group(2).strip()
        if a is not None and b is not None and raw:out.append({'start':a,'end':b,'text':raw,'speaker':speaker,'unreliable':False})
    return out

def _stem(name): return re.sub(r'[^a-zа-яё0-9]+',' ',Path(name or 'video').stem.lower(),flags=re.I).strip()
def _read_text(t,f):
    if f.get('mimeType')=='application/vnd.google-apps.document':return io.export_text(t,f['id'])
    r=requests.get(DRIVE+'/files/'+f['id'],headers=io.hdr(t),params={'alt':'media'},timeout=60);r.raise_for_status();return r.text
def discover_zoom_transcript(t,parent,video_name):
    files=io.search(t,f"'{parent}' in parents and trashed=false"); vt=set(_stem(video_name).split()); cand=[]
    for f in files:
        if f.get('name')==video_name:continue
        n=(f.get('name') or '').lower(); ext=Path(n).suffix
        if ext not in {'.vtt','.srt'}:continue
        other=set(_stem(n).split()); score=5*len(vt&other)/max(1,len(vt|other)) if vt and other else 0
        if any(x in n for x in ('transcript','audio transcript','расшифров','стенограмм','субтитр')):score+=4
        score+=3
        if score>=3:cand.append((score,f))
    for score,f in sorted(cand,key=lambda x:x[0],reverse=True)[:5]:
        try: segs=parse_timed_transcript(_read_text(t,f))
        except Exception:continue
        if segs:return {'file':{'driveId':f['id'],'name':f.get('name'),'mimeType':f.get('mimeType')},'segments':segs,'score':round(score,2)}
    return None

def _dedupe(segs):
    out=[]
    for s in sorted(segs,key=lambda x:(x['start'],x['end'])):
        if out and s['start']<=out[-1]['end']+1 and _similarity(s['text'],out[-1]['text'])>=.82:
            if len(s['text'])>len(out[-1]['text']):out[-1]=s
            continue
        out.append(s)
    return out
def transcribe_asr(video,work,dur):
    segs=[];start=0.;i=0;langs=Counter()
    while start<dur:
        end=min(dur,start+300);w=work/f'b{i:03d}.wav';io.wav(video,w,start,end-start);local,lang=asr_detail(w)
        if lang:langs[lang]+=1
        if len(_words(' '.join(x['text'] for x in local)))<5 and end-start>20:local,lang=asr_detail(w,True)
        for x in local:segs.append({'start':start+x['start'],'end':min(dur,start+x['end']),'text':x['text'],'speaker':None,'unreliable':False})
        if end>=dur:break
        start=end-1.5;i+=1
    return _dedupe(segs),langs.most_common(1)[0][0] if langs else None
def _windows(segs,dur):
    out=[];start=0.;i=0
    while start<dur:
        end=min(dur,start+300);out.append({'index':i,'start':start,'end':end,'text':' '.join(s['text'] for s in segs if s['end']>start and s['start']<end)})
        if end>=dur:break
        start=end;i+=1
    return out
def qc_transcript(video,work,dur,segs):
    windows=_windows(segs,dur);rich=[x['index'] for x in windows if bridge_term_hits(x['text'])];qc=[]
    for i in autonomous_qc_indices(len(windows),rich):
        b=windows[i];w=work/f'q{i:03d}.wav';io.wav(video,w,b['start'],b['end']-b['start']);check=asr_text(w,True);ok,sim=_qc_match(b['text'],check);retry=False;rs=None
        if not ok:
            retry=True;check=asr_text(w,qc_retry=True);rok,rs=_qc_match(b['text'],check)
            if rok:ok=True;sim=max(sim,rs)
        if not ok:
            for s in segs:
                if s['end']>b['start'] and s['start']<b['end']:s['unreliable']=True
        qc.append({'block':i,'start':b['start'],'end':b['end'],'ok':ok,'similarity':round(sim,3),'retry':retry,'retrySimilarity':None if rs is None else round(rs,3)});io.safe(stage='ASR_QC',unit_index=i,qc_block=i,qc_ok=ok,qc_retry=retry,qc_similarity=round(sim,3))
    need=min(len(windows),max(3,math.ceil(len(windows)*.1))) if windows else 0;failed=sum(not x['ok'] for x in qc);allowed=max(1,math.floor(len(qc)*.2)) if qc else 0;anchors={0,len(windows)//2,max(0,len(windows)-1)} if windows else set();ar=[x for x in qc if x['block'] in anchors];ap=sum(x['ok'] for x in ar);required=max(1,len(anchors)-1) if anchors else 0;passed=bool(windows) and len(qc)>=need and failed<=allowed and ap>=required
    io.safe(stage='ASR_QC',qc_failed=failed,qc_total=len(qc),qc_anchor_passed=ap,exit_code=0 if passed else 1);return qc,passed
def assign_ids(job,segs,source):
    for i,s in enumerate(segs,1):s.update({'segment_id':stable_entity_id('segment',job,f"{source}|{s['start']:.3f}|{s['end']:.3f}|{s.get('speaker') or ''}|{s['text'][:100]}"),'ordinal':i,'source':source})
    return segs
def obtain_transcript(t,parent,name,video,work,dur,job):
    io.safe(job_id=job,stage='TRANSCRIPT_DISCOVERY',exit_code=0);z=discover_zoom_transcript(t,parent,name);warnings=[]
    if z:
        segs=assign_ids(job,z['segments'],'zoom_transcript');qc,ok=qc_transcript(video,work,dur,segs)
        if ok:return segs,{'primarySource':'zoom_transcript','sourceFile':z['file'],'status':transcript_status(True,sum(s['unreliable'] for s in segs),primary_source='Zoom'),'qc':qc,'language':None},warnings
        warnings.append('Zoom transcript found but failed autonomous audio QC; local ASR used as primary.')
    segs,lang=transcribe_asr(video,work,dur);segs=assign_ids(job,segs,'local_asr');qc,ok=qc_transcript(video,work,dur,segs)
    if not ok:raise RuntimeError('ASR_QC_FAILED')
    return segs,{'primarySource':'local_asr','sourceFile':None,'status':transcript_status(True,sum(s['unreliable'] for s in segs),primary_source='ASR'),'qc':qc,'language':lang},warnings

def visual(video,work,dur,critical,job):
    import cv2,imagehash
    from PIL import Image
    scene=[];ph=[];prev=None;prevh=None;t=0.;i=0
    while t<dur:
        f=work/f's{i:05d}.jpg';io.frame(video,t,f);im=cv2.imread(str(f))
        if im is not None:
            g=cv2.resize(cv2.cvtColor(im,cv2.COLOR_BGR2GRAY),(160,90))
            if prev is not None and float(cv2.absdiff(g,prev).mean())>=18:scene.append(round(t,3))
            h=imagehash.phash(Image.open(f))
            if prevh is not None and h-prevh>=12:ph.append(round(t,3))
            prev,prevh=g,h
        f.unlink(missing_ok=True);t+=10;i+=1
    p1=visual_pass1_plan(dur,scene,ph);req=visual_pass2_requirements(p1,critical);ev=[]
    for i,ts in enumerate(req['targets']):
        f=work/f'e{i:04d}.jpg';io.frame(video,ts,f)
        if f.exists() and f.stat().st_size:
            digest=io.sha(f);ev.append({'evidence_id':stable_entity_id('frame',job,f'{ts:.3f}|{digest}'),'time':ts,'path':str(f),'sha256':digest,'source':'video_frame'})
    p2={'status':'VISUAL_PASS_2_COMPLETE' if len(ev)==len(req['targets']) else 'VISUAL_PASS_2_FAILED','evidence':[{k:v for k,v in x.items() if k!='path'} for x in ev],'gapCheckPass':len(ev)==len(req['targets'])};return p1,p2,ev

def course_text(t):
    files=io.search(t,"trashed=false and name contains 'Курс Бридж - Конспект. Правки'")
    if not files:raise RuntimeError('BLOCKED_METHOD_SOURCE_MISSING')
    files.sort(key=lambda f:('каноничес' not in (f.get('name') or '').lower(),f.get('name') or ''));f=files[0];return _read_text(t,f),f['id'],f.get('name')
def derive_deals_decisions(episodes,job):
    deals=[];dec=[]
    for e in episodes:
        if e.get('type') in {'торговля','розыгрыш','защита','ошибка/коррекция'}:deals.append({'deal_id':stable_entity_id('deal',job,e['episode_id']),'episode_id':e['episode_id'],'status':'candidate','hands':{'N':None,'E':None,'S':None,'W':None},'auction':None,'contract':None,'declarer':None,'opening_lead':None,'result':None,'reconstruction_rule':'UNKNOWN unless explicitly recoverable from transcript/visual evidence','statement_type':UNCERTAIN,'evidence':e.get('evidence',[])+e.get('visual_evidence',[])})
        if e.get('decision_cues'):dec.append({'decision_id':stable_entity_id('decision',job,e['episode_id']),'episode_id':e['episode_id'],'actor':e.get('speaker'),'observed_context':e.get('summary_text','')[:1000],'decision_cues':e.get('decision_cues',[]),'reasoning':None,'decision_quality':'not rated without sufficient context','single_deal_result_must_not_determine_quality':True,'statement_type':INFERENCE,'evidence':e.get('evidence',[])})
    return deals,dec

def _tm(s):
    s=max(0,int(float(s or 0)));return f'{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}'
def _safe_stem(name):
    stem=Path(name or 'video').stem.strip() or 'video';return ''.join('_' if ord(c)<32 or c in '/\\' else c for c in stem)[:120]
def pdf_report(out,master,shots):
    from reportlab.platypus import SimpleDocTemplate,Paragraph,PageBreak,Image as RImage,KeepTogether
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.units import mm
    from xml.sax.saxutils import escape
    from PIL import Image
    pdfmetrics.registerFont(TTFont('DV','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'));pdfmetrics.registerFont(TTFont('DVB','/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
    body=ParagraphStyle('b',fontName='DV',fontSize=8.5,leading=11,spaceAfter=4);h1=ParagraphStyle('h1',fontName='DVB',fontSize=15,leading=18,spaceAfter=8);h2=ParagraphStyle('h2',fontName='DVB',fontSize=11,leading=14,spaceBefore=8,spaceAfter=5);h3=ParagraphStyle('h3',fontName='DVB',fontSize=9.5,leading=12,spaceBefore=5,spaceAfter=3);small=ParagraphStyle('s',fontName='DV',fontSize=7,leading=9,spaceAfter=2)
    doc=SimpleDocTemplate(str(out),pagesize=A4,leftMargin=13*mm,rightMargin=13*mm,topMargin=13*mm,bottomMargin=13*mm);st=[Paragraph('Мастер-анализ видеозаписи занятия — 3.1 FREE',h1)];src=master['source']
    for x in [f"Оригинал: {src.get('name')}",f"Длительность: {_tm(src.get('durationSeconds'))}; размер: {src.get('sizeBytes')} байт",f"Drive ID: {src.get('driveId')}; SHA-256: {src.get('sha256')}",f"Алгоритм: {master['algorithmVersion']} / {master['algorithmRevision']}"]:st.append(Paragraph(escape(str(x)),body))
    st.append(Paragraph('1. Краткая карта занятия',h2));sm=master.get('session_summary',{});st.append(Paragraph(escape(f"Смысловых эпизодов: {sm.get('episode_count',0)}. Темы: {', '.join(sm.get('topics',[])[:30]) or 'не определены'}."),body));tq=master.get('technical_qc',{}).get('transcript',{});st.append(Paragraph(escape(f"Транскрипт: {tq.get('primarySource')}; статус: {tq.get('status')}."),body))
    for w in master.get('warnings',[]):st.append(Paragraph(escape('Предупреждение: '+str(w)),body))
    st.append(Paragraph('2. Карта ключевых учебных событий',h2))
    important_ids={x.get('focus_episode_id') for x in master.get('learning_interactions',[])}
    timeline=[x for x in master.get('timeline',[]) if x.get('episode_id') in important_ids]
    for x in timeline:st.append(Paragraph(escape(f"{_tm(x['start'])}–{_tm(x['end'])} — {x.get('type')}; темы: {', '.join(x.get('topics',[])) or '—'}; confidence: {x.get('confidence')}"),body))
    if not timeline:st.append(Paragraph('Ключевые учебные события автоматически не подтверждены.',body))
    st.append(Paragraph('3. Анализ действий ученика',h2))
    observations=master.get('student_analysis',{}).get('observations',[])
    if not observations:st.append(Paragraph('Персональные наблюдения не подтверждены надёжной атрибуцией.',body))
    for x in observations:st.append(Paragraph(escape(f"Задача: {x.get('task')}; действие: {x.get('student_action')}; помощь: {x.get('support_state')}; результат: {x.get('result')}; перенос: {x.get('transfer')}."),body))
    st.append(Paragraph('4. Учебные циклы: действие — вмешательство — результат',h2))
    cycles=list(master.get('learning_interactions',[]) or [])
    quality=master.get('content_quality',{}) or {}
    no_speaker_labels=quality.get('speaker_labels_present') is False or quality.get('actor_attribution_status')=='unavailable_without_speaker_labels'
    empty_actor_cycles=[
        x for x in cycles
        if not x.get('student_action') and not x.get('teacher_intervention') and not x.get('student_response')
    ]
    if cycles and no_speaker_labels and len(empty_actor_cycles)==len(cycles):
        st.append(Paragraph(escape(
            f"Надёжных меток говорящих нет. Найдено {len(cycles)} возможных учебных ситуаций, "
            "но действия ученика, вмешательства преподавателя и результат нельзя честно разделить по ролям. "
            "Повторяющиеся пустые карточки не печатаются; все исходные кандидаты без сокращений сохранены "
            "во встроенном master_analysis.json."
        ),body))
    elif not cycles:
        st.append(Paragraph('Подтверждённые учебные циклы автоматически не выделены.',body))
    else:
        for x in cycles:
            st.append(Paragraph(escape(f"Задача/ситуация: {x.get('task_or_trigger') or 'не установлена'}"),body))
            st.append(Paragraph(escape(f"Действие ученика: {x.get('student_action') or 'не установлено'}"),body))
            st.append(Paragraph(escape(f"Вмешательство преподавателя: {x.get('intervention_type') or 'не установлено'} — {x.get('teacher_intervention') or ''}"),body))
            st.append(Paragraph(escape(f"Реакция и результат: {x.get('student_response') or 'не установлена'}; {x.get('outcome')}; самостоятельность: {x.get('autonomy')}; перенос: {x.get('transfer')}."),small))
    smap={x['evidence_id']:x for x in shots};st+=[PageBreak(),Paragraph('5. Доказательные эпизоды',h2)]
    selected=[e for e in master.get('episodes',[]) if e.get('episode_id') in important_ids or e.get('visual_evidence')]
    for e in selected:
        parts=[Paragraph(escape(f"Эпизод {e.get('ordinal')}: {_tm(e['start'])}–{_tm(e['end'])} — {e.get('type')}"),h3),Paragraph(escape(e.get('summary_text','') or '[нет текста]'),body),Paragraph(escape(f"Темы: {', '.join(e.get('terms',[])) or '—'}; канон: {e.get('course_link_status','не установлено')}; confidence: {e.get('confidence')}"),small)]
        roles={x.get('evidence_id'):x for x in e.get('visual_sequence',[])}
        for evid in e.get('visual_evidence',[])[:3]:
            sh=smap.get(evid)
            if sh:
                try:
                    with Image.open(sh['path']) as im:iw,ih=im.size
                    scale=min((175*mm)/iw,(95*mm)/ih);parts.append(RImage(sh['path'],width=iw*scale,height=ih*scale));meta=roles.get(evid,{});parts.append(Paragraph(escape(f"{meta.get('role','EVIDENCE')} — {_tm(sh['time'])}: {meta.get('caption','полный необрезанный доказательный кадр')}; evidence {evid}"),small))
                except Exception:pass
        st.append(KeepTogether(parts))
    sections=[('6. Кандидаты на ошибки и затруднения','errors',lambda x:f"{x.get('category')}; темы: {', '.join(x.get('topics',[])) or '—'}. {x.get('note','')}"),('7. Подтверждённые сильные решения','strengths',lambda x:f"{x.get('description')}; темы: {', '.join(x.get('topics',[])) or '—'}; самостоятельность: {x.get('autonomy')}"),('8. Анализ работы преподавателя','teacher_analysis',lambda x:f"{x.get('method')}. {x.get('note','')}"),('9. Кандидаты в библиотеку лучших объяснений','best_explanations',lambda x:f"Темы: {', '.join(x.get('topics',[])) or '—'}; {x.get('candidate_reason')}; {x.get('status')}")]
    st.append(PageBreak())
    for title,key,fmt in sections:
        st.append(Paragraph(title,h2));items=master.get(key,[])
        if not items:st.append(Paragraph('Автоматические кандидаты не выделены; это не доказывает отсутствие соответствующих событий.',body))
        for x in items:st.append(Paragraph(escape(fmt(x)),body))
    st.append(Paragraph('10. Связь с каноном и пробелы',h2))
    printable=[x for x in (master.get('canon_links',[]) or []) if x.get('status')!='не найдено' and str(x.get('canonical_excerpt') or '').strip()]
    canon_by_excerpt={}
    for x in printable:
        key=re.sub(r'\\W+',' ',str(x.get('canonical_excerpt') or '').casefold()).strip()
        if not key:continue
        old=canon_by_excerpt.get(key)
        try:score=float(x.get('score') or 0)
        except Exception:score=0.0
        try:old_score=float(old.get('score') or 0) if old else -1.0
        except Exception:old_score=-1.0
        if old is None or score>old_score:canon_by_excerpt[key]=x
    unique_links=sorted(canon_by_excerpt.values(),key=lambda x:float(x.get('score') or 0),reverse=True)
    shown=unique_links[:20]
    st.append(Paragraph(escape(
        f"Кандидатов связи с каноном: {len(printable)}; уникальных формулировок: {len(unique_links)}; "
        f"в отчёте показано {len(shown)} наиболее сильных. Повторы и полный список сохранены "
        "во встроенном master_analysis.json."
    ),body))
    if not shown:st.append(Paragraph('Подтверждённые связи с каноном автоматически не выделены.',body))
    for x in shown:st.append(Paragraph(escape(f"{x.get('status')} (score {x.get('score')}): {x.get('canonical_excerpt') or ''}"),body))
    for x in master.get('knowledge_gaps',[]):st.append(Paragraph(escape(f"Кандидат в пробел: {x.get('question_context')}. Следующий шаг: {x.get('next_action')}"),body))
    st.append(Paragraph('11. Рекомендации и следующее занятие',h2))
    recs=master.get('recommendations',[])
    if not recs:st.append(Paragraph('Рекомендации не сформированы: недостаточно подтверждённых данных.',body))
    for x in recs:st.append(Paragraph(escape(x.get('text','')),body))
    st.append(Paragraph('12. Кандидаты раздач и решений',h2));st.append(Paragraph(escape(f"Раздач: {len(master.get('deals',[]))}; решений: {len(master.get('decisions',[]))}. Неизвестное не достраивается по догадке."),body))
    st+=[PageBreak(),Paragraph('13. Полный транскрипт с таймкодами',h2)]
    for s in master.get('transcript',[]):st.append(Paragraph(escape(f"[{_tm(s['start'])}–{_tm(s['end'])}] "+((s.get('speaker')+': ') if s.get('speaker') else '')+s.get('text','')+(' [требует проверки]' if s.get('unreliable') else '')),body))
    tq=master.get('technical_qc',{}).get('transcript',{}) or {}
    cq=master.get('content_quality',{}) or {}
    risk=tq.get('riskSummary',{}) or {}
    gate=cq.get('r24Gate',{}) or {}
    qc_records=list(tq.get('qc',[]) or [])
    failed_qc=[x for x in qc_records if not bool(x.get('ok'))]
    hallucination_qc=[
        x for x in qc_records
        if 'REPEATED_NONSPEECH_HALLUCINATION' in (x.get('failureReasons') or [])
    ]
    transcript=list(master.get('transcript',[]) or [])
    empty_isolated=0
    for record in failed_qc:
        start=float(record.get('start') or 0);end=float(record.get('end') or start)
        if not any(float(s.get('end') or 0)>start and float(s.get('start') or 0)<end for s in transcript):
            empty_isolated+=1
    derived_leaks=int(gate.get('unreliableDerivedEvidenceCount') or 0)
    st+=[PageBreak(),Paragraph('14. Технический QC и качество содержания',h2)]
    st.append(Paragraph(escape(
        f"Итог: semantic QC — {cq.get('semantic_qc_status') or 'не установлен'}; "
        f"content gate — {'PASS' if gate.get('ok') else 'FAIL/UNKNOWN'}; "
        f"утечки ненадёжных доказательств в производные выводы — {derived_leaks}."
    ),body))
    st.append(Paragraph(escape(
        f"Контроль ASR: источник {tq.get('primarySource') or 'не установлен'}; сегментов {cq.get('transcript_segments',len(transcript))}; "
        f"проверено окон {len(qc_records)}; изолировано непрошедших окон {len(failed_qc)}; "
        f"из них пустых интервалов без первичных речевых сегментов {empty_isolated}; "
        f"патологических повторяющихся галлюцинаций {len(hallucination_qc)}."
    ),body))
    st.append(Paragraph(escape(
        f"ASR-риск: максимум {risk.get('maxEstimatedErrorRisk')}; средних и выше блоков {risk.get('mediumOrHigherBlocks',0)}; "
        f"высоких/критических {risk.get('highOrCriticalBlocks',0)}. Это эвристическая диагностика, а не калиброванная вероятность."
    ),body))
    st.append(Paragraph(escape(
        f"Изоляция: ненадёжных сегментов сохранено {cq.get('unreliable_transcript_segments',0)}; "
        f"для смысловой аналитики использовано {cq.get('semantic_derivation_transcript_segments',0)}; "
        f"исключение ненадёжных сегментов — {'включено' if cq.get('unreliable_segments_excluded_from_semantic_derivation') else 'не подтверждено'}."
    ),body))
    st.append(Paragraph(escape(
        f"Семантика: эпизодов {cq.get('semantic_episodes',0)}; автоисправлений {cq.get('semantic_auto_corrections',0)}; "
        f"неразрешённых критических кандидатов {cq.get('semantic_critical_unresolved',0)}; "
        f"раздач-кандидатов {cq.get('deal_candidates',0)}; решений-кандидатов {cq.get('decision_candidates',0)}."
    ),body))
    st.append(Paragraph(escape(
        f"Разметка ролей: {cq.get('actor_attribution_status') or 'не установлена'}; "
        f"метки говорящих {'есть' if cq.get('speaker_labels_present') else 'отсутствуют'}; "
        f"неподтверждённых персональных утверждений исключено {cq.get('actor_specific_claims_excluded',0)}."
    ),body))
    st.append(Paragraph(escape(
        f"Визуальный анализ: доказательств {cq.get('visual_evidence_items',0)}; "
        f"кадров в отчёте {cq.get('selected_report_visuals',0)}. "
        f"Канонических связей в master JSON {cq.get('canon_links_found',0)}."
    ),body))
    st.append(Paragraph('Полные технические данные, все несвёрнутые учебные циклы, canon links и исходный master_analysis.json встроены в PDF без сокращения.',body))
    doc.build(st)
    return append_deal_review_pages(Path(out), master=master, shots=shots)
def embed_master(pdf,master):
    import fitz
    raw=json.dumps(master,ensure_ascii=False,indent=2).encode();d=fitz.open(pdf);d.embfile_add('master_analysis.json',raw,filename='master_analysis.json',ufilename='master_analysis.json',desc='Bridge Video 3.1 FREE master analysis');tmp=Path(str(pdf)+'.embed.pdf');d.save(tmp,garbage=4,deflate=True);d.close();tmp.replace(pdf);return hashlib.sha256(raw).hexdigest()
def pdfqc(p,expected_deal_review_pages=0):
    import fitz
    d=fitz.open(p);issues=[]
    if d.page_count<=0:issues.append('no-pages')
    for page in d:
        pix=page.get_pixmap(matrix=fitz.Matrix(1,1))
        if pix.width<=0 or pix.height<=0:issues.append('render')
        for b in page.get_text('blocks'):
            x0,y0,x1,y1=b[:4]
            if x0<-1 or y0<-1 or x1>page.rect.x1+1 or y1>page.rect.y1+1:issues.append('bounds')
    if expected_deal_review_pages:
        if d.page_count<expected_deal_review_pages:issues.append('deal-review-page-count')
        else:
            for page in list(d)[-expected_deal_review_pages:]:
                text=page.get_text()
                if page.rect.width<=page.rect.height:issues.append('deal-review-not-landscape')
                for marker in ('3.1 FREE - DEAL REVIEW','Распознано','Достроенный расклад','Торговля','EVIDENCE REVIEW'):
                    if marker not in text:issues.append('deal-review-marker')
    embedded=set(d.embfile_names()) if hasattr(d,'embfile_names') else set();ok='master_analysis.json' in embedded
    if not ok:issues.append('master-json-not-embedded')
    res={'ok':not issues,'pages':d.page_count,'issues':sorted(set(issues)),'sha256':io.sha(p),'masterEmbedded':ok,'dealReviewPages':int(expected_deal_review_pages)};d.close();return res

def process_job(t):
    job=os.environ['BRIDGE_JOB_ID'];explicit_source=os.environ.get('BRIDGE_ORIGINAL_SOURCE_DRIVE_ID','').strip();revs=['3.1-free-semantic-r1','3.1-free-semantic-r2','3.1-free-semantic-r3','3.1.3-semantic-r1'];matches=[]
    if explicit_source:
        candidate=io.meta(t,explicit_source)
        if not str(candidate.get('mimeType') or '').startswith('video/'):raise RuntimeError('BLOCKED_IDENTITY')
        if job!=stable_job_id('drive',explicit_source):raise RuntimeError('BLOCKED_IDENTITY')
        candidates=[candidate]
    else:candidates=io.search(t,"trashed=false and mimeType contains 'video/'")
    for f in candidates:
        accepted={stable_job_id('drive',f['id'])};accepted.update(legacy_job_id('drive',f['id'],r) for r in revs)
        if job in accepted:matches.append(f)
    if len(matches)!=1:raise RuntimeError('BLOCKED_IDENTITY')
    src=matches[0];parent=(src.get('parents') or [None])[0]
    if not parent:raise RuntimeError('BLOCKED_IDENTITY')
    io.safe(job_id=job,stage='FETCHING',exit_code=0,size_bytes=int(src.get('size') or 0))
    with tempfile.TemporaryDirectory(prefix='bridge-') as td:
        work=Path(td);suffix=Path(src.get('name') or '').suffix.lower();suffix=suffix if suffix and len(suffix)<=12 else '.video';video=work/('source'+suffix);io.download(t,src['id'],video);dur=io.duration(video);ps=io.perms(t,src['id']);passport={'driveId':src['id'],'name':src['name'],'mimeType':src.get('mimeType'),'sizeBytes':video.stat().st_size,'durationSeconds':dur,'sha256':io.sha(video),'parentFolderId':parent,'permissions':io.pmatrix(ps),'immutable':True}
        course,cid,cname=course_text(t);segs,tinfo,warnings=obtain_transcript(t,parent,src['name'],video,work,dur,job);participants=sorted({s.get('speaker') for s in segs if s.get('speaker')});critical=[s['start'] for s in segs if bridge_term_hits(s['text'])];p1,p2,shots=visual(video,work,dur,critical,job)
        if not p2['gapCheckPass']:raise RuntimeError('VISUAL_GAP_CHECK_FAILED')
        eps=semantic_episode_plan(segs,job);attach_visual_evidence(eps,shots);links=course_link_candidates(eps,course,cid);deals,decisions=derive_deals_decisions(eps,job);pubshots=[{k:v for k,v in x.items() if k!='path'} for x in shots]
        master=master_analysis_payload(job_id=job,passport=passport,transcript=segs,transcript_qc=tinfo,visual_qc={'pass1':p1,'pass2':p2},episodes=eps,course_links=links,screenshots=pubshots,participants=[{'name':x,'role':'unknown until confirmed'} for x in participants],methodology_source={'driveId':cid,'name':cname,'sha256':hashlib.sha256(course.encode()).hexdigest(),'status':'canonical-first'},extra_warnings=warnings);master['deals']=deals;master['decisions']=decisions;master['content_quality']['deal_candidates']=len(deals);master['content_quality']['decision_candidates']=len(decisions);r24=validate_r24_master(master);master['content_quality']['r24Gate']=r24;io.safe(job_id=job,stage='MASTER_ANALYSIS_BUILD',exit_code=0 if r24['ok'] else 1,episode_count=len(eps),content_warning_count=len(warnings))
        if not r24['ok']:raise RuntimeError('R24_CONTENT_GATE_FAILED:'+','.join(r24['issues']))
        report=work/f"{_safe_stem(src['name'])} — мастер-анализ 3.1 FREE.pdf"
        deal_review=pdf_report(report,master,shots)
        master['content_quality']['deal_review_pdf']=deal_review
        msha=embed_master(report,master)
        q=pdfqc(report,expected_deal_review_pages=deal_review['pages'])
        io.safe(job_id=job,stage='PDF_QC',exit_code=0 if q['ok'] else 1,master_embedded=q['masterEmbedded'],deal_review_pages=q['dealReviewPages'])
        if not q['ok']:raise RuntimeError('PDF_QC_FAILED')
        up=io.upload_file(t,parent,report,'application/pdf');have={io.pkey(x) for x in io.perms(t,up['id']) if x.get('role')!='owner'}
        for p in ps:
            if p.get('role')!='owner' and io.pkey(p) not in have:io.add_perm(t,up['id'],p)
        access=io.pmatrix(io.perms(t,up['id']))==io.pmatrix(ps)
        if not access:raise RuntimeError('PDF_ACCESS_MISMATCH')
        chk=work/'recheck.bin';io.download(t,src['id'],chk)
        if io.sha(chk)!=passport['sha256'] or chk.stat().st_size!=passport['sizeBytes']:raise RuntimeError('ORIGINAL_REVERIFY_FAILED')
        done={'schema':'bridge-video-ai-done','algorithmVersion':ALGORITHM_VERSION,'algorithmRevision':ALGORITHM_REVISION,'status':'AI_DONE','job_id':job,'original':passport,'masterPdf':{'driveId':up['id'],'name':up['name'],'sizeBytes':int(up.get('size') or 0),'pages':q['pages'],'dealReviewPages':q['dealReviewPages'],'dealReviewScreenshotsEmbedded':deal_review['screenshots_embedded'],'sha256':q['sha256'],'masterJsonEmbedded':q['masterEmbedded'],'masterJsonSha256':msha,'access_match':access},'speech':{'primarySource':tinfo.get('primarySource'),'segmentCount':len(segs),'qcCount':len(tinfo.get('qc') or []),'unreliableCount':sum(bool(s.get('unreliable')) for s in segs)},'visual':{'pass1':p1['status'],'pass2':p2['status'],'gapCheckPass':p2['gapCheckPass'],'evidenceCount':len(shots)},'semantic':master.get('content_quality',{}),'methodologySource':master['technical_qc']['methodology_source'],'at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())};io.upload_json(t,parent,f'AI_DONE_{job}.json',done);io.upload_json(t,parent,f'METHODOLOGY_READY_{job}.json',{'schema':'bridge-video-methodology-ready','status':'METHODOLOGY_READY','job_id':job,'algorithmVersion':ALGORITHM_VERSION,'algorithmRevision':ALGORITHM_REVISION,'contentGate':r24,'masterPdfDriveId':up['id'],'masterPdfSha256':q['sha256'],'at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())});done_sha=q['sha256']
    io.upload_json(t,parent,f'CLEANUP_ACK_{job}.json',{'status':'CLEANUP_ACK','job_id':job,'algorithmRevision':ALGORITHM_REVISION,'reportSha256':done_sha,'temporaryRunnerDataDeleted':True,'at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())});io.safe(job_id=job,stage='CLEANUP_ACK',exit_code=0)
    return done
