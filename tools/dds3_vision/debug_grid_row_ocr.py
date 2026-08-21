#!/usr/bin/env python3
from __future__ import annotations
import tempfile
from pathlib import Path
import fitz

from bridge_school_api.dds3 import vision_publication_grid as vg
from tools.dds3_vision.evaluate_publication_cross import SAMPLES,_download,_find_clip,_truth_hands

sample=SAMPLES[2]
with tempfile.TemporaryDirectory(prefix='dds3-grid-component-') as temp:
    root=Path(temp); pdf=root/'source.pdf'; _download(sample.url,pdf)
    doc=fitz.open(pdf)
    for page_index,page in enumerate(doc):
        clip=_find_clip(page,sample.title)
        if clip is None: continue
        truth=_truth_hands(page,clip)
        pix=page.get_pixmap(matrix=fitz.Matrix(220/72,220/72),clip=clip,alpha=False)
        image_bytes=pix.tobytes('png')
        cv2,np,pytesseract=vg._deps(); image=vg._decode_grid(image_bytes,cv2,np)
        rows=vg._grid_rows(image,pytesseract); glyph=vg._glyph_column(rows['N'])
        row=rows['N'][3]; cy=vg._row_center(row); h,w=image.shape[:2]
        x0=max(0,int(glyph+w*0.05)); x1=min(w,int(x0+w*0.22)); y0=max(0,int(cy-w*0.035)); y1=min(h,int(cy+w*0.035))
        crop=image[y0:y1,x0:x1]; gray=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY)
        _,inv=cv2.threshold(gray,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)
        n,labels,stats,centroids=cv2.connectedComponentsWithStats(inv,8)
        comps=[]
        for i in range(1,n):
            x,y,cw,ch,area=stats[i]
            if area<20 or ch<max(8,int(crop.shape[0]*0.25)) or cw<2: continue
            comps.append((x,y,cw,ch,area))
        comps.sort()
        print('COMP_DIAG_IMAGE',image.shape,'TRUTH',truth['N'],'GLYPH',glyph,'CROP',(x0,y0,x1,y1),'COMPS',comps)
        for idx,(x,y,cw,ch,area) in enumerate(comps):
            pad=5
            part=gray[max(0,y-pad):min(gray.shape[0],y+ch+pad),max(0,x-pad):min(gray.shape[1],x+cw+pad)]
            vals=[]
            for scale in (3,5):
                big=cv2.resize(part,None,fx=scale,fy=scale,interpolation=cv2.INTER_CUBIC)
                for psm in (10,13):
                    raw=pytesseract.image_to_string(big,config=f'--psm {psm}').strip().replace('\n','|')
                    wl=pytesseract.image_to_string(big,config=f'--psm {psm} -c tessedit_char_whitelist=AKQJT9876543210').strip().replace('\n','|')
                    digits=pytesseract.image_to_string(big,config=f'--psm {psm} -c tessedit_char_whitelist=98765432').strip().replace('\n','|')
                    vals.append((scale,psm,raw,wl,digits))
            print('COMP',idx,(x,y,cw,ch,area),vals)
        break
