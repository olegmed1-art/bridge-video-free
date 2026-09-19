"""Bounded pixel primitives for visible Bridgit card panels.

Geometry only: no card identity is produced here. Bridgit's overlapping cards
have persistent dark left borders even when rank/suit glyphs fragment the white
interior, so edge persistence is the stronger locator signal.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Sequence

BRIDGIT_PIXEL_VERSION = "bridgit-visible-panels-v2"

@dataclass(frozen=True)
class PanelStart:
    x: int
    y: int
    exposed_width: int
    exposed_height: int

def _is_card_white(rgb: Sequence[int], *, floor: int = 238, spread: int = 12) -> bool:
    if len(rgb) < 3: return False
    r,g,b=(int(rgb[i]) for i in range(3))
    return min(r,g,b)>=floor and max(r,g,b)-min(r,g,b)<=spread

def white_runs(row: Iterable[Sequence[int]], *, min_width: int = 8) -> list[tuple[int,int]]:
    runs=[]; start=None
    for x,rgb in enumerate(row):
        white=_is_card_white(rgb)
        if white and start is None: start=x
        elif not white and start is not None:
            if x-start>=min_width: runs.append((start,x))
            start=None
    if start is not None:
        end=x+1 if 'x' in locals() else 0
        if end-start>=min_width: runs.append((start,end))
    return runs

def exposed_starts_from_scanlines(rows, *, y0=0, min_width=8, min_support=2, merge_tolerance=3):
    if min_support<1: raise ValueError("min_support must be positive")
    clusters=[]
    for offset,row in enumerate(rows):
        for start,end in white_runs(row,min_width=min_width):
            match=next((c for c in clusters if abs(c['x']-start)<=merge_tolerance),None)
            if match is None: clusters.append({'x':start,'first_y':y0+offset,'last_y':y0+offset,'support':1,'max_width':end-start})
            else:
                match['last_y']=y0+offset; match['support']+=1; match['max_width']=max(match['max_width'],end-start)
    return [PanelStart(c['x'],c['first_y'],c['max_width'],c['last_y']-c['first_y']+1) for c in clusters if c['support']>=min_support]

def persistent_dark_edges(columns: Sequence[Sequence[Sequence[int]]], *, x0: int = 0,
                          dark_ceiling: int = 60, min_fraction: float = .90,
                          merge_width: int = 2) -> list[int]:
    """Return x positions of vertical dark borders persistent through a crop.

    Input is column-major pixels. Adjacent 1-2 px border strokes are collapsed
    to one x. A high persistence threshold avoids promoting rank/suit glyphs.
    """
    if not 0 < min_fraction <= 1: raise ValueError("min_fraction outside (0,1]")
    hits=[]
    for i,col in enumerate(columns):
        if not col: continue
        fraction=sum(1 for rgb in col if max(int(v) for v in rgb[:3]) < dark_ceiling)/len(col)
        if fraction>=min_fraction: hits.append(x0+i)
    groups=[]
    for x in hits:
        if not groups or x-groups[-1][-1]>merge_width: groups.append([x])
        else: groups[-1].append(x)
    return [group[0] for group in groups]

__all__=["BRIDGIT_PIXEL_VERSION","PanelStart","white_runs","exposed_starts_from_scanlines","persistent_dark_edges"]
