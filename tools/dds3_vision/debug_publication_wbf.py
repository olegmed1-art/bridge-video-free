#!/usr/bin/env python3
from __future__ import annotations
import tempfile
from pathlib import Path
import fitz

from bridge_school_api.dds3 import vision_publication as vp
from tools.dds3_vision.evaluate_publication_cross import SAMPLES, _download, _find_clip, _truth_hands

sample = SAMPLES[0]
with tempfile.TemporaryDirectory(prefix='dds3-pub-debug-') as temp:
    root = Path(temp)
    pdf = root / 'source.pdf'
    _download(sample.url, pdf)
    doc = fitz.open(pdf)
    for page_index, page in enumerate(doc):
        clip = _find_clip(page, sample.title)
        if clip is None:
            continue
        truth = _truth_hands(page, clip)
        pix = page.get_pixmap(matrix=fitz.Matrix(220/72, 220/72), clip=clip, alpha=False)
        image_bytes = pix.tobytes('png')
        cv2,np,pytesseract = vp._deps()
        image = vp._decode(image_bytes, cv2, np)
        compass = vp._ocr_compass(image, pytesseract)
        tokens = vp._rank_tokens(image, pytesseract)
        print('DEBUG_PAGE', page_index+1, 'TRUTH', truth)
        print('DEBUG_COMPASS', compass, 'IMAGE', image.shape)
        center_x=sum(v[0] for v in compass.values())/4
        center_y=sum(v[1] for v in compass.values())/4
        span_x=max(v[0] for v in compass.values())-min(v[0] for v in compass.values())
        side_gap=max(32.0,span_x*0.80)
        height,width=image.shape[:2]
        groups={
            'N':[t for t in tokens if t['cy']<compass['N'][1] and abs(t['cx']-center_x)<width*0.18 and compass['N'][1]-t['cy']<height*0.34],
            'S':[t for t in tokens if t['cy']>compass['S'][1] and abs(t['cx']-center_x)<width*0.18 and t['cy']-compass['S'][1]<height*0.34],
            'L':[t for t in tokens if abs(t['cx']-center_x)>side_gap and abs(t['cy']-center_y)<height*0.24],
        }
        for name, group in groups.items():
            rows=vp._cluster_rows(group)
            print('DEBUG_GROUP', name)
            for row in rows:
                print(' ROW', round(vp._row_center(row),1), [(t['text'],t['x'],round(t['cx'],1),round(t['cy'],1)) for t in row])
        break
