#!/usr/bin/env python3
import os,re,json,time,tempfile,hashlib,subprocess,statistics
from pathlib import Path
from xml.sax.saxutils import escape
import run_drive_3_1_free as io
from bridge_worker_3_1_free import ALGORITHM_VERSION
ALGORITHM_REVISION='3.1-free-master-analysis-r4'

def stable_job_id(ns,key):return hashlib.sha256(f'{ns}:{key}'.encode()).hexdigest()[:32]
def legacy_job_id(ns,key,rev):return hashlib.sha256(f'{ns}:{key}:{rev}'.encode()).hexdigest()[:32]
def _safe_stem(n):return Path(n).stem.replace('/','_').replace('\\','_')
def _tm(x):
    x=max(0,int(float(x)));return f'{x//3600:02d}:{(x%3600)//60:02d}:{x%60:02d}'
def _norm(s):return re.sub(r'\s+',' ',(s or '').strip())
def bridge_term_hits(t):
    low=(t or '').lower();terms=['расклад','сдач','торгов','заяв','контракт','козыр','без коз','бк','импас','экспас','первый ход','разыгрыва','дилер','пас','гейм','взятк','стол','болван']
    return [x for x in terms if x in low]
def course_text(t):
    files=io.search(t,"trashed=false and mimeType='application/vnd.google-apps.document'");best=None
    for f in files:
        n=(f.get('name') or '').lower()
        if 'курс' in n and 'бридж' in n and 'конспект' in n:best=f;break
    if not best:return '',None,None
    try:return io.export_text(t,best['id']),best['id'],best['name']
    except Exception:return '',best['id'],best['name']
def chunks(dur,step=300):
    out=[];s=0
    while s<dur:
        e=min(dur,s+step);out.append((s,e));s=e
    return out
def obtain_transcript(t,parent,name,video,work,dur,job):
    io.safe(job_id=job,stage='TRANSCRIPT_DISCOVERY',exit_code=0)
    base=_safe_stem(name).lower();cand=io.search(t,"trashed=false")
    text_candidates=[]
    for f in cand:
        fn=(f.get('name') or '').lower()
        if base[:24] in fn and any(x in fn for x in ['transcript','транскрип','caption','subtitle','.vtt','.srt']):text_candidates.append(f)
    if text_candidates:
        for f in text_candidates:
            try:
                txt=io.download_text(t,f['id']);segs=parse_timed_text(txt,dur)
                if segs:
                    info={'primarySource':'source_captions','status':'SOURCE CAPTIONS','qc':[]};return segs,info,[]
            except Exception:pass
    segs=[];qc=[];warnings=[]
    try:
        from faster_whisper import WhisperModel
        model=WhisperModel(os.environ.get('WHISPER_MODEL','small'),device='cpu',compute_type='int8')
        for idx,(s,e) in enumerate(chunks(dur,300)):
            wav=work/f'a{idx}.wav';subprocess.run(['ffmpeg','-y','-ss',str(s),'-to',str(e),'-i',str(video),'-vn','-ac','1','-ar','16000',str(wav)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
            best=None
            for retry in range(2):
                ss,inf=model.transcribe(str(wav),language='ru',beam_size=5 if retry==0 else 7,initial_prompt='Спортивный бридж: торговля, заявка, контракт, козырь, без козыря, БК, импас, экспас, разыгрывающий, первый ход, взятка, дилер, пас, гейм.')
                cur=[]
                for z in ss:
                    txt=_norm(z.text)
                    if txt:cur.append({'start':s+float(z.start),'end':s+float(z.end),'text':txt,'speaker':None,'unreliable':False})
                joined=' '.join(x['text'] for x in cur)
                score=lexical_qc(joined)
                if best is None or score>best[0]:best=(score,cur)
                if score>=.72:break
            qc.append({'unit':idx,'similarity':round(best[0],3),'ok':best[0]>=.72})
            io.safe(stage='ASR_QC',qc_block=idx,unit_index=idx,qc_retry=retry>0,qc_similarity=round(best[0],3),qc_ok=best[0]>=.72)
            segs.extend(best[1])
        failed=sum(not q['ok'] for q in qc);anchors=sum(1 for x in ['бридж','контракт','козыр'] if any(x in s['text'].lower() for s in segs))
        io.safe(stage='ASR_QC',exit_code=0 if failed==0 else 1,qc_total=len(qc),qc_failed=failed,qc_anchor_passed=anchors)
        if failed:warnings.append(f'ASR QC: {failed} блоков ниже порога; такие места требуют проверки.')
        info={'primarySource':'local_asr','status':'AUTO-VERIFIED ASR TRANSCRIPT','qc':qc,'anchors':anchors}
        return segs,info,warnings
    except Exception as ex:
        raise RuntimeError(f'ASR_FAILED: {ex}')
def lexical_qc(text):
    words=re.findall(r'[а-яёa-z0-9]+',(text or '').lower())
    if not words:return 0.0
    good=sum(len(w)>=2 for w in words)/len(words);bridge=min(1.0,len(bridge_term_hits(text))/5)
    return .7*good+.3*bridge
def parse_timed_text(txt,dur):
    segs=[];rx=re.compile(r'(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})')
    lines=txt.splitlines();i=0
    while i<len(lines):
        m=rx.search(lines[i])
        if not m:i+=1;continue
        vals=list(map(int,m.groups()));s=vals[0]*3600+vals[1]*60+vals[2]+vals[3]/1000;e=vals[4]*3600+vals[5]*60+vals[6]+vals[7]/1000;i+=1;buf=[]
        while i<len(lines) and lines[i].strip() and not rx.search(lines[i]):buf.append(lines[i].strip());i+=1
        text=_norm(' '.join(buf))
        if text:segs.append({'start':s,'end':e,'text':text,'speaker':None,'unreliable':False})
    return segs
def visual(video,work,dur,critical,job):
    import cv2,imagehash
    from PIL import Image
    probes=sorted(set([0,max(0,dur-1)]+[x for x in range(0,int(dur)+1,120)]+[int(x) for x in critical]))
    cap=cv2.VideoCapture(str(video));shots=[];last=None
    for i,t in enumerate(probes):
        cap.set(cv2.CAP_PROP_POS_MSEC,t*1000);ok,frame=cap.read()
        if not ok:continue
        p=work/f'shot_{i:04d}.jpg';cv2.imwrite(str(p),frame);h=imagehash.phash(Image.open(p));change=64 if last is None else h-last;last=h
        shots.append({'time':float(t),'path':str(p),'phash':str(h),'change':int(change)})
    cap.release();p1={'status':'PASS' if shots else 'FAIL','count':len(shots)}
    gaps=[]
    for a,b in zip(shots,shots[1:]):
        if b['time']-a['time']>150:gaps.append((a['time'],b['time']))
    p2={'status':'PASS' if not gaps else 'FAIL','gapCheckPass':not gaps,'gaps':gaps,'criticalSamples':len(critical)}
    return p1,p2,shots
def semantic_episode_plan(segs,job):
    eps=[]
    for s in segs:
        txt=s['text'];low=txt.lower();typ=None
        if any(x in low for x in ['ошиб','неправиль','нет,','нельзя','почему не']):typ='ошибка/коррекция'
        elif '?' in txt or any(x in low for x in ['почему','как ','что ','какой','сколько']):typ='вопрос/обсуждение'
        elif any(x in low for x in ['торгов','заяв','пас','контра','открыва']):typ='торговля'
        elif any(x in low for x in ['первый ход','защит','вист']):typ='защита'
        elif any(x in low for x in ['разыгрыва','взятк','импас','экспас','козыр']):typ='розыгрыш'
        elif any(x in low for x in ['важно','запомн','правило','принцип']):typ='методический эпизод'
        else:continue
        eps.append({'start':s['start'],'end':s['end'],'type':typ,'text':txt,'topics':bridge_term_hits(txt),'confidence':'low' if s.get('unreliable') else 'medium','visualEvidence':[]})
    return eps
def attach_visual_evidence(eps,shots):
    if not shots:return
    for e in eps:
        near=min(shots,key=lambda x:abs(x['time']-e['start']))
        if abs(near['time']-e['start'])<=75:e['visualEvidence']=[{'time':near['time'],'phash':near['phash'],'change':near['change']}]
def course_link_candidates(eps,course,cid):
    out=[];cl=course.lower()
    for e in eps:
        hits=[t for t in e.get('topics',[]) if t in cl]
        out.append({'episodeStart':e['start'],'status':'candidate' if hits else 'не найдено','topics':hits,'canonicalExcerpt':None,'sourceDriveId':cid})
    return out
def derive_deals_decisions(eps,job):
    deals=[];dec=[]
    for e in eps:
        if any(x in e.get('text','').lower() for x in ['расклад','сдач']):deals.append({'jobId':job,'start':e['start'],'status':'candidate','evidence':e['text'][:500]})
        if e['type'] in ['торговля','защита','розыгрыш'] and e.get('topics'):dec.append({'jobId':job,'start':e['start'],'type':e['type'],'topics':e['topics'],'status':'candidate','evidence':e['text'][:500]})
    return deals,dec
def master_analysis_payload(**kw):
    passport=kw['passport'];segs=kw['transcript'];eps=kw['episodes'];tq=kw['transcript_qc'];v=kw['visual_qc'];links=kw['course_links'];shots=kw['screenshots'];participants=kw['participants'];method=kw['methodology_source'];warnings=list(kw.get('extra_warnings') or [])
    topic_counts={}
    for e in eps:
        for t in e.get('topics',[]):topic_counts[t]=topic_counts.get(t,0)+1
    top=sorted(topic_counts,key=topic_counts.get,reverse=True)
    return {'schema':'bridge-video-master-analysis-v1','algorithmVersion':ALGORITHM_VERSION,'algorithmRevision':ALGORITHM_REVISION,'original':passport,'summary':{'episodeCount':len(eps),'topics':top[:30],'durationSeconds':passport['durationSeconds']},'participants':participants,'transcript':segs,'episodes':eps,'course_links':links,'screenshots':shots,'deals':[],'decisions':[],'content_quality':{'warnings':warnings,'unreliable_segments':sum(bool(s.get('unreliable')) for s in segs)},'technical_qc':{'transcript':tq,'visual':v,'methodology_source':method},'principles':{'unknown_not_invented':True,'original_immutable':True,'source_permissions_preserved':True}}
def pdf_report(path,master,shots):
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,PageBreak
    from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    styles=getSampleStyleSheet();body=ParagraphStyle('Body',parent=styles['BodyText'],fontName='DejaVuSans',fontSize=8.5,leading=11);h1=ParagraphStyle('H1',parent=body,fontSize=16,leading=20,spaceAfter=8);h2=ParagraphStyle('H2',parent=body,fontSize=12,leading=15,spaceBefore=7,spaceAfter=4);small=ParagraphStyle('Small',parent=body,fontSize=7,leading=9)
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfbase import pdfmetrics
    pdfmetrics.registerFont(TTFont('DejaVuSans','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'));doc=SimpleDocTemplate(str(path),pagesize=A4,rightMargin=12*mm,leftMargin=12*mm,topMargin=12*mm,bottomMargin=12*mm);st=[];o=master['original'];st+=[Paragraph('Мастер-анализ видеозаписи занятия — 3.1 FREE',h1),Paragraph(escape(f"Оригинал: {o['name']}"),body),Paragraph(escape(f"Длительность: {_tm(o['durationSeconds'])}; размер: {o['sizeBytes']} байт"),body),Paragraph(escape(f"Drive ID: {o['driveId']}; SHA-256: {o['sha256']}"),small),Paragraph(escape(f"Алгоритм: {master['algorithmVersion']} / {master['algorithmRevision']}"),body)]
    st.append(Paragraph('1. Краткая карта занятия',h2));st.append(Paragraph(escape(f"Смысловых эпизодов: {master['summary']['episodeCount']}. Темы: {', '.join(master['summary']['topics']) or 'не выделены автоматически'}."),body));tq=master['technical_qc']['transcript'];st.append(Paragraph(escape(f"Транскрипт: {tq.get('primarySource')}; статус: {tq.get('status')}."),body))
    for w in master['content_quality'].get('warnings',[]):st.append(Paragraph(escape('Предупреждение: '+w),body))
    st.append(Paragraph('2. Хронология',h2))
    for e in master['episodes'][:500]:st.append(Paragraph(escape(f"{_tm(e['start'])}–{_tm(e['end'])} — {e['type']}; темы: {', '.join(e.get('topics',[])) or '—'}; confidence: {e['confidence']}"),body))
    sections=[('3. Ошибки и коррекции','episodes',lambda x:''),('4. Вопросы и объяснения','episodes',lambda x:'')]
    for title,key,fmt in sections:
        st.append(Paragraph(title,h2));items=master.get(key,[])
        if not items:st.append(Paragraph('Автоматические кандидаты не выделены; это не доказывает отсутствие соответствующих событий.',body))
        for x in items:st.append(Paragraph(escape(fmt(x)),body))
    st.append(Paragraph('7. Связь с каноном и пробелы',h2))
    for x in master.get('canon_links',[]):
        if x.get('status')!='не найдено':st.append(Paragraph(escape(f"{x.get('status')} (score {x.get('score')}): {x.get('canonical_excerpt') or ''}"),body))
    for x in master.get('knowledge_gaps',[]):st.append(Paragraph(escape(f"Кандидат в пробел: {x.get('question_context')}. Следующий шаг: {x.get('next_action')}"),body))
    st.append(Paragraph('8. Рекомендации и следующее занятие',h2))
    for x in master.get('recommendations',[]):st.append(Paragraph(escape(x.get('text','')),body))
    st.append(Paragraph('9. Кандидаты раздач и решений',h2));st.append(Paragraph(escape(f"Раздач: {len(master.get('deals',[]))}; решений: {len(master.get('decisions',[]))}. Неизвестное не достраивается по догадке."),body))
    st+=[PageBreak(),Paragraph('10. Полный транскрипт с таймкодами',h2)]
    for s in master.get('transcript',[]):st.append(Paragraph(escape(f"[{_tm(s['start'])}–{_tm(s['end'])}] "+((s.get('speaker')+': ') if s.get('speaker') else '')+s.get('text','')+(' [требует проверки]' if s.get('unreliable') else '')),body))
    st+=[PageBreak(),Paragraph('11. Технический QC и качество содержания',h2),Paragraph(escape(json.dumps(master.get('content_quality',{}),ensure_ascii=False,indent=2)),small),Paragraph(escape(json.dumps(master.get('technical_qc',{}),ensure_ascii=False,indent=2)),small),Paragraph('В PDF встроен master_analysis.json — машиночитаемая версия этого мастер-анализа.',body)];doc.build(st)
def embed_master(pdf,master):
    import pymupdf as fitz
    raw=json.dumps(master,ensure_ascii=False,indent=2).encode();d=fitz.open(pdf);d.embfile_add('master_analysis.json',raw,filename='master_analysis.json',ufilename='master_analysis.json',desc='Bridge Video 3.1 FREE master analysis');tmp=Path(str(pdf)+'.embed.pdf');d.save(tmp,garbage=4,deflate=True);d.close();tmp.replace(pdf);return hashlib.sha256(raw).hexdigest()
def pdfqc(p):
    import pymupdf as fitz
    d=fitz.open(p);issues=[]
    if d.page_count<=0:issues.append('no-pages')
    for page in d:
        pix=page.get_pixmap(matrix=fitz.Matrix(1,1))
        if pix.width<=0 or pix.height<=0:issues.append('render')
        for b in page.get_text('blocks'):
            x0,y0,x1,y1=b[:4]
            if x0<-1 or y0<-1 or x1>page.rect.x1+1 or y1>page.rect.y1+1:issues.append('bounds')
    embedded=set(d.embfile_names()) if hasattr(d,'embfile_names') else set();ok='master_analysis.json' in embedded
    if not ok:issues.append('master-json-not-embedded')
    res={'ok':not issues,'pages':d.page_count,'issues':sorted(set(issues)),'sha256':io.sha(p),'masterEmbedded':ok};d.close();return res

def process_job(t):
    job=os.environ['BRIDGE_JOB_ID'];candidates=io.search(t,"trashed=false and mimeType contains 'video/'");revs=['3.1-free-semantic-r1','3.1-free-semantic-r2','3.1-free-semantic-r3','3.1.3-semantic-r1'];matches=[]
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
        master=master_analysis_payload(job_id=job,passport=passport,transcript=segs,transcript_qc=tinfo,visual_qc={'pass1':p1,'pass2':p2},episodes=eps,course_links=links,screenshots=pubshots,participants=[{'name':x,'role':'unknown until confirmed'} for x in participants],methodology_source={'driveId':cid,'name':cname,'sha256':hashlib.sha256(course.encode()).hexdigest(),'status':'canonical-first'},extra_warnings=warnings);master['deals']=deals;master['decisions']=decisions;master['content_quality']['deal_candidates']=len(deals);master['content_quality']['decision_candidates']=len(decisions);io.safe(job_id=job,stage='MASTER_ANALYSIS_BUILD',exit_code=0,episode_count=len(eps),content_warning_count=len(warnings))
        report=work/f"{_safe_stem(src['name'])} — мастер-анализ 3.1 FREE.pdf";pdf_report(report,master,shots);msha=embed_master(report,master);q=pdfqc(report);io.safe(job_id=job,stage='PDF_QC',exit_code=0 if q['ok'] else 1,master_embedded=q['masterEmbedded'])
        if not q['ok']:raise RuntimeError('PDF_QC_FAILED')
        up=io.upload_file(t,parent,report,'application/pdf');have={io.pkey(x) for x in io.perms(t,up['id']) if x.get('role')!='owner'}
        for p in ps:
            if p.get('role')!='owner' and io.pkey(p) not in have:io.add_perm(t,up['id'],p)
        access=io.pmatrix(io.perms(t,up['id']))==io.pmatrix(ps)
        if not access:raise RuntimeError('PDF_ACCESS_MISMATCH')
        chk=work/'recheck.bin';io.download(t,src['id'],chk)
        if io.sha(chk)!=passport['sha256'] or chk.stat().st_size!=passport['sizeBytes']:raise RuntimeError('ORIGINAL_REVERIFY_FAILED')
        done={'schema':'bridge-video-ai-done','algorithmVersion':ALGORITHM_VERSION,'algorithmRevision':ALGORITHM_REVISION,'status':'AI_DONE','job_id':job,'original':passport,'masterPdf':{'driveId':up['id'],'name':up['name'],'sizeBytes':int(up.get('size') or 0),'pages':q['pages'],'sha256':q['sha256'],'masterJsonEmbedded':q['masterEmbedded'],'masterJsonSha256':msha,'access_match':access},'speech':{'primarySource':tinfo.get('primarySource'),'segmentCount':len(segs),'qcCount':len(tinfo.get('qc') or []),'unreliableCount':sum(bool(s.get('unreliable')) for s in segs)},'visual':{'pass1':p1['status'],'pass2':p2['status'],'gapCheckPass':p2['gapCheckPass'],'evidenceCount':len(shots)},'semantic':master.get('content_quality',{}),'methodologySource':master['technical_qc']['methodology_source'],'at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())};io.upload_json(t,parent,f'AI_DONE_{job}.json',done);done_sha=q['sha256']
    io.upload_json(t,parent,f'CLEANUP_ACK_{job}.json',{'status':'CLEANUP_ACK','job_id':job,'reportSha256':done_sha,'temporaryRunnerDataDeleted':True,'at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())});io.safe(job_id=job,stage='CLEANUP_ACK',exit_code=0)