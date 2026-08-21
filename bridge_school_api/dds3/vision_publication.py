"""Local/free fail-closed extractor for classic publication cross diagrams.

Targets real publication diagrams with N above, S below, W left and E right of a visible
N/W/E/S compass. Reads only image pixels. Missing cards are never repaired from deck
complement and Dealer/Vulnerability are never derived from Board number.
"""
from __future__ import annotations

import hashlib
import itertools
import re
import statistics
from typing import Any

from .screenshot import ObservedField, ScreenshotDealObservation

RANKS = set("AKQJT98765432")
VUL_MAP = {
    "NONE":"None","LOVE":"None","NS":"NS","N/S":"NS","N-S":"NS",
    "EW":"EW","E/W":"EW","E-W":"EW","BOTH":"Both","ALL":"Both",
}
DEALER_MAP = {"N":"N","NORTH":"N","E":"E","EAST":"E","S":"S","SOUTH":"S","W":"W","WEST":"W"}


class PublicationVisionError(ValueError):
    pass


def _deps():
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        import pytesseract  # type: ignore
    except Exception as exc:
        raise PublicationVisionError("LOCAL_VISION_RUNTIME_UNAVAILABLE") from exc
    return cv2,np,pytesseract


def _decode(image_bytes:bytes,cv2:Any,np:Any)->Any:
    if not image_bytes: raise PublicationVisionError("EMPTY_IMAGE")
    image=cv2.imdecode(np.frombuffer(image_bytes,np.uint8),cv2.IMREAD_COLOR)
    if image is None: raise PublicationVisionError("IMAGE_DECODE_FAILED")
    height,width=image.shape[:2]
    if width<250 or height<180: raise PublicationVisionError("IMAGE_TOO_SMALL")
    if width!=700:
        scale=700.0/width
        image=cv2.resize(image,(700,max(1,round(height*scale))),interpolation=cv2.INTER_CUBIC)
    return image


def _ocr_compass(image:Any,pytesseract:Any)->dict[str,tuple[float,float,float]]:
    data=pytesseract.image_to_data(image,config="--psm 11 -c tessedit_char_whitelist=NWES",output_type=pytesseract.Output.DICT)
    labels:dict[str,list[tuple[float,float,float]]]={seat:[] for seat in "NWES"}
    for i,raw in enumerate(data["text"]):
        text=raw.strip().upper()
        if text not in labels: continue
        conf=max(0.0,min(1.0,float(data["conf"][i])/100.0))
        if conf<0.15: continue
        x=int(data["left"][i]); y=int(data["top"][i]); w=int(data["width"][i]); h=int(data["height"][i])
        labels[text].append((x+w/2,y+h/2,conf))
    if any(not labels[seat] for seat in "NWES"): raise PublicationVisionError("UNSUPPORTED_LAYOUT_NO_COMPASS")
    best=None
    for n,w,e,s in itertools.product(labels["N"],labels["W"],labels["E"],labels["S"]):
        if not (n[1]<s[1] and w[0]<e[0]): continue
        cx=(n[0]+s[0]+w[0]+e[0])/4; cy=(n[1]+s[1]+w[1]+e[1])/4
        span_x=e[0]-w[0]; span_y=s[1]-n[1]
        if not (10<=span_x<=image.shape[1]*0.35 and 10<=span_y<=image.shape[0]*0.35): continue
        score=(abs(n[0]-s[0])+abs(w[1]-e[1])+abs((cy-n[1])-(s[1]-cy))+abs((cx-w[0])-(e[0]-cx))-20*min(n[2],w[2],e[2],s[2]))
        candidate={"N":n,"W":w,"E":e,"S":s}
        if best is None or score<best[0]: best=(score,candidate)
    if best is None: raise PublicationVisionError("UNSUPPORTED_LAYOUT_NO_COMPASS_CLUSTER")
    return best[1]


def _clean_rank_text(text:str)->str:
    value=re.sub(r"\s+","",text.upper()).replace("10","T")
    return value if all(ch in RANKS for ch in value) else ""


def _rank_tokens(image:Any,pytesseract:Any)->list[dict[str,Any]]:
    height=image.shape[0]
    boxes=pytesseract.image_to_boxes(image,config="--psm 11 -c tessedit_char_whitelist=AKQJT9876543210")
    tokens=[]
    for line in boxes.splitlines():
        fields=line.split()
        if len(fields)<5: continue
        char=fields[0].upper()
        if char=="0": char="T"
        if char not in RANKS: continue
        try: x1,y1,x2,y2=map(int,fields[1:5])
        except ValueError: continue
        top=height-y2; bottom=height-y1
        tokens.append({"text":char,"x":x1,"right":x2,"cx":(x1+x2)/2,"cy":(top+bottom)/2})
    return tokens


def _cluster_rows(tokens:list[dict[str,Any]],tolerance:float=13.0)->list[list[dict[str,Any]]]:
    clusters=[]
    for token in sorted(tokens,key=lambda item:(item["cy"],item["x"])):
        for cluster in clusters:
            center=sum(item["cy"] for item in cluster)/len(cluster)
            if abs(token["cy"]-center)<=tolerance:
                cluster.append(token); break
        else: clusters.append([token])
    for cluster in clusters: cluster.sort(key=lambda item:item["x"])
    clusters.sort(key=lambda cluster:sum(item["cy"] for item in cluster)/len(cluster))
    return clusters


def _row_center(row:list[dict[str,Any]])->float:
    return sum(token["cy"] for token in row)/len(row)


def _glyph_column(rows:list[list[dict[str,Any]]])->float:
    left=[float(min(token["x"] for token in row)) for row in rows if row]
    if len(left)<2: raise PublicationVisionError("UNSTABLE_SUIT_GLYPH_COLUMN")
    med=float(statistics.median(left))
    inliers=[value for value in left if abs(value-med)<=8.0]
    if len(inliers)<2: raise PublicationVisionError("UNSTABLE_SUIT_GLYPH_COLUMN")
    return float(statistics.median(inliers))


def _ocr_holding_crop(image:Any,cy:float,x0:int,pytesseract:Any,cv2:Any,*,y_radius:int=17)->tuple[str,float]:
    height,width=image.shape[:2]
    y0=max(0,int(cy-y_radius)); y1=min(height,int(cy+y_radius+1)); x1=min(width,int(x0+width*0.30))
    crop=image[y0:y1,max(0,x0):x1]
    if not crop.size: raise PublicationVisionError("EMPTY_HOLDING_CROP")
    crop=cv2.resize(crop,None,fx=3,fy=3,interpolation=cv2.INTER_CUBIC)
    gray=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY)
    _,binary=cv2.threshold(gray,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    candidates=[]
    for source in (gray,binary):
        for psm in (7,11):
            text=_clean_rank_text(pytesseract.image_to_string(source,config=f"--psm {psm} -c tessedit_char_whitelist=AKQJT9876543210"))
            candidates.append(text)
    nonempty=[value for value in candidates if value]
    if not nonempty: return "",0.70
    counts={value:nonempty.count(value) for value in set(nonempty)}
    best=max(counts,key=counts.get)
    if len(counts)>1 and counts[best]<2: raise PublicationVisionError(f"AMBIGUOUS_CARD_OCR:{candidates}")
    return best,0.70 if len(counts)==1 else 0.62


def _ocr_holding_row(image:Any,row:list[dict[str,Any]],glyph_x:float,pytesseract:Any,cv2:Any)->tuple[str,float]:
    if not row: return "",0.70
    return _ocr_holding_crop(image,_row_center(row),max(0,int(glyph_x+18)),pytesseract,cv2)


def _retry_hand_from_rank_start(image:Any,rows:list[list[dict[str,Any]]],pytesseract:Any,cv2:Any)->tuple[list[str],list[float]]:
    """Retry a failed hand from visible rank geometry without deck completion.

    The retry uses a narrow single-row crop so neighbouring suit rows cannot supply
    characters. A small positive offset removes a visible suit glyph when rank-only box
    OCR has mistaken it for a rank. The retry is used only after the normal 13-card count
    fails, and still requires independent OCR agreement plus final 52-unique validation.
    """
    starts=[float(min(token["x"] for token in row)) for row in rows if row]
    if len(starts)<2: raise PublicationVisionError("UNSTABLE_RANK_START_COLUMN")
    med=float(statistics.median(starts))
    inliers=[value for value in starts if abs(value-med)<=14.0]
    if len(inliers)<2: raise PublicationVisionError("UNSTABLE_RANK_START_COLUMN")
    start=float(statistics.median(inliers))
    holdings=[]; confs=[]
    for row in rows:
        if not row: return [],[]
        value,conf=_ocr_holding_crop(image,_row_center(row),max(0,int(start+9)),pytesseract,cv2,y_radius=7)
        holdings.append(value); confs.append(min(conf,0.66))
    return holdings,confs


def _extract_hands(image:Any,compass:dict[str,tuple[float,float,float]],pytesseract:Any,cv2:Any)->tuple[dict[str,dict[str,str]],dict[str,dict[str,float]]]:
    tokens=_rank_tokens(image,pytesseract)
    height,width=image.shape[:2]
    center_x=sum(value[0] for value in compass.values())/4
    center_y=sum(value[1] for value in compass.values())/4
    span_x=max(value[0] for value in compass.values())-min(value[0] for value in compass.values())
    side_gap=max(32.0,span_x*0.80)
    north_tokens=[t for t in tokens if t["cy"]<compass["N"][1] and abs(t["cx"]-center_x)<width*0.18 and compass["N"][1]-t["cy"]<height*0.34]
    north_rows=_cluster_rows(north_tokens)[-4:]
    if len(north_rows)!=4: raise PublicationVisionError(f"INCOMPLETE_HAND_ROWS:N:{len(north_rows)}")
    south_tokens=[t for t in tokens if t["cy"]>compass["S"][1] and abs(t["cx"]-center_x)<width*0.18 and t["cy"]-compass["S"][1]<height*0.34]
    south_rows=_cluster_rows(south_tokens)[:4]
    if len(south_rows)!=4: raise PublicationVisionError(f"INCOMPLETE_HAND_ROWS:S:{len(south_rows)}")
    lateral_tokens=[t for t in tokens if abs(t["cx"]-center_x)>side_gap and abs(t["cy"]-center_y)<height*0.24]
    lateral_rows=_cluster_rows(lateral_tokens)
    if len(lateral_rows)<4: raise PublicationVisionError(f"INCOMPLETE_LATERAL_GRID:{len(lateral_rows)}")
    lateral_rows=sorted(lateral_rows,key=lambda row:abs(_row_center(row)-center_y))[:4]; lateral_rows.sort(key=_row_center)
    raw_rows={
        "N":north_rows,"S":south_rows,
        "W":[[token for token in row if token["cx"]<center_x] for row in lateral_rows],
        "E":[[token for token in row if token["cx"]>center_x] for row in lateral_rows],
    }
    glyph_x={hand:_glyph_column(rows) for hand,rows in raw_rows.items()}
    hands={}; confidence={}; cards=[]
    for hand in "NESW":
        holdings=[]; row_conf=[]
        for row in raw_rows[hand]:
            value,conf=_ocr_holding_row(image,row,glyph_x[hand],pytesseract,cv2)
            holdings.append(value); row_conf.append(conf)
        if sum(len(value) for value in holdings)!=13:
            try:
                retry_holdings,retry_conf=_retry_hand_from_rank_start(image,raw_rows[hand],pytesseract,cv2)
            except PublicationVisionError:
                retry_holdings,retry_conf=[],[]
            if retry_holdings and sum(len(value) for value in retry_holdings)==13:
                holdings,row_conf=retry_holdings,retry_conf
        if sum(len(value) for value in holdings)!=13:
            raise PublicationVisionError(f"INCOMPLETE_HAND:{hand}:{'.'.join(holdings)}")
        hands[hand]=dict(zip("SHDC",holdings,strict=True)); confidence[hand]=dict(zip("SHDC",row_conf,strict=True))
        for suit,ranks in zip("SHDC",holdings,strict=True): cards.extend(suit+rank for rank in ranks)
    expected={suit+rank for suit in "SHDC" for rank in "AKQJT98765432"}
    if len(cards)!=52 or len(set(cards))!=52: raise PublicationVisionError(f"DECK_VALIDATION_FAILED:{len(cards)}/{len(set(cards))}")
    if set(cards)!=expected: raise PublicationVisionError("DECK_VALIDATION_FAILED:NOT_STANDARD_DECK")
    return hands,confidence


def _extract_metadata(image:Any,pytesseract:Any)->tuple[int,str,str,float]:
    text=pytesseract.image_to_string(image,config="--psm 6").replace("\n"," ")
    board_match=re.search(r"\bBoard\s*[:#.]?\s*(\d{1,3})\b",text,re.IGNORECASE)
    dealer_match=re.search(r"\bDealer\s*[:.]?\s*(North|East|South|West|[NESW])\b",text,re.IGNORECASE)
    vul_match=re.search(r"\bVul(?:nerable|nerability)?\s*[:.]?\s*(None|Love|N\s*[-/]?\s*S|E\s*[-/]?\s*W|Both|All)\b",text,re.IGNORECASE)
    if vul_match is None: vul_match=re.search(r"\b(None|Love|N\s*[-/]?\s*S|E\s*[-/]?\s*W|Both|All)\s+Vul\b",text,re.IGNORECASE)
    if not board_match or not dealer_match or not vul_match: raise PublicationVisionError(f"METADATA_OCR_FAILED:{text[:240]!r}")
    board=int(board_match.group(1)); dealer=DEALER_MAP.get(dealer_match.group(1).upper())
    vulnerability=VUL_MAP.get(re.sub(r"\s+","",vul_match.group(1).upper()))
    if dealer is None or vulnerability is None: raise PublicationVisionError("METADATA_OCR_INVALID")
    return board,dealer,vulnerability,0.80


def extract_publication_cross_observation(image_bytes:bytes,*,media_type:str,filename:str|None=None)->ScreenshotDealObservation:
    cv2,np,pytesseract=_deps(); image=_decode(image_bytes,cv2,np); compass=_ocr_compass(image,pytesseract)
    hands,hand_confidence=_extract_hands(image,compass,pytesseract,cv2)
    board,dealer,vulnerability,metadata_confidence=_extract_metadata(image,pytesseract)
    image_sha256=hashlib.sha256(image_bytes).hexdigest(); source="local_tesseract_publication_cross_v1"
    return ScreenshotDealObservation(
        hands=hands,
        board_number=ObservedField(board,confidence=metadata_confidence,source=source),
        dealer=ObservedField(dealer,confidence=metadata_confidence,source=source),
        vulnerability=ObservedField(vulnerability,confidence=metadata_confidence,source=source),
        hand_confidence=hand_confidence,
        extra_metadata={
            "vision_extractor":ObservedField(source,confidence=1.0,source="runtime"),
            "image_sha256":ObservedField(image_sha256,confidence=1.0,source="runtime"),
            "filename":ObservedField(filename,confidence=1.0,source="runtime"),
            "media_type":ObservedField(media_type,confidence=1.0,source="runtime"),
        },
    )
