#!/usr/bin/env python3
from __future__ import annotations
import tempfile
from pathlib import Path
import fitz

from bridge_school_api.dds3 import vision_publication_grid as vg
from tools.dds3_vision.evaluate_publication_cross import SAMPLES,_download,_find_clip,_truth_hands

sample=SAMPLES[2]
with tempfile.TemporaryDirectory(prefix='dds3-grid-row-') as temp:
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
        print('ROW_DIAG_IMAGE',image.shape,'TRUTH',truth['N'],'GLYPH',glyph)
        # Only the two failing rows: diamonds and clubs.
        for idx in (2,3):
            row=rows['N'][idx]; cy=vg._row_center(row); h,w=image.shape[:2]
            print('ROW',idx,'CY',cy,'TOKENS',[(t['text'],t['x'],round(t['cx'],1),round(t['cy'],1)) for t in row])
            for guard in (12,18,24):
                y0=max(0,int(cy-22)); y1=min(h,int(cy+22)); x0=max(0,int(glyph+guard)); x1=min(w,int(x0+w*0.32))
                crop=image[y0:y1,x0:x1]; gray=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY)
                _,otsu=cv2.threshold(gray,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
                for scale in (2,3):
                    for label,src in (('g',gray),('o',otsu)):
                        big=cv2.resize(src,None,fx=scale,fy=scale,interpolation=cv2.INTER_CUBIC)
                        vals=[]
                        for psm in (7,8,13):
                            raw=pytesseract.image_to_string(big,config=f'--psm {psm}').strip().replace('\n','|')
                            wl=pytesseract.image_to_string(big,config=f'--psm {psm} -c tessedit_char_whitelist=AKQJT9876543210').strip().replace('\n','|')
                            vals.append((psm,raw,wl))
                        print(' CAND',guard,scale,label,vals)
        break
