"""Stable contracts for DDS3 position/all-moves educational analysis.

The actual values MUST be supplied by the DDS3 SolverContext-backed executable.
This module only normalizes engine output into regret/swing data; it never invents
bridge values.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class MoveValue:
    card: str
    tricks: int


def rank_move_values(values:list[MoveValue])->dict[str,Any]:
    if not values: raise ValueError("DDS3 returned no legal moves")
    best=max(v.tricks for v in values)
    rows=[]
    for v in values:
        regret=best-v.tricks
        rows.append({"card":v.card,"tricks":v.tricks,"regret":regret,"regret_class":"0" if regret==0 else "1" if regret==1 else "2+","optimal":regret==0})
    return {"best_tricks":best,"optimal_cards":[r["card"] for r in rows if r["optimal"]],"moves":rows,"engine":"DDS3","fallback_used":False}


def trajectory(values:list[int])->dict[str,Any]:
    if not values: raise ValueError("trajectory requires at least V0")
    swings=[]
    for i in range(1,len(values)):
        delta=values[i]-values[i-1]
        if delta: swings.append({"after_play":i,"from":values[i-1],"to":values[i],"delta":delta})
    first=swings[0] if swings else None
    return {"values":values,"first_swing":first,"final_delta":values[-1]-values[0],"swings":swings,"engine":"DDS3","fallback_used":False}
