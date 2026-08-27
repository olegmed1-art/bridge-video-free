"""Build labelled glyph template banks only from stable, human-labelled evidence."""
from __future__ import annotations
from typing import Mapping,Sequence
from bridge_vision.temporal_glyphs import stable_consensus
TEMPLATE_BANK_VERSION="bridgit-template-bank-v1"
def build_template_bank(labelled_observations:Mapping[str,Sequence[Sequence[Sequence[bool]]]],*,min_support:int=2,min_pair_iou:float=.90)->tuple[dict[str,list[list[bool]]],dict[str,object]]:
    if not isinstance(labelled_observations,Mapping):raise ValueError("labelled_observations must be a mapping")
    templates={};rejected={};support={}
    for raw_label,observations in labelled_observations.items():
        label=str(raw_label).strip().upper()
        if not label:raise ValueError("template label must be non-empty")
        result=stable_consensus(observations,min_support=min_support,min_pair_iou=min_pair_iou)
        if result["status"]!="STABLE" or result["template"] is None:rejected[label]=str(result["status"]);continue
        templates[label]=[list(row) for row in result["template"]];support[label]=int(result["support"])
    evidence={"version":TEMPLATE_BANK_VERSION,"accepted_labels":sorted(templates),"rejected_labels":rejected,"support":support,"min_support":min_support,"min_pair_iou":min_pair_iou}
    return templates,evidence
__all__=["TEMPLATE_BANK_VERSION","build_template_bank"]
