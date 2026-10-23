"""Preserve existing factor diagnostics and bind separately reviewed components.

No classifier, threshold or confidence calibration lives here. A whole-card
UNKNOWN can retain a known rank, suit or owner without promoting its other parts.
"""
from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
import json

from bridge_vision.original_deal_solver import RecoveryInputError

COMPONENTS = {"rank": list("AKQJT98765432"), "suit": list("SHDC"), "owner": list("NESW")}


def observation_sha256(item):
    return sha256(json.dumps(item, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def factor_component_view(item):
    receipt = item.get("receipt")
    detail = item.get("factor_diagnostic") or (receipt.get("factor_diagnostic") if isinstance(receipt, Mapping) else {}) or {}
    if not isinstance(detail, Mapping):
        raise RecoveryInputError("FACTOR_DIAGNOSTIC_INVALID")
    card = detail.get("component_candidate") or detail.get("best_card")
    valid = isinstance(card, str) and len(card) == 2 and card[0] in COMPONENTS["rank"] and card[1] in COMPONENTS["suit"]
    result = {part: {"domain": values[:], "confidence": None, "provenance": [],
                     "candidate_domain": [card[0 if part == "rank" else 1]] if valid and part != "owner" else values[:],
                     "diagnostic_margin": detail.get(part + "_margin")}
              for part, values in COMPONENTS.items()}
    result["raw_factor_diagnostic"] = deepcopy(detail)
    result["observation_sha256"] = observation_sha256(item)
    result["state"] = "BOTH_UNKNOWN"
    return result


def attach_reviewed_components(frame, proof, event, rank_result):
    slots = proof.get("component_slots", [])
    if not isinstance(slots, list) or len(slots) > 52:
        raise RecoveryInputError("COMPONENT_SLOT_LIMIT_OR_TYPE")
    unknown = frame.get("rejected_observations", [])
    physical = dict(rank_result.get("physical_slots", {}))
    used_observations = {}
    for slot in slots:
        if not isinstance(slot, Mapping):
            raise RecoveryInputError("COMPONENT_SLOT_INVALID")
        idx, sid, box = slot.get("unknown_index"), slot.get("slot_id"), slot.get("box")
        valid = (type(idx) is int and 0 <= idx < len(unknown)
            and isinstance(sid, str) and 0 < len(sid) <= 128
            and isinstance(box, dict) and all(type(box.get(k)) is int for k in ("x", "y", "w", "h"))
            and min(box["x"], box["y"]) >= 0 and min(box["w"], box["h"]) > 0
            and box["x"] + box["w"] <= frame["raw_frame_size"]["width"]
            and box["y"] + box["h"] <= frame["raw_frame_size"]["height"]
            and all(isinstance(slot.get(k), str) and 0 < len(slot[k].strip()) <= 512
                    for k in ("physical_slot_ref", "layout_ref", "scale_ref", "no_occlusion_ref")))
        if not valid or slot.get("observation_sha256") != observation_sha256(unknown[idx]):
            rank_result["rejected"].append({"reason": "COMPONENT_PHYSICAL_BINDING_UNPROVEN", "slot": deepcopy(slot)})
            continue
        view = factor_component_view(unknown[idx])
        if idx in used_observations and used_observations[idx] != sid:
            rank_result["conflicts"].append({"reason": "COMPONENT_OBSERVATION_REUSED", "unknown_index": idx})
            continue
        used_observations[idx] = sid
        reviews = slot.get("components", {})
        if not isinstance(reviews, Mapping) or set(reviews) - set(COMPONENTS):
            raise RecoveryInputError("COMPONENT_REVIEWS_INVALID")
        valid = True
        for part, review in reviews.items():
            domain = review.get("domain") if isinstance(review, Mapping) else None
            if (not isinstance(domain, list) or not domain or len(domain) > len(COMPONENTS[part])
                    or any(not isinstance(v, str) or v not in COMPONENTS[part] for v in domain)
                    or len(set(domain)) != len(domain) or not isinstance(review.get("review_ref"), str)
                    or not review["review_ref"].strip()):
                valid = False
                break
            view[part]["domain"] = sorted(domain)
            view[part]["provenance"] = [review["review_ref"]]
        if not valid:
            rank_result["rejected"].append({"reason": "COMPONENT_DOMAIN_UNPROVEN", "slot": deepcopy(slot)})
            continue
        bkey = tuple(box[k] for k in ("x", "y", "w", "h"))
        if (sid in physical and physical[sid] != bkey) or any(v == bkey and k != sid for k, v in physical.items()):
            rank_result["conflicts"].append({"reason": "COMPONENT_PHYSICAL_SLOT_ALIAS", "slot_id": sid})
            continue
        physical[sid] = bkey
        if len(physical) > 52:
            raise RecoveryInputError("PHYSICAL_SLOT_LIMIT")
        choices = [[r + s] for r in view["rank"]["domain"] for s in view["suit"]["domain"]]
        existing = [i for i in rank_result["intervals"] if sid in i["slots"]]
        if existing:
            interval = existing[0]
            pos = interval["slots"].index(sid)
            allowed = {c[0] for c in choices}
            interval["possibilities"] = [c for c in interval["possibilities"] if c[pos] in allowed]
            interval["owner_domain"] = [s for s in interval.get("owner_domain", [interval["seat"]]) if s in view["owner"]["domain"]]
            interval.setdefault("component_views", {})[sid] = view
        elif sid in rank_result.get("physical_slots", {}):
            rank_result["conflicts"].append({"reason": "COMPONENT_SLOT_REUSES_ANCHOR", "slot_id": sid})
        else:
            interval = {"row_id": "component:" + sid, "event_id": event, "frame_sha256": frame["frame_sha256"],
                "timestamp_ms": frame["timestamp_ms"], "slots": [sid], "anchors": [], "seat": None,
                "owner_domain": view["owner"]["domain"], "possibilities": choices,
                "status": "MULTIPLE", "method": "INFERRED_COMPONENT_CONSTRAINT", "reviewed_row": deepcopy(slot),
                "component_evidence": view}
            rank_result["intervals"].append(interval)
    rank_result["physical_slots"] = physical
