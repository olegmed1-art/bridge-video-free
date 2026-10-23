"""Reviewed physical rank rows for the existing temporal recovery path.

Restores the documented order rule, not the unavailable historical weighted
implementation. No pixel spacing, confidence, colour or absent slot is evidence.
Ambiguous intervals retain all rank combinations (at most C(13,6)=1716).
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from itertools import combinations
import hashlib
import json

from bridge_vision.original_deal_solver import RecoveryInputError, recover_original_deal, feasible_owner_domains
from bridge_contracts.video_deal import FULL_DECK, SEATS

RANKS = "AKQJT98765432"


def reviewed_rank_rows(frame, proof, facts, event_key):
    rows = proof.get("rank_rows", [])
    if not isinstance(rows, list) or len(rows) > 16:
        raise RecoveryInputError("RANK_ROW_LIMIT_OR_TYPE")
    if sum(len(r.get("slots", [])) for r in rows if isinstance(r, Mapping) and isinstance(r.get("slots"), list)) > 52:
        raise RecoveryInputError("PHYSICAL_SLOT_LIMIT")
    result = {"facts": [], "intervals": [], "rejected": [], "conflicts": [], "physical_slots": {}}
    known = {f["evidence_id"]: f for f in facts if f.get("kind") == "IN_HAND"}
    used_slots = set()
    used_anchors = set()
    used_boxes = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise RecoveryInputError("RANK_ROW_INVALID")
        row_id, seat, suit = row.get("row_id"), row.get("screen_seat"), row.get("suit")
        def reject(reason):
            result["rejected"].append({"reason": reason, "row_id": row_id, "event_id": event_key})
        if (not isinstance(row_id, str) or not row_id or len(row_id) > 128
                or not isinstance(seat, str) or seat not in proof["screen_to_seat"] or suit not in ("S", "H", "D", "C")
                or row.get("direction") not in ("DESCENDING", "ASCENDING")
                or row.get("visible_complete_interval") is not True
                or any(not isinstance(row.get(k), str) or not row[k].strip() or len(row[k]) > 512
                       for k in ("layout_ref", "scale_ref", "sort_direction_ref", "same_suit_ref", "no_occlusion_ref"))):
            reject("RANK_LAYOUT_ORDER_SUIT_OR_VISIBILITY_UNPROVEN")
            continue
        slots = row.get("slots")
        if not isinstance(slots, list) or not 1 <= len(slots) <= 13:
            reject("PHYSICAL_SLOT_LIST_UNPROVEN")
            continue
        ranks = RANKS if row["direction"] == "DESCENDING" else RANKS[::-1]
        anchors = []
        valid = True
        local_ids, boxes = set(), set()
        for index, slot in enumerate(slots):
            if not isinstance(slot, Mapping):
                valid = False
                break
            sid, box = slot.get("slot_id"), slot.get("box")
            if (not isinstance(sid, str) or not sid or len(sid) > 128
                    or sid in local_ids or sid in used_slots
                    or not isinstance(slot.get("physical_slot_ref"), str) or not slot["physical_slot_ref"].strip()
                    or slot.get("suit") != suit or not isinstance(box, dict)
                    or any(type(box.get(k)) is not int for k in ("x", "y", "w", "h"))
                    or min(box["x"], box["y"]) < 0 or min(box["w"], box["h"]) <= 0
                    or box["x"] + box["w"] > frame["raw_frame_size"]["width"]
                    or box["y"] + box["h"] > frame["raw_frame_size"]["height"]):
                valid = False
                break
            bkey = tuple(box[k] for k in ("x", "y", "w", "h"))
            if bkey in boxes or bkey in used_boxes:
                result["conflicts"].append({"reason": "PHYSICAL_SLOT_BOX_REUSED", "event_id": event_key})
                valid = False
                break
            boxes.add(bkey)
            local_ids.add(sid)
            if slot.get("status") == "UNKNOWN":
                continue
            ordinal = slot.get("observation_index")
            fact = known.get(event_key + ":" + str(ordinal)) if type(ordinal) is int else None
            if (slot.get("status") != "ANCHOR" or fact is None
                    or fact["seat"] != proof["screen_to_seat"][seat]
                    or fact["card"][1] != suit or slot.get("card") != fact["card"]):
                valid = False
                break
            anchors.append((index, ranks.index(fact["card"][0]), fact["evidence_id"]))
        if len({a[2] for a in anchors}) != len(anchors):
            valid = False
        # Adjacent intervals in one row share an anchor legitimately; a physical
        # observation cannot describe two separate slots/rows in this frame.
        if any(a[2] in used_anchors for a in anchors):
            valid = False
        if not valid:
            reject("PHYSICAL_SLOT_OR_ANCHOR_UNPROVEN")
            continue
        used_slots.update(local_ids)
        used_boxes.update(boxes)
        result["physical_slots"].update({s["slot_id"]: tuple(s["box"][k] for k in ("x", "y", "w", "h")) for s in slots})
        used_anchors.update(a[2] for a in anchors)
        if not anchors:
            reject("RANK_ANCHOR_MISSING")
            continue
        # Virtual limits are deck-rank bounds, never invented physical slots.
        bounds = [(-1, -1, None), *anchors, (len(slots), len(ranks), None)]
        for left, right in zip(bounds, bounds[1:]):
            a, lo, left_ref = left
            b, hi, right_ref = right
            count = b - a - 1
            if hi <= lo or count > hi - lo - 1:
                result["conflicts"].append({"reason": "RANK_ORDER_IMPOSSIBLE", "row_id": row_id,
                                            "event_id": event_key})
                continue
            if count == 0:
                continue
            possibilities = [[ranks[i] + suit for i in choice]
                             for choice in combinations(range(lo + 1, hi), count)]
            interval = {"row_id": row_id, "event_id": event_key, "frame_sha256": frame["frame_sha256"],
                        "timestamp_ms": frame["timestamp_ms"],
                        "slots": [s["slot_id"] for s in slots[a + 1:b]],
                        "anchors": [r for r in (left_ref, right_ref) if r is not None],
                        "method": "INFERRED_ORDER_GAP" if left_ref and right_ref else "INFERRED_ORDER_BOUNDARY",
                        "direction": row["direction"], "seat": proof["screen_to_seat"][seat], "suit": suit,
                        "possibilities": possibilities,
                        "status": "UNIQUE" if len(possibilities) == 1 else "MULTIPLE",
                        "reviewed_row": deepcopy(row)}
            result["intervals"].append(interval)
            if len(possibilities) == 1:
                for sid, card in zip(interval["slots"], possibilities[0]):
                    if any(f["card"] == card for f in [*known.values(), *result["facts"]]
                           if f["evidence_id"].startswith(event_key + ":")):
                        result["conflicts"].append({"reason": "RANK_SLOT_DUPLICATES_VISIBLE_CARD",
                                                    "card": card, "event_id": event_key})
                    result["facts"].append({"card": card, "seat": interval["seat"],
                        "kind": "ORDER_INFERENCE", "source": interval["method"],
                        "timestamp_ms": frame["timestamp_ms"],
                        "evidence_id": event_key + ":rank:" + sid,
                        "derivation": deepcopy(interval)})
    return result


def recover_with_rank_intervals(facts, intervals, *, node_limit=10000):
    """Exact global rank/ownership probes, equivalent to a logical fixed point.

    Search witnesses never leave this function. A failed exhaustive feasibility
    probe removes an alternative. A budget-exhausted probe removes nothing.
    Joint physical-slot and 13-card constraints are tested, not just marginals.
    """
    if type(node_limit) is not int or not 1 <= node_limit <= 10000:
        raise RecoveryInputError("RANK_SEARCH_LIMIT_INVALID")
    facts, intervals = deepcopy(facts), deepcopy(intervals)
    base = recover_original_deal(facts)
    if not intervals or base["status"] == "CONFLICT":
        return base, facts, intervals, [{"round": 1, "new_slot_facts": 0, "solution_status": base["solution_status"]}], True
    if len(intervals) > 64:
        base.update(status="UNRESOLVED_LIMIT", solution_status="UNKNOWN")
        return base, facts, intervals, [{"reason": "RANK_INTERVAL_LIMIT"}], False
    intervals.sort(key=lambda r: (len(r["possibilities"]), r["event_id"], r["row_id"], r["slots"]))
    snapshot = deepcopy(intervals)
    snapshot_sha = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    placed = {}
    played = {}
    for fact in facts:
        if fact.get("kind") == "PLAYED":
            played[fact["card"]] = fact["played_at_ms"]
        if fact.get("kind") in ("IN_HAND", "ORDER_INFERENCE"):
            event = fact["evidence_id"].split(":", 1)[0]
            placed.setdefault((event, fact["card"]), set()).add(fact["evidence_id"])
    nodes = 0
    incomplete = False
    capacities = dict.fromkeys(SEATS, 13)
    deck = sorted(FULL_DECK)

    def exists(owner_force=None, slot_force=None, choice_force=None):
        nonlocal nodes, incomplete
        domains = deepcopy(base["domains"])
        if owner_force:
            card, seat = owner_force
            if seat not in domains[card]:
                return False
            domains[card] = [seat]
        def visit(index, current, occupied):
            nonlocal nodes, incomplete
            if nodes >= node_limit:
                incomplete = True
                return None
            nodes += 1
            if feasible_owner_domains(deck, capacities, current, feasibility_only=True)["status"] == "CONFLICT":
                return False
            if index == len(intervals):
                return True
            interval = intervals[index]
            event = interval["event_id"]
            owners = interval.get("owner_domain", [interval["seat"]])
            for choice in interval["possibilities"]:
                if choice_force and choice_force[0] == index and choice != choice_force[1]:
                    continue
                if slot_force and slot_force[0] == index and choice[slot_force[1]] != slot_force[2]:
                    continue
                next_domains = dict(current)
                next_occupied = dict(occupied)
                valid = True
                for card, sid in zip(choice, interval["slots"]):
                    ref = event + ":rank:" + sid
                    key = event, card
                    allowed = [s for s in next_domains[card] if s in owners]
                    if (not allowed or occupied.get(key, set()) - {ref}
                            or (card in played and interval["timestamp_ms"] > played[card])):
                        valid = False
                        break
                    next_domains[card] = allowed
                    next_occupied[key] = {ref}
                if valid:
                    answer = visit(index + 1, next_domains, next_occupied)
                    if answer is not False:
                        return answer
            return False
        return visit(0, domains, placed)

    satisfiable = exists()
    if satisfiable is False:
        base.update(status="CONFLICT", solution_status="CONFLICT", inferred={}, domains={}, deal=None)
        base["conflicts"].append({"reason": "NO_JOINT_RANK_OWNERSHIP_SOLUTION"})
        return base, facts, intervals, [{"search_nodes": nodes}], True
    if satisfiable is None:
        base.update(status="UNRESOLVED_LIMIT", solution_status="UNKNOWN")
        return base, facts, intervals, [{"search_nodes": nodes}], False
    domains = {card: [s for s in owners if len(owners) == 1 or exists((card, s)) is not False]
               for card, owners in base["domains"].items()}
    refs = {f["evidence_id"] for f in facts}
    additions = []
    for index, interval in enumerate(intervals):
        interval["slot_domains"] = {}
        for pos, sid in enumerate(interval["slots"]):
            domain = sorted({choice[pos] for choice in interval["possibilities"]})
            possible = [c for c in domain if exists(slot_force=(index, pos, c)) is not False]
            interval["slot_domains"][sid] = possible
            ref = interval["event_id"] + ":rank:" + sid
            owners = sorted({s for c in possible for s in domains[c]
                             if s in interval.get("owner_domain", [interval["seat"]])
                             and exists(owner_force=(c, s), slot_force=(index, pos, c)) is not False})
            if len(possible) == 1 and len(owners) == 1 and ref not in refs:
                additions.append({"card": possible[0], "seat": owners[0], "kind": "ORDER_INFERENCE",
                    "timestamp_ms": interval["timestamp_ms"], "source": interval["method"], "evidence_id": ref,
                    "derivation": {**deepcopy(interval), "global_constraint_probe": True,
                                   "premise_evidence_ids": sorted(refs), "ownership_constraints": base["constraints"],
                                   "rank_constraints_ref": "recovery.rank_constraints_snapshot",
                                   "rank_constraints_sha256": snapshot_sha}})
            view = interval.get("component_evidence") or interval.get("component_views", {}).get(sid)
            if view:
                view["rank"]["domain"] = sorted({c[0] for c in possible})
                view["suit"]["domain"] = sorted({c[1] for c in possible})
                view["owner"]["domain"] = owners
                known_rank, known_suit = len(view["rank"]["domain"]) == 1, len(view["suit"]["domain"]) == 1
                view["state"] = ("BOTH_KNOWN_OWNER_UNKNOWN" if len(owners) != 1 else "CARD_AND_OWNER_KNOWN") if known_rank and known_suit else "RANK_KNOWN_SUIT_UNKNOWN" if known_rank else "RANK_UNKNOWN_SUIT_KNOWN" if known_suit else "BOTH_UNKNOWN"
        interval["possibilities"] = [choice for choice in interval["possibilities"]
            if all(c in interval["slot_domains"][sid] for c, sid in zip(choice, interval["slots"]))
            and exists(choice_force=(index, choice)) is not False]
        interval["possibilities_are_outer_approximation"] = incomplete
        interval["status"] = "UNIQUE" if len(interval["possibilities"]) == 1 else "MULTIPLE"
    known_cards = {f["card"] for f in facts + additions}
    for card, owners in domains.items():
        if len(owners) == 1 and card not in known_cards and card not in base["inferred"]:
            additions.append({"card": card, "seat": owners[0], "kind": "CONSTRAINT_INFERENCE",
                "source": "INFERRED_COMPONENT_CONSTRAINT" if any(i.get("component_evidence") for i in intervals) else "INFERRED_ORDER_CONSTRAINT", "evidence_id": "rank-owner:" + card,
                "derivation": {"global_constraint_probe": True, "premise_evidence_ids": sorted(refs),
                               "rank_constraints_ref": "recovery.rank_constraints_snapshot",
                               "rank_constraints_sha256": snapshot_sha, "ownership_constraints": base["constraints"]}})
    facts.extend(additions)
    recovery = recover_original_deal(facts)
    recovery["rank_constraints_snapshot"] = snapshot
    recovery["rank_constraints_sha256"] = snapshot_sha
    # Global domains may be stricter than unary capacity propagation; an unknown
    # probe retains its old domain, so partial results remain a safe superset.
    recovery["domains"] = {c: [s for s in recovery["domains"][c] if s in domains[c]] for c in domains}
    recovery["joint_rank_search_complete"] = not incomplete
    trace = [{"round": 1, "solution_status": base["solution_status"], "new_slot_facts": len(additions),
              "method": "GLOBAL_JOINT_CONSTRAINT_PROBES", "search_nodes": nodes},
             {"round": 2, "solution_status": recovery["solution_status"], "new_slot_facts": 0,
              "method": "GLOBAL_FIXED_POINT" if not incomplete else "CONSERVATIVE_LIMIT"}]
    return recovery, facts, intervals, trace, not incomplete
