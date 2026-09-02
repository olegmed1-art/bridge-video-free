#!/usr/bin/env python3
"""Field-evaluate the appeals-form cross layout on a public real bridge PDF.

Canonical truth is derived independently from embedded PDF vector text. OCR output and
DDS3 never create or repair truth. Rendered images exist only in the temporary CI job.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import tempfile
import urllib.request
from pathlib import Path

import fitz
from PIL import Image
import pytesseract

from bridge_school_api.dds3.image_ingress import _extract_local_observation
from bridge_school_api.dds3.vision_appeals_cross import AppealsCrossVisionError, _ocr_appeals_compass
from bridge_school_api.dds3.vision_appeals_cross_v4 import (
    _context_tokens,
    _read_row,
    extract_appeals_cross_observation,
)
from bridge_school_api.dds3.vision_publication import _decode, _deps
from tools.dds3_vision.evaluate_publication_cross import SUIT_GLYPHS, _cluster_rows

SOURCE_URL = "https://www.bridge.is/files/EBUAppeals2001_1575249453.pdf"
PAGE_INDEX = 7
EXPECTED_BOARD = 2
RANKS = set("AKQJT98765432")
DEALER_MAP = {"N":"N","NORTH":"N","E":"E","EAST":"E","S":"S","SOUTH":"S","W":"W","WEST":"W"}
VUL_MAP = {"NONE":"None","LOVE":"None","NS":"NS","N/S":"NS","N-S":"NS","EW":"EW","E/W":"EW","E-W":"EW","BOTH":"Both","ALL":"Both"}


def _download(path: Path) -> None:
    req = urllib.request.Request(SOURCE_URL, headers={"User-Agent":"bridge-school-dds3-field-gate/1.0"})
    with urllib.request.urlopen(req, timeout=45) as response, path.open("wb") as output:
        output.write(response.read())


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("–", "-").replace("—", "-")).strip()


def _truth_metadata(page: fitz.Page) -> tuple[int, str, str]:
    text = _norm(page.get_text("text"))
    board_match = re.search(r"\bBoard\s*no\s*(\d{1,3})\b", text, re.IGNORECASE)
    dealer_match = re.search(r"\bDealer\s*(North|East|South|West|[NESW])\b", text, re.IGNORECASE)
    vul_match = re.search(r"\b(None|Love|N\s*[-/]?\s*S|E\s*[-/]?\s*W|Both|All)\s+vulnerable\b", text, re.IGNORECASE)
    if not board_match or not dealer_match or not vul_match:
        raise ValueError("explicit appeals metadata missing from source vector text")
    board = int(board_match.group(1)); dealer = DEALER_MAP[dealer_match.group(1).upper()]
    vulnerability = VUL_MAP[re.sub(r"\s+", "", vul_match.group(1).upper())]
    if board != EXPECTED_BOARD: raise ValueError(f"unexpected board {board}")
    return board, dealer, vulnerability


def _source_hand_rows(page: fitz.Page) -> list[dict]:
    rows: list[dict] = []
    for word in page.get_text("words"):
        raw = word[4].strip()
        if len(raw) < 1 or raw[0] not in SUIT_GLYPHS: continue
        suit = SUIT_GLYPHS[raw[0]]
        holding = re.sub(r"\s+", "", raw[1:].upper()).replace("10", "T")
        if any(char not in RANKS for char in holding): continue
        rows.append({"x":(word[0]+word[2])/2,"y":(word[1]+word[3])/2,"suit":suit,"holding":holding,"word":word})
    if len(rows) != 16: raise ValueError(f"expected 16 combined suit/holding vector rows, got {len(rows)}")
    return rows


def _truth_hands(page: fitz.Page) -> dict[str, str]:
    grouped = _cluster_rows(_source_hand_rows(page)); hands: dict[str, str] = {}; cards: list[str] = []
    for hand, hand_rows in grouped.items():
        suits = {row["suit"]:row["holding"] for row in hand_rows}
        if set(suits) != set("SHDC"): raise ValueError(f"source missing suit row for {hand}")
        holding = ".".join(suits[suit] for suit in "SHDC")
        if sum(len(part) for part in holding.split(".")) != 13: raise ValueError(f"source hand {hand} is not 13 cards: {holding}")
        hands[hand] = holding
        for suit,ranks in zip("SHDC",holding.split("."),strict=True): cards.extend(suit+rank for rank in ranks)
    expected={suit+rank for suit in "SHDC" for rank in "AKQJT98765432"}
    if len(cards)!=52 or len(set(cards))!=52 or set(cards)!=expected: raise ValueError(f"source deck invalid: {len(cards)}/{len(set(cards))}")
    return hands


def _deal_clip(page: fitz.Page) -> fitz.Rect:
    words=[row["word"] for row in _source_hand_rows(page)]; y0=min(w[1] for w in words); y1=max(w[3] for w in words)
    return fitz.Rect(0,max(0,y0-105),page.rect.width,min(page.rect.height,y1+30))


def _observation_hands(observation) -> dict[str,str]:
    return {seat:".".join(observation.hands[seat][suit] for suit in "SHDC") for seat in "NESW"}


def _negative_crop(image_bytes:bytes)->bytes:
    image=Image.open(io.BytesIO(image_bytes)).convert("RGB"); cropped=image.crop((0,0,image.width,max(1,int(image.height*0.78))))
    buf=io.BytesIO(); cropped.save(buf,format="PNG"); return buf.getvalue()


def _raster_token_diagnostics(image:Image.Image)->list[dict]:
    data=pytesseract.image_to_data(image,config="--psm 6",output_type=pytesseract.Output.DICT)
    out=[]
    for i,raw in enumerate(data["text"]):
        text=raw.strip()
        if not text: continue
        top=int(data["top"][i]); left=int(data["left"][i]); width=int(data["width"][i]); height=int(data["height"][i])
        if 80 <= top <= 520:
            out.append({"t":text,"x":left,"y":top,"w":width,"h":height,"conf":data["conf"][i]})
    return out[:120]


def _pixel_row_diagnostics(image_bytes: bytes) -> dict[str, list[str]]:
    """Expose the 16 direct row readings when the final deck gate rejects.

    This is diagnostic evidence only. It deliberately stops before any deck-level repair
    and is not used to select or modify the production observation.
    """
    cv2, np, tess = _deps()
    image = _decode(image_bytes, cv2, np)
    compass = _ocr_appeals_compass(image, tess, cv2)
    n, w, e, s = (compass[seat] for seat in "NWES")
    span = s[1] - n[1]
    axis_x = (n[0] + s[0]) / 2
    center_y = (n[1] + s[1]) / 2
    starts = {
        "N": axis_x - 0.80 * span,
        "S": axis_x - 0.80 * span,
        "W": axis_x - 3.10 * span,
        "E": axis_x + 1.50 * span,
    }
    rows = {
        "N": [n[1] - factor * span for factor in (1.53, 1.16, 0.80, 0.43)],
        "S": [s[1] + factor * span for factor in (0.43, 0.80, 1.16, 1.53)],
        "W": [center_y + factor * span for factor in (-0.55, -0.18, 0.18, 0.55)],
        "E": [center_y + factor * span for factor in (-0.55, -0.18, 0.18, 0.55)],
    }
    contexts = [_context_tokens(image, tess, psm=psm) for psm in (3, 6, 11)]
    output: dict[str, list[str]] = {}
    for seat in "NESW":
        values: list[str] = []
        for cy in rows[seat]:
            try:
                value, confidence = _read_row(
                    image,
                    x0=starts[seat],
                    cy=cy,
                    span=span,
                    pytesseract=tess,
                    cv2=cv2,
                    context_token_sets=contexts,
                )
                values.append(f"{value}@{confidence:.2f}")
            except Exception as exc:
                values.append(f"ERROR:{type(exc).__name__}:{exc}")
        output[seat] = values
    return output


def main()->int:
    parser=argparse.ArgumentParser(); parser.add_argument("--output",type=Path); parser.add_argument("--dpi",type=int,default=300); args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="dds3-appeals-field-") as temp:
        root=Path(temp); pdf=root/"source.pdf"; _download(pdf); source_sha=hashlib.sha256(pdf.read_bytes()).hexdigest()
        doc=fitz.open(pdf); page=doc[PAGE_INDEX]; clip=_deal_clip(page); truth_hands=_truth_hands(page); board,dealer,vulnerability=_truth_metadata(page)
        pix=page.get_pixmap(matrix=fitz.Matrix(args.dpi/72,args.dpi/72),clip=clip,alpha=False); image_bytes=pix.tobytes("png"); image_sha=hashlib.sha256(image_bytes).hexdigest()
        raster_image=Image.open(io.BytesIO(image_bytes)).convert("RGB")
        raster_ocr=pytesseract.image_to_string(raster_image,config="--psm 6")[:1600]
        raster_tokens=_raster_token_diagnostics(raster_image)
        try:
            observed=extract_appeals_cross_observation(image_bytes,media_type="image/png",filename="ebu-appeals-2001-board-2.png")
            routed=_extract_local_observation(image_bytes,media_type="image/png",filename="ebu-appeals-2001-board-2.png")
            observed_hands=_observation_hands(observed); routed_source=routed.extra_metadata["vision_extractor"].value
            hands_exact=observed_hands==truth_hands
            metadata_exact=(int(observed.board_number.value)==board and str(observed.dealer.value)==dealer and str(observed.vulnerability.value)==vulnerability)
            routing_exact=routed_source=="local_tesseract_appeals_cross_v4"; status="exact" if hands_exact and metadata_exact and routing_exact else "wrong_accept"
            result={"status":status,"hands_exact":hands_exact,"metadata_exact":metadata_exact,"production_routing_exact":routing_exact,"board":board,"source_sha256":source_sha,"image_sha256":image_sha,"truth_hands":truth_hands,"observed_hands":observed_hands}
        except AppealsCrossVisionError as exc:
            result={"status":"rejected","reason":str(exc),"source_sha256":source_sha,"image_sha256":image_sha,"truth_hands":truth_hands,"pixel_row_diagnostics":_pixel_row_diagnostics(image_bytes),"raster_ocr":raster_ocr,"raster_tokens":raster_tokens}
        except Exception as exc:
            result={"status":"field_error","reason":f"{type(exc).__name__}:{exc}","source_sha256":source_sha,"image_sha256":image_sha,"truth_hands":truth_hands,"raster_ocr":raster_ocr,"raster_tokens":raster_tokens}
        try:
            _extract_local_observation(_negative_crop(image_bytes),media_type="image/png",filename="ebu-appeals-2001-board-2-crop.png"); negative_status="wrong_accept"
        except Exception: negative_status="rejected"
        result["negative_crop"]=negative_status
    exact=int(result.get("status")=="exact"); wrong=int(result.get("status")=="wrong_accept"); negative_pass=int(result.get("negative_crop")=="rejected")
    report={"extractor":"local_tesseract_appeals_cross_v4","layout_family":"ebu_appeals_form_cross","real_public_sources":1,"real_board_pages":1,"exact":exact,"wrong_accepts":wrong,"negative_crop_rejected":negative_pass,"truth_source":"embedded source PDF vector text","dds3_used_for_truth":False,"bridge_inference_repair":False,"paid_or_cloud_vision":False,"result":result}
    text=json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True); print(text)
    if args.output: args.output.write_text(text+"\n",encoding="utf-8")
    return 0 if exact==1 and wrong==0 and negative_pass==1 else 3

if __name__=="__main__": raise SystemExit(main())
