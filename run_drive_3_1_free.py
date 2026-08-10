#!/usr/bin/env python3
from pathlib import Path
import hashlib, json, math, os, re, shutil, subprocess, tempfile, time
import requests
from collections import Counter
from bridge_worker_3_1_free import ALGORITHM_VERSION, ALGORITHM_REVISION, stable_job_id, autonomous_qc_indices, bridge_term_hits, visual_pass1_plan, visual_pass2_requirements, sanitize_public_log

DRIVE='https://www.googleapis.com/drive/v3'
UPLOAD='https://www.googleapis.com/upload/drive/v3/files'
PROMPT='Спортивный бридж: заявка, контракт, Стейман, трансфер, контра, реконтра, импас, козырь, БК, первый ход. Не добавляй неслышанные слова.'

def safe(**kw): print(json.dumps(sanitize_public_log(kw),ensure_ascii=False))
def sh(cmd):
    p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    if p.returncode: raise RuntimeError('subprocess failed')
    return p.stdout

def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(8*1024*1024),b''): h.update(b)
    return h.hexdigest()

def token():
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    info=json.loads(os.environ['GOOGLE_SERVICE_ACCOUNT_JSON'])
    c=service_account.Credentials.from_service_account_info(info,scopes=['https://www.googleapis.com/auth/drive'])
    c.refresh(Request()); return c.token

def hdr(t): return {'Authorization':'Bearer '+t}
def search(t,q):
    out=[]; page=None
    while True:
        p={'q':q,'fields':'nextPageToken,files(id,name,mimeType,size,parents,modifiedTime,owners(displayName,emailAddress))','pageSize':1000,'spaces':'drive'}
        if page: p['pageToken']=page
        r=requests.get(DRIVE+'/files',headers=hdr(t),params=p,timeout=60); r.raise_for_status(); d=r.json(); out+=d.get('files',[]); page=d.get('nextPageToken')
        if not page: return out

def meta(t,fid):
    r=requests.get(DRIVE+'/files/'+fid,headers=hdr(t),params={'fields':'id,name,mimeType,size,parents,modifiedTime,owners(displayName,emailAddress)'},timeout=30); r.raise_for_status(); return r.json()
def perms(t,fid):
    r=requests.get(DRIVE+f'/files/{fid}/permissions',headers=hdr(t),params={'fields':'permissions(id,type,role,emailAddress,domain,displayName,allowFileDiscovery)'},timeout=30); r.raise_for_status(); return r.json().get('permissions',[])
def pkey(p): return (p.get('type'),(p.get('emailAddress') or p.get('domain') or ('anyone' if p.get('type')=='anyone' else p.get('displayName') or '')).lower(),p.get('role'))
def pmatrix(ps): return sorted([{'type':p.get('type'),'principal':p.get('emailAddress') or p.get('domain') or ('anyone' if p.get('type')=='anyone' else p.get('displayName') or ''),'role':p.get('role'),'allowFileDiscovery':p.get('allowFileDiscovery')} for p in ps if p.get('role')!='owner'],key=lambda x:(str(x['type']),str(x['principal']).lower(),str(x['role'])))
def download(t,fid,out):
    with requests.get(DRIVE+'/files/'+fid,headers=hdr(t),params={'alt':'media'},stream=True,timeout=120) as r:
        r.raise_for_status()
        with open(out,'wb') as f:
            for b in r.iter_content(8*1024*1024):
                if b: f.write(b)
def export_text(t,fid):
    r=requests.get(DRIVE+f'/files/{fid}/export',headers=hdr(t),params={'mimeType':'text/plain'},timeout=60); r.raise_for_status(); return r.text

def upload_file(t,parent,path,mime):
    m={'name':Path(path).name,'parents':[parent]}; b='bridge'+hashlib.sha1(Path(path).name.encode()).hexdigest()[:10]
    body=(f'--{b}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'+json.dumps(m)+f'\r\n--{b}\r\nContent-Type: {mime}\r\n\r\n').encode()+Path(path).read_bytes()+f'\r\n--{b}--\r\n'.encode()
    r=requests.post(UPLOAD+'?uploadType=multipart&fields=id,name,size,parents',headers={**hdr(t),'Content-Type':f'multipart/related; boundary={b}'},data=body,timeout=180); r.raise_for_status(); return r.json()
def upload_json(t,parent,name,obj):
    p=Path(tempfile.mkstemp(suffix='.json')[1]); p.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
    try: p2=p.with_name(name); p.rename(p2); return upload_file(t,parent,p2,'application/json')
    finally:
        for x in [p,p.with_name(name)]: x.unlink(missing_ok=True)
def add_perm(t,fid,p):
    body={'type':p.get('type'),'role':p.get('role')}
    if p.get('emailAddress'): body['emailAddress']=p['emailAddress']
    if p.get('domain'): body['domain']=p['domain']
    if p.get('type')=='anyone' and p.get('allowFileDiscovery') is not None: body['allowFileDiscovery']=p.get('allowFileDiscovery')
    r=requests.post(DRIVE+f'/files/{fid}/permissions',headers={**hdr(t),'Content-Type':'application/json'},params={'sendNotificationEmail':'false'},json=body,timeout=30); r.raise_for_status()

def duration(p): return float(sh(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(p)]).strip())
def wav(video,out,start=None,dur=None):
    c=['ffmpeg','-y'];
    if start is not None: c+=['-ss',str(start)]
    c+=['-i',str(video)]
    if dur is not None: c+=['-t',str(dur)]
    c+=['-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',str(out)]; sh(c)

def words(x): return re.findall(r'[A-Za-zА-Яа-яЁё0-9]+',(x or '').lower())
def similarity(a,b):
    aa,bb=words(a),words(b)
    if not aa or not bb:return 0.0
    ca,cb=Counter(aa),Counter(bb); common=sum((ca&cb).values()); pr=common/len(bb); rc=common/len(aa); return 2*pr*rc/(pr+rc) if pr+rc else 0.0

MODEL=None
def asr(path,strict=False):
    global MODEL
    if MODEL is None:
        from faster_whisper import WhisperModel
        MODEL=WhisperModel(os.getenv('WHISPER_MODEL','small'),device='cpu',compute_type='int8')
    kw={'language':None,'condition_on_previous_text':False,'initial_prompt':PROMPT,'beam_size':3 if strict else 5,'vad_filter':True}
    if strict: kw['vad_parameters']={'threshold':.65,'min_speech_duration_ms':300,'min_silence_duration_ms':800}
    segs,_=MODEL.transcribe(str(path),**kw); return ' '.join((s.text or '').strip() for s in segs if (s.text or '').strip())

def transcribe(video,work,dur):
    blocks=[]; start=0.; i=0
    while start<dur:
        end=min(dur,start+300); w=work/f'b{i:03d}.wav'; wav(video,w,start,end-start); text=asr(w)
        if len(words(text))<5 and end-start>20: text=asr(w,True)
        if not text:
            fresh=work/f'b{i:03d}-fresh.wav'; wav(video,fresh,start,end-start); text=asr(fresh,True)
        blocks.append({'index':i,'start':start,'end':end,'text':text or '[неразборчиво]','unreliable':not bool(text)})
        if end>=dur:break
        start=end-1.5;i+=1
    rich=[b['index'] for b in blocks if bridge_term_hits(b['text'])]; qidx=autonomous_qc_indices(len(blocks),rich); qc=[]
    for i in qidx:
        b=blocks[i]; w=work/f'q{i:03d}.wav'; wav(video,w,b['start'],b['end']-b['start']); r=asr(w,True); sim=similarity(b['text'],r); t1=set(bridge_term_hits(b['text'])); t2=set(bridge_term_hits(r)); ok=bool(r) and sim>=.35 and (not t1 or bool(t1&t2)); qc.append({'block':i,'ok':ok,'similarity':round(sim,3)})
    need=min(len(blocks),max(3,math.ceil(len(blocks)*.1))); passed=len(qc)>=need and all(x['ok'] for x in qc)
    return blocks,qc,passed

def frame(video,t,out): sh(['ffmpeg','-y','-ss',f'{t:.3f}','-i',str(video),'-frames:v','1','-q:v','2',str(out)])
def visual(video,work,dur,critical):
    import cv2, imagehash
    from PIL import Image
    scene=[]; ph=[]; prev=None; prevh=None; t=0.;i=0
    while t<dur:
        f=work/f's{i:05d}.jpg'; frame(video,t,f); im=cv2.imread(str(f))
        if im is not None:
            g=cv2.resize(cv2.cvtColor(im,cv2.COLOR_BGR2GRAY),(160,90))
            if prev is not None and float(cv2.absdiff(g,prev).mean())>=18: scene.append(round(t,3))
            h=imagehash.phash(Image.open(f))
            if prevh is not None and h-prevh>=12: ph.append(round(t,3))
            prev,prevh=g,h
        f.unlink(missing_ok=True);t+=10;i+=1
    p1=visual_pass1_plan(dur,scene,ph); req=visual_pass2_requirements(p1,critical); ev=[]
    for i,t in enumerate(req['targets']):
        f=work/f'e{i:04d}.jpg'; frame(video,t,f)
        if f.exists() and f.stat().st_size: ev.append({'time':t,'path':str(f),'sha256':sha(f)})
    return p1,{'status':'VISUAL_PASS_2_COMPLETE' if len(ev)==len(req['targets']) else 'VISUAL_PASS_2_FAILED','evidence':ev,'gapCheckPass':len(ev)==len(req['targets'])}

def course_text(t):
    files=[]
    for name in ['Курс Бридж - Конспект. Правки.','Курс Бридж - Конспект. Правки']:
        safe=name.replace("'","\\'"); files+=search(t,f"trashed=false and name='{safe}'")
    if not files: raise RuntimeError('BLOCKED_METHOD_SOURCE_MISSING')
    return export_text(t,files[0]['id']),files[0]['id']

def analyze(blocks,course):
    paras=[' '.join(x.split()) for x in course.splitlines() if len(x.strip())>=30]; eps=[]
    for b in blocks:
        terms=bridge_term_hits(b['text'])
        if not terms:continue
        bw=set(words(b['text'])); best=None;score=0
        for p in paras:
            pw=set(words(p)); sc=len(bw&pw)/max(1,len(bw|pw))
            if sc>score:score,best=sc,p
        eps.append({'start':b['start'],'end':b['end'],'terms':terms,'course':best if score>=.02 else None,'score':round(score,3)})
    if not eps: raise RuntimeError('METHODICAL_ANALYSIS_NOT_READY')
    return eps

def pdf_report(out,passport,blocks,qc,eps,p1,p2):
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,PageBreak
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.units import mm
    from xml.sax.saxutils import escape
    font='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf';pdfmetrics.registerFont(TTFont('DV',font)); style=ParagraphStyle('b',fontName='DV',fontSize=8.5,leading=11,spaceAfter=4)
    doc=SimpleDocTemplate(str(out),pagesize=A4,leftMargin=14*mm,rightMargin=14*mm,topMargin=14*mm,bottomMargin=14*mm); st=[]
    for x in ['Анализ видеозаписи занятия — 3.1 FREE',f"Оригинал: {passport['name']}",f"Размер: {passport['sizeBytes']}",f"SHA-256: {passport['sha256']}",f"ASR QC: {sum(x['ok'] for x in qc)}/{len(qc)}",f"Visual: {p1['status']} / {p2['status']}"]: st.append(Paragraph(escape(str(x)),style))
    st.append(Spacer(1,6))
    for i,e in enumerate(eps,1):
        st.append(Paragraph(escape(f"Эпизод {i}, {e['start']:.1f}–{e['end']:.1f} c; термины: {', '.join(e['terms'])}"),style))
        st.append(Paragraph(escape('Материал курса: '+(e['course'] or 'прямое совпадение не найдено')),style))
    st.append(PageBreak());st.append(Paragraph('Приложение: полная проверенная расшифровка',style))
    for b in blocks: st.append(Paragraph(escape(f"[{b['start']:.2f}–{b['end']:.2f}] {b['text']}"),style))
    doc.build(st)

def pdfqc(p):
    import fitz
    d=fitz.open(p); issues=[]
    for i,page in enumerate(d):
        pix=page.get_pixmap(matrix=fitz.Matrix(1,1))
        if pix.width<=0 or pix.height<=0:issues.append('render')
        for b in page.get_text('blocks'):
            x0,y0,x1,y1=b[:4]
            if x0<-1 or y0<-1 or x1>page.rect.x1+1 or y1>page.rect.y1+1:issues.append('bounds')
    return {'ok':not issues,'pages':d.page_count,'issues':issues,'sha256':sha(p)}

def main():
    job=os.environ['BRIDGE_JOB_ID']; t=token(); candidates=search(t,"trashed=false and mimeType='video/mp4'"); matches=[f for f in candidates if stable_job_id('drive',f['id'])==job]
    if len(matches)!=1: raise RuntimeError('BLOCKED_IDENTITY')
    srcm=matches[0]; parent=(srcm.get('parents') or [None])[0]
    if not parent:raise RuntimeError('BLOCKED_IDENTITY')
    safe(job_id=job,stage='FETCHING',exit_code=0,size_bytes=int(srcm.get('size') or 0))
    with tempfile.TemporaryDirectory(prefix='bridge-') as td:
        work=Path(td); video=work/'source.mp4';download(t,srcm['id'],video);dur=duration(video);ps=perms(t,srcm['id']);passport={'driveId':srcm['id'],'name':srcm['name'],'sizeBytes':video.stat().st_size,'durationSeconds':dur,'sha256':sha(video),'parentFolderId':parent,'permissions':pmatrix(ps)}
        course,cid=course_text(t); blocks,qc,ok=transcribe(video,work,dur)
        if not ok: raise RuntimeError('ASR_QC_FAILED')
        critical=[b['start'] for b in blocks if bridge_term_hits(b['text'])];p1,p2=visual(video,work,dur,critical)
        if not p2['gapCheckPass']:raise RuntimeError('VISUAL_GAP_CHECK_FAILED')
        eps=analyze(blocks,course); report=work/f'Диана 8 — анализ 3.1 FREE.pdf';pdf_report(report,passport,blocks,qc,eps,p1,p2);q=pdfqc(report)
        if not q['ok']:raise RuntimeError('PDF_QC_FAILED')
        up=upload_file(t,parent,report,'application/pdf')
        have={pkey(x) for x in perms(t,up['id']) if x.get('role')!='owner'}
        for p in ps:
            if p.get('role')!='owner' and pkey(p) not in have:add_perm(t,up['id'],p)
        access=pmatrix(perms(t,up['id']))==pmatrix(ps)
        if not access:raise RuntimeError('PDF_ACCESS_MISMATCH')
        chk=work/'recheck.bin';download(t,srcm['id'],chk);recheck=sha(chk)==passport['sha256'] and chk.stat().st_size==passport['sizeBytes']
        if not recheck:raise RuntimeError('ORIGINAL_REVERIFY_FAILED')
        done={'schema':'bridge-video-ai-done','algorithmVersion':ALGORITHM_VERSION,'algorithmRevision':ALGORITHM_REVISION,'status':'AI_DONE','job_id':job,'original':passport,'pdf':{'driveId':up['id'],'name':up['name'],'sizeBytes':int(up.get('size') or 0),'pages':q['pages'],'sha256':q['sha256'],'access_match':access},'speech':{'blockCount':len(blocks),'qcCount':len(qc),'unreliableCount':sum(b['unreliable'] for b in blocks)},'visual':{'pass1':p1['status'],'pass2':p2['status'],'gapCheckPass':p2['gapCheckPass']},'methodologySource':{'driveId':cid,'sha256':hashlib.sha256(course.encode()).hexdigest()},'at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
        upload_json(t,parent,f'AI_DONE_{job}.json',done)
        done_sha=q['sha256']
    upload_json(t,parent,f'CLEANUP_ACK_{job}.json',{'status':'CLEANUP_ACK','job_id':job,'reportSha256':done_sha,'temporaryRunnerDataDeleted':True,'at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())})
    safe(job_id=job,stage='CLEANUP_ACK',exit_code=0)

if __name__=='__main__':
    try: main()
    except Exception as e:
        safe(job_id=os.getenv('BRIDGE_JOB_ID',''),stage='FAILED_UNRECOVERABLE',exit_code=1,error_class=type(e).__name__); raise
