#!/usr/bin/env python3
"""Conservative reconstruction of visible bridge hands from report screenshots.

This module is intentionally evidence-only.  It parses only the clearly visible
horizontal North/South hands in the legacy BBO table layout embedded in the
master PDF.  It never infers hidden East/West cards, never fills the deck by
complement, and never treats time/topic/board-number proximity as board identity.

Multiple screenshots are clustered only when their observed seat+card content
overlaps strongly (>=6 exact matches and >=70% of the smaller observed state)
and no cross-seat card conflict exists.  Any impossible merge fails closed.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

RECONSTRUCTION_METHOD_VERSION = "report-visual-board-v1"
FONT_PATHS=[
 '/usr/share/fonts/truetype/liberation/LiberationSansNarrow-Bold.ttf',
 '/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf',
]
LABELS=['A','K','Q','J','T','9','8','7','6','5','4','3','2']
RENDER_TEXT={**{x:x for x in LABELS},'T':'10'}
SUITS='HCDS'

def _norm_mask(mask):
 ys,xs=np.where(mask>0)
 if not len(xs):return None
 a=mask[ys.min():ys.max()+1,xs.min():xs.max()+1]
 h,w=a.shape; sc=min(26/max(w,1),26/max(h,1)); nw=max(1,round(w*sc)); nh=max(1,round(h*sc))
 b=cv2.resize(a,(nw,nh),interpolation=cv2.INTER_NEAREST)
 o=np.zeros((32,32),np.uint8); yy=(32-nh)//2; xx=(32-nw)//2; o[yy:yy+nh,xx:xx+nw]=b
 return o

def _templates():
 out=[]
 for fp in FONT_PATHS:
  if not Path(fp).exists():
   continue
  for size in (22,26,30):
   font=ImageFont.truetype(fp,size)
   for lab in LABELS:
    ca=Image.new('L',(80,55),0); ImageDraw.Draw(ca).text((4,-2),RENDER_TEXT[lab],font=font,fill=255)
    n=_norm_mask((np.array(ca)>80).astype(np.uint8)*255)
    if n is not None:out.append((lab,n,cv2.distanceTransform((255-n).astype(np.uint8),cv2.DIST_L2,3)))
 return out
TEMPLATES=_templates()

def _dist(a,b):
 da=cv2.distanceTransform((255-a).astype(np.uint8),cv2.DIST_L2,3)
 db=cv2.distanceTransform((255-b).astype(np.uint8),cv2.DIST_L2,3)
 pa=a>0;pb=b>0
 return float((da[pb].mean() if pb.any() else 99)+(db[pa].mean() if pa.any() else 99)+2*np.mean(pa!=pb))

def _mask_variant(im,x,y,w,h,toplimit=22):
 crop=im[y:y+h,x:x+w]
 if crop.size==0:return None,(0,[])
 gray=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY); mask=(gray<160).astype(np.uint8)*255
 mask[:1,:]=0;mask[:,:1]=0
 n,lab,stats,_=cv2.connectedComponentsWithStats(mask,8); ids=[]
 for i in range(1,n):
  xx,yy,ww,hh,a=(int(v) for v in stats[i])
  if a>=3 and yy<toplimit and yy+hh<=toplimit+2 and xx<20:ids.append(i)
 if not ids:return None,(0,[])
 m=np.isin(lab,ids).astype(np.uint8)*255
 contours,hier=cv2.findContours(m,cv2.RETR_CCOMP,cv2.CHAIN_APPROX_SIMPLE); holes=0
 if hier is not None:holes=sum(1 for z in hier[0] if z[3]>=0)
 areas=sorted([int(stats[i,cv2.CC_STAT_AREA]) for i in ids],reverse=True)
 return _norm_mask(m),(holes,areas)

def _scores(im,x,y,w,h,allowed):
 m,top=_mask_variant(im,x,y,w,h,min(h,22))
 if m is None:return {},top
 by={}
 da=cv2.distanceTransform((255-m).astype(np.uint8),cv2.DIST_L2,3); pa=m>0
 for lab,t,td in TEMPLATES:
  if lab not in allowed:continue
  pb=t>0
  d=float((da[pb].mean() if pb.any() else 99)+(td[pa].mean() if pa.any() else 99)+2*np.mean(pa!=pb));by[lab]=min(by.get(lab,99),d)
 return by,top

def _best(by):
 ss=sorted((d,l) for l,d in by.items())
 if not ss:return None,0.0,[]
 conf=(ss[1][0]-ss[0][0])/(ss[1][0]+1e-6) if len(ss)>1 else 1.0
 return ss[0][1],float(conf),ss

def recognize_rank(im,x,y):
 _,top=_scores(im,x,y,18,22,set(LABELS)); holes,areas=top
 ratio=(areas[1]/areas[0] if len(areas)>1 and areas[0] else 1.0)
 # Old BBO Q is a closed loop plus a small disconnected tail in the corner glyph.
 if holes==1 and len(areas)>=2 and ratio<0.12:
  return 'Q',0.99,'Q_TOPOLOGY'
 if holes>=2:
  return '8',0.99,'EIGHT_TWO_HOLES'
 # Old BBO renders ten as "10": one closed zero plus a non-trivial separate one.
 if holes==1 and len(areas)>=2 and ratio>=0.12:
  return 'T',0.95,'TEN_TOPOLOGY'
 patch=im[y:y+45,x:x+45]
 if patch.size==0:return None,0.0,'NO_PATCH'
 dens=float((cv2.cvtColor(patch,cv2.COLOR_BGR2GRAY)[:,15:45]<150).mean())
 if dens>.30:
  by,_=_scores(im,x,y,15,20,{'J','Q','K'});lab,conf,_=_best(by)
  return (lab if conf>=.12 else None),conf,'FACE_DENSITY'
 # Exclude J/Q/K when there is no face-art evidence. This prevents 9->Q confusion.
 by,_=_scores(im,x,y,24,28,{'A','T','9','8','7','6','5','4','3','2'});lab,conf,_=_best(by)
 return (lab if conf>=.08 else None),conf,'NUMERIC_OR_ACE'

def table_bbox(im):
 hsv=cv2.cvtColor(im,cv2.COLOR_BGR2HSV)
 mask=cv2.inRange(hsv,np.array([35,45,25]),np.array([100,255,230]))
 n,_,stats,_=cv2.connectedComponentsWithStats(mask,8)
 if n<=1:return None
 idx=1+int(np.argmax(stats[1:,cv2.CC_STAT_AREA]));x,y,w,h,_=(int(v) for v in stats[idx])
 if w<400 or h<300:return None
 return x,y,w,h

def horizontal_groups(im,seat):
 tb=table_bbox(im)
 if not tb:return []
 x,y,w,h=tb;x0,x1=int(x+.12*w),int(x+.88*w)
 y0,y1=(y,int(y+.23*h)) if seat=='N' else (int(y+.70*h),y+h)
 r=im[y0:y1,x0:x1]
 white=cv2.inRange(r,np.array([190,190,190]),np.array([255,255,255]))
 n,_,stats,_=cv2.connectedComponentsWithStats(white,8);out=[]
 for j in range(1,n):
  xx,yy,ww,hh,aa=(int(v) for v in stats[j])
  if aa>7000 and 115<=hh<=175 and ww>=80:
   out.append((xx+x0,yy+y0,ww,hh,aa))
 out.sort(key=lambda z:z[0])
 if len(out)!=4:return []
 med=float(np.median([z[1] for z in out]))
 out=[z for z in out if abs(z[1]-med)<=6]
 if len(out)!=4:return []
 # Suit groups must be separated and comparable in height; otherwise no inference.
 hs=[z[3] for z in out]
 if max(hs)-min(hs)>8:return []
 return out

def parse_seat(im,seat):
 gs=horizontal_groups(im,seat)
 if len(gs)!=4:return {'status':'UNAVAILABLE','cards':[], 'groups':0}
 cards=[];details=[]
 for g,suit in zip(gs,SUITS):
  x,y,w,h,_=g;cw=round(h*.736);step=round(h*.243)
  n=max(1,round((w-cw)/max(1,step))+1)
  if n<1 or n>13:return {'status':'UNAVAILABLE','cards':[],'groups':4}
  for k in range(n):
   xx=x+k*step;rank,conf,method=recognize_rank(im,xx,y)
   if rank is None:continue
   card=rank+suit
   cards.append(card);details.append({'card':card,'confidence':round(conf,4),'method':method,'x':xx,'y':y})
 # no card may repeat in one seat
 if len(cards)!=len(set(cards)):
  return {'status':'CONFLICT','cards':[],'groups':4,'details':details}
 return {'status':'PARSED' if cards else 'UNAVAILABLE','cards':cards,'groups':4,'details':details}

def parse_image(path):
 im=cv2.imread(str(path))
 if im is None:return {'status':'UNAVAILABLE','hands':{}}
 hands={};details={}
 for seat in ('N','S'):
  p=parse_seat(im,seat);details[seat]=p
  if p['status']=='PARSED':hands[seat]=p['cards']
 allpairs=[(seat,c) for seat,cards in hands.items() for c in cards]
 allcards=[c for _,c in allpairs]
 if len(allcards)!=len(set(allcards)):
  return {'status':'CONFLICT','hands':{},'details':details}
 total=len(allcards)
 status='PARTIAL_BOARD_OBSERVATION' if total>=4 else 'INSUFFICIENT'
 fp=hashlib.sha256('|'.join(f'{s}:{c}' for s,c in sorted(allpairs)).encode()).hexdigest()[:20] if allpairs else None
 return {'status':status,'hands':hands,'recognized_card_count':total,'state_fingerprint':fp,'details':details}

# --- PDF report mapping / clustering ---
def _decode_pdf_image(doc, xref):
    data=doc.extract_image(xref)
    raw=data.get('image')
    if not raw:return None
    arr=np.frombuffer(raw,np.uint8)
    return cv2.imdecode(arr,cv2.IMREAD_COLOR)

def parse_cv_image(im):
    if im is None:return {'status':'UNAVAILABLE','hands':{}}
    hands={};details={}
    for seat in ('N','S'):
        p=parse_seat(im,seat);details[seat]=p
        if p['status']=='PARSED':hands[seat]=p['cards']
    allpairs=[(seat,c) for seat,cards in hands.items() for c in cards]
    allcards=[c for _,c in allpairs]
    if len(allcards)!=len(set(allcards)):
        return {'status':'CONFLICT','hands':{},'details':details}
    total=len(allcards)
    status='PARTIAL_BOARD_OBSERVATION' if total>=4 else 'INSUFFICIENT'
    fp=hashlib.sha256('|'.join(f'{s}:{c}' for s,c in sorted(allpairs)).encode()).hexdigest()[:20] if allpairs else None
    return {'status':status,'hands':hands,'recognized_card_count':total,'state_fingerprint':fp,'details':details}

def report_observations(doc, master):
    shot={str(x.get('evidence_id')):x for x in master.get('screenshots',[]) if isinstance(x,dict)}
    out=[]
    for pno in range(doc.page_count):
        page=doc[pno]
        blocks=page.get_text('blocks')
        labels=[]
        for b in blocks:
            text=re.sub(r'\s+',' ',str(b[4] or '')).strip()
            m=re.search(r'evidence\s+(frame_[0-9a-f]+)',text)
            if m: labels.append((float(b[1]),float(b[0]),m.group(1),text))
        if not labels:continue
        seen=set()
        for entry in page.get_images(full=True):
            xref=entry[0]
            for rect in page.get_image_rects(xref):
                key=(xref,round(rect.x0,2),round(rect.y0,2));
                if key in seen:continue
                seen.add(key)
                candidates=[]
                for y0,x0,eid,text in labels:
                    dy=y0-float(rect.y1)
                    if -3<=dy<=30:candidates.append((abs(dy),abs(x0-float(rect.x0)),eid,text))
                if not candidates:continue
                _,_,eid,text=min(candidates)
                im=_decode_pdf_image(doc,xref); parsed=parse_cv_image(im)
                if parsed.get('status')!='PARTIAL_BOARD_OBSERVATION':continue
                meta=shot.get(eid,{})
                out.append({
                    'evidence_id':eid,'time':meta.get('time'),'page':pno+1,'xref':xref,
                    'hands':parsed['hands'],'recognized_card_count':parsed['recognized_card_count'],
                    'state_fingerprint':parsed['state_fingerprint'],
                    'parser_status':parsed['status'],
                })
    return out

def _pairs(obs):return {(s,c) for s,cards in (obs.get('hands') or {}).items() for c in cards}
def _cross_seat_conflict(pa,pb):
    aa={c:s for s,c in pa};bb={c:s for s,c in pb}
    return any(c in bb and bb[c]!=s for c,s in aa.items())
def compatible(a,b):
    pa,pb=_pairs(a),_pairs(b)
    if not pa or not pb or _cross_seat_conflict(pa,pb):return False
    inter=len(pa&pb); small=min(len(pa),len(pb))
    return inter>=6 and inter/small>=0.70

def cluster_observations(obs):
    clusters=[]
    for o in sorted(obs,key=lambda z:(float(z.get('time') or 1e12),z.get('page',0),z.get('xref',0))):
        matches=[]
        for i,c in enumerate(clusters):
            anchor=max(c,key=lambda z:z.get('recognized_card_count',0))
            if compatible(o,anchor):matches.append(i)
        if len(matches)==1:clusters[matches[0]].append(o)
        else:clusters.append([o])
    return clusters

def visual_deals_from_clusters(clusters, job_id=''):
    deals=[];qc=[]
    for idx,cl in enumerate(clusters,1):
        union={'N':set(),'E':set(),'S':set(),'W':set()}; evidence=[]
        for o in cl:
            evidence.append(o['evidence_id'])
            for seat,cards in o.get('hands',{}).items():union[seat].update(cards)
        # Reject impossible merge.
        allmap={};conf=False
        for seat,cards in union.items():
            if len(cards)>13:conf=True
            for c in cards:
                if c in allmap and allmap[c]!=seat:conf=True
                allmap[c]=seat
        if conf:
            qc.append({'cluster':idx,'status':'REJECTED_CONFLICT','evidence':evidence});continue
        pairs=sorted(f'{s}:{c}' for s,cards in union.items() for c in cards)
        if not pairs:continue
        fp=hashlib.sha256('|'.join(pairs).encode()).hexdigest()[:20]
        hands={s:(sorted(cards) if cards else None) for s,cards in union.items()}
        deals.append({
            'deal_id':'visualdeal_'+hashlib.sha256((job_id+'|'+fp).encode()).hexdigest()[:20],
            'episode_id':None,'status':'candidate','hands':hands,'auction':None,'contract':None,'declarer':None,
            'opening_lead':None,'result':None,'board_fingerprint':'report-visual:'+fp,
            'platform_board_key':'report-visual:'+fp,
            'reconstruction_rule':'REPORT_VISUAL_CARD_CORNER_EVIDENCE; cluster identity requires >=6 exact seat-card matches and >=70% overlap of the smaller observed state; time/topic/board-number alone never identify a board.',
            'statement_type':'VISUAL_EVIDENCE','evidence':sorted(set(evidence)),
            'visual_observation_count':len(cl),'visual_recognized_cards':len(pairs),
            'visual_seats_observed':[s for s,cards in union.items() if cards],
        })
        qc.append({'cluster':idx,'status':'ACCEPTED_PARTIAL','evidence':evidence,'recognized_cards':len(pairs),'fingerprint':fp})
    return deals,qc


def reconstruct_report_visual_deals(pdf_path: str | Path, master: Mapping[str, Any]) -> dict[str, Any]:
    """Parse report visuals from an existing master PDF without touching source media."""
    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    try:
        observations = report_observations(doc, master)
    finally:
        doc.close()
    clusters = cluster_observations(observations)
    deals, cluster_qc = visual_deals_from_clusters(clusters, str(master.get("job_id") or ""))
    return {
        "method_version": RECONSTRUCTION_METHOD_VERSION,
        "parser_scope": "legacy-bbo-horizontal-N/S-only",
        "observations": observations,
        "deals": deals,
        "qc": {
            "report_visual_observation_count": len(observations),
            "board_cluster_count": len(clusters),
            "accepted_partial_board_count": len(deals),
            "rejected_conflict_cluster_count": sum(x.get("status") == "REJECTED_CONFLICT" for x in cluster_qc),
            "recognized_card_union_total": sum(int(x.get("visual_recognized_cards") or 0) for x in deals),
            "full_board_inference_allowed": False,
            "hidden_hand_complement_inference_allowed": False,
            "time_topic_board_number_identity_allowed": False,
            "cluster_content_overlap_rule": ">=6 exact seat-card matches and >=70% overlap of smaller observed state",
            "cluster_qc": cluster_qc,
        },
    }


__all__ = [
    "RECONSTRUCTION_METHOD_VERSION",
    "parse_cv_image",
    "report_observations",
    "cluster_observations",
    "visual_deals_from_clusters",
    "reconstruct_report_visual_deals",
]
