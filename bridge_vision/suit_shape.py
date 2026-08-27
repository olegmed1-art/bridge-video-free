"""Bounded suit-shape classifier boundary for graphic bridge cards."""
from __future__ import annotations
from typing import Mapping,Any
SUIT_SHAPE_VERSION="bridge-suit-shape-v1";_FAMILIES={"red":("H","D"),"black":("S","C")}
def classify_suit(scores:Mapping[str,Any],*,colour_family:str,min_score:float=.90,min_margin:float=.08)->dict[str,Any]:
    if colour_family not in _FAMILIES:raise ValueError("colour_family must be red or black")
    if not 0<=min_score<=1 or not 0<=min_margin<=1:raise ValueError("invalid suit classifier threshold")
    parsed=[]
    for suit in _FAMILIES[colour_family]:
        try:value=float(scores.get(suit))
        except (TypeError,ValueError):value=-1.0
        if not 0<=value<=1:value=-1.0
        parsed.append((suit,value))
    parsed.sort(key=lambda i:i[1],reverse=True);best,second=parsed
    evidence={"classifier_version":SUIT_SHAPE_VERSION,"colour_family":colour_family,"scores":dict(parsed),"min_score":min_score,"min_margin":min_margin}
    if best[1]<min_score:return {"suit":None,"confidence":0.0,"reason":"LOW_SUIT_SCORE","evidence":evidence}
    if best[1]-second[1]<min_margin:return {"suit":None,"confidence":0.0,"reason":"AMBIGUOUS_SUIT_SHAPE","evidence":evidence}
    return {"suit":best[0],"confidence":best[1],"reason":None,"evidence":evidence}
__all__=["SUIT_SHAPE_VERSION","classify_suit"]
