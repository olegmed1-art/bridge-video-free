#!/usr/bin/env python3
from __future__ import annotations
import tempfile
from pathlib import Path
import fitz

from tools.dds3_vision.evaluate_publication_cross import SAMPLES, _download, _find_clip, _truth_hands

sample=SAMPLES[2]
with tempfile.TemporaryDirectory(prefix='dds3-grid-debug-') as temp:
    root=Path(temp); pdf=root/'source.pdf'; _download(sample.url,pdf)
    doc=fitz.open(pdf)
    for page_index,page in enumerate(doc):
        clip=_find_clip(page,sample.title)
        if clip is None: continue
        truth=_truth_hands(page,clip)
        pix=page.get_pixmap(matrix=fitz.Matrix(220/72,220/72),clip=clip,alpha=False)
        image_bytes=pix.tobytes('png')
        import cv2,numpy as np,pytesseract
        image=cv2.imdecode(np.frombuffer(image_bytes,np.uint8),cv2.IMREAD_COLOR)
        if image.shape[1]!=700:
            scale=700/image.shape[1]; image=cv2.resize(image,(700,round(image.shape[0]*scale)),interpolation=cv2.INTER_CUBIC)
        print('DEBUG_GRID_PAGE',page_index+1,'TRUTH',truth,'IMAGE',image.shape,'CLIP',tuple(round(v,1) for v in clip))
        text=pytesseract.image_to_string(image,config='--psm 6')
        print('DEBUG_GRID_TEXT',repr(text[:1600]))
        data=pytesseract.image_to_data(image,config='--psm 11',output_type=pytesseract.Output.DICT)
        rows=[]
        for i,raw in enumerate(data['text']):
            t=raw.strip()
            if not t: continue
            rows.append((t,int(data['left'][i]),int(data['top'][i]),int(data['width'][i]),int(data['height'][i]),data['conf'][i]))
        print('DEBUG_GRID_WORDS',rows[:240])
        boxes=pytesseract.image_to_boxes(image,config='--psm 11 -c tessedit_char_whitelist=AKQJT9876543210')
        print('DEBUG_GRID_RANK_BOXES',boxes[:5000])
        break
