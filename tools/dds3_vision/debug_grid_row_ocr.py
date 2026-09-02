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
        rows=vg._grid_rows(image,pytesseract)
        print('GRID_DIAG_IMAGE',image.shape,'TRUTH',truth)
        for hand in 'NESW':
            glyph=vg._glyph_column(rows[hand])
            expected=truth[hand].split('.')
            observed=[]
            for suit,row,truth_holding in zip('SHDC',rows[hand],expected,strict=True):
                try:
                    value,confidence=vg._ocr_grid_holding_row(image,row,glyph,pytesseract,cv2)
                    observed.append(value)
                    print('ROW',hand,suit,'truth',truth_holding,'observed',value,'confidence',confidence,'match',value==truth_holding)
                except Exception as exc:
                    observed.append('ERROR')
                    print('ROW',hand,suit,'truth',truth_holding,'ERROR',type(exc).__name__,str(exc))
            print('HAND',hand,'truth',truth[hand],'observed','.'.join(observed))
        break
