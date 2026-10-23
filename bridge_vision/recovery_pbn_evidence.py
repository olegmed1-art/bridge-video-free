"""Structural bindings for pinned temporal evidence; no ownership solver.

Frozen validation is not runtime enrollment. This ABI accepts the actual pinned
producer's existing evidence contract without asserting human identity or asking
the owner to label each new video. A new runtime authority needs its own reviewed
producer contract; changing a JSON authority string cannot enroll it.
"""
from collections.abc import Mapping
import hashlib
import json

from bridge_contracts.video_deal import FULL_DECK, SEATS

EXTENDED_PRODUCER_PINS = {
    "bridge_vision/temporal_deal_recovery.py": "395171951e18dd0af3bd9130b196e0610fa75eaac22806b78c16905071aeb66e",
    "bridge_vision/original_deal_solver.py": "47ba1f93171d365fcb45e1b2ff4ea7b7137e85f713fb50e56536d76fe19c650d",
    "bridge_vision/bridgit_rank_order_evidence.py": "611e7e61ce1fee217ab3f68db74b8aeaeeab3cf102fa42cb2ed08e0d719a460a",
    "bridge_vision/bridgit_component_evidence.py": "16c7de1ecd0fe549d11771aa4b0436b704e992dce99bf7577e503b9a4f9b3cb1",
    "bridge_vision/teacher_card_reconstruction_r265.py": "1464ccb9603570d8579b716e228f1669604ef299c0c58bf91c5c9c3d7b2a8616",
    "bridge_vision/temporal_recovery_export.py": "6b401b5e36c5a4e4c116cade16a8a0bab8bbaa47085e9fbe05f56153e03bfce2",
}
DERIVED_SOURCES = {"INFERRED_ORDER_GAP", "INFERRED_ORDER_BOUNDARY",
                   "INFERRED_ORDER_CONSTRAINT", "INFERRED_COMPONENT_CONSTRAINT"}
# The handoff pins the Windows CRLF checkout for this dependency; the upstream
# patch pins its exact LF Git blob. The raw checkout's LF conversion was hashed
# and matched to that pinned blob. Admission compares actual raw pins; it never normalizes an
# unknown artifact or treats a similar source revision as equivalent.
TEACHER_LF_SHA256 = "257442529109eff1161f0bce072aef237203b9e1856bc384a4311c073f328086"
EXTENDED_PRODUCER_R2_PINS = {
    **EXTENDED_PRODUCER_PINS,
    "bridge_vision/temporal_deal_recovery.py": "bc3a0c5ae3aa73031eccf8dc2239032aab6641aab29280d3ffcd7c755e2d6c47",
    "bridge_vision/runtime_recovery_evidence.py": "ce85e2daac81e51eac07e2074aed9c08fc5f61f715f2f868997d0277a87d9030",
}
# The R3 generic gate no longer imports a video-specific adapter. Both byte
# forms below were checked against the supplied raw checkout and exact LF blob;
# an unknown revision is never normalized or accepted as a compatible gate.
RUNTIME_R3_LF_SHA256 = "868de58bf695f66b9a422518106ded877dbb55ff74b3594ebc58d6dd3046853b"
EXTENDED_PRODUCER_R3_PINS = {
    **EXTENDED_PRODUCER_R2_PINS,
    "bridge_vision/runtime_recovery_evidence.py": "f6a319b499d2cb3b9667e0c0e73beaf29ba0447ae00bce714f37353b05850618",
}
BINDINGS = ("source_video_sha256", "frame_sha256", "episode_id", "board_epoch",
            "board_number", "timestamp_ms", "observation_index", "raw_frame_size",
            "recognizer_version", "reference_profile_id", "data_class", "pixel_observer_version")
IDENTITY_FIELDS = ("source_video_sha256", "episode_id", "board_epoch", "board_number",
                   "raw_frame_size", "recognizer_version", "reference_profile_id", "data_class", "pixel_observer_version")


def need(value, code):
    if not value:
        raise ValueError("PBN_RECOVERY_" + code)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("ascii")).hexdigest()


def ref(value):
    return type(value) is str and 0 < len(value.strip()) <= 512


def extended_profile(pins):
    teacher = "bridge_vision/teacher_card_reconstruction_r265.py"
    runtime = "bridge_vision/runtime_recovery_evidence.py"
    return any(all(pins.get(path) in (sha, TEACHER_LF_SHA256) if path == teacher else
                   pins.get(path) in (sha, RUNTIME_R3_LF_SHA256) if path == runtime and profile is EXTENDED_PRODUCER_R3_PINS else
                   pins.get(path) == sha for path, sha in profile.items())
               for profile in (EXTENDED_PRODUCER_PINS, EXTENDED_PRODUCER_R2_PINS, EXTENDED_PRODUCER_R3_PINS))


def box_ok(box, size):
    return (isinstance(box, Mapping) and isinstance(size, Mapping)
            and all(type(box.get(k)) is int for k in ("x", "y", "w", "h"))
            and min(box["x"], box["y"]) >= 0 and min(box["w"], box["h"]) > 0
            and all(type(size.get(k)) is int and 1 <= size[k] <= 32768 for k in ("width", "height"))
            and box["x"] + box["w"] <= size["width"] and box["y"] + box["h"] <= size["height"])


def scope_frames(temporal, scope, receipts):
    history = temporal.get("history")
    need(type(history) is list and 1 <= len(history) <= 2048, "HISTORY")
    frames = {}
    previous = None
    for item in history:
        need(isinstance(item, Mapping) and isinstance(item.get("frame"), Mapping), "HISTORY_FRAME")
        frame = item["frame"]
        event = digest([frame.get(k) for k in ("source_video_sha256", "episode_id", "observation_index")])
        need(item.get("event_id") == event and event in receipts and event not in frames
             and item.get("payload_sha256") == digest(frame) and item.get("review_status") == "BOUND_TO_REVIEW", "HISTORY_BINDING")
        proof = receipts[event]
        need(all(frame.get(k) == proof.get(k) for k in BINDINGS), "FRAME_RECEIPT_BINDING")
        identity = [frame.get(k) for k in IDENTITY_FIELDS] + [proof.get(k) for k in ("scene_id", "orientation_id", "screen_to_seat")]
        need(scope["identity"] == identity and proof.get("settled") is True
             and all(ref(proof.get(k)) for k in ("scene_id", "orientation_id", "source_frame_pts_ref",
                 "deal_identity_ref", "seat_assignment_ref", "settled_frame_ref")), "COMPLETE_SCOPE_IDENTITY")
        mapping = proof.get("screen_to_seat")
        need(isinstance(mapping, Mapping) and set(mapping) == set(SEATS)
             and any([mapping[s] for s in SEATS] == list(SEATS[i:] + SEATS[:i]) for i in range(4)), "ORIENTATION")
        need(type(frame.get("observation_index")) is int and frame["observation_index"] >= 0
             and type(frame.get("timestamp_ms")) is int and frame["timestamp_ms"] >= 0
             and type(frame.get("observations")) is list and len(frame["observations"]) <= 52, "FRAME_SHAPE")
        if previous is not None:
            old_event, old_frame = previous
            need(proof.get("continuous_from_event_id") == old_event and ref(proof.get("continuity_ref"))
                 and frame["observation_index"] == old_frame["observation_index"] + 1
                 and frame["timestamp_ms"] > old_frame["timestamp_ms"], "SCOPE_CONTINUITY")
        frames[event] = frame
        previous = event, frame
    need(list(frames) == scope.get("event_ids") and set(frames) == set(receipts), "SCOPE_EVENT_COVERAGE")
    return frames


def played_owner(fact, proof, card, frame):
    need(type(card.get("played_at_ms")) is int and 0 <= card["played_at_ms"] <= frame["timestamp_ms"]
         and ref(card.get("seat_time_ref")), "PLAYED_TIME")
    if card.get("zone_type") is None:
        owner = proof["screen_to_seat"].get(card.get("screen_seat"))
    else:
        zones = proof.get("played_zones")
        need(type(zones) is list and len(zones) <= 8, "PLAYED_ZONES")
        matches = [z for z in zones if isinstance(z, Mapping) and z.get("zone_id") == card.get("zone_id")]
        need(len(matches) == 1, "PLAYED_ZONE_REFERENCE")
        zone = matches[0]; mapping = zone.get("slot_to_seat"); boxes = zone.get("slot_boxes")
        need(zone.get("zone_type") == card.get("zone_type") and zone.get("zone_type") in
             ("current_trick", "previous_trick", "played_in_front_of_hand") and
             all(ref(zone.get(k)) for k in ("zone_id", "layout_ref", "calibration_ref", "orientation_ref", "orientation_id"))
             and isinstance(mapping, Mapping) and len(mapping) == 4 and all(ref(k) for k in mapping)
             and all(type(v) is str for v in mapping.values()) and set(mapping.values()) == set(SEATS)
             and isinstance(boxes, Mapping) and set(boxes) == set(mapping)
             and type(card.get("zone_slot")) is str and card["zone_slot"] in mapping, "PLAYED_ZONE_MAPPING")
        need(all(box_ok(box, frame["raw_frame_size"]) for box in boxes.values())
             and len({tuple(box[k] for k in ("x", "y", "w", "h")) for box in boxes.values()}) == 4
             and card.get("zone_slot_box") == boxes[card["zone_slot"]], "PLAYED_ZONE_BOX")
        idx = card.get("observation_index")
        need(type(idx) is int and 0 <= idx < len(frame["observations"]), "PLAYED_OBSERVATION")
        observed_box = frame["observations"][idx].get("box")
        need(observed_box is None or observed_box == boxes[card["zone_slot"]], "PLAYED_OBSERVATION_BOX")
        owner = mapping[card["zone_slot"]]
        need(ref(card.get("trick_id")), "PLAYED_TRICK")
    if card.get("zone_type") is not None or "deal_start_ms" in proof or "deal_end_ms" in proof:
        need(type(proof.get("deal_start_ms")) is int and type(proof.get("deal_end_ms")) is int
             and 0 <= proof["deal_start_ms"] <= card["played_at_ms"] <= frame["timestamp_ms"] < proof["deal_end_ms"], "PLAYED_BOARD_INTERVAL")
    need(card.get("played_by") == owner and fact.get("initial_owner") == owner and fact.get("membership") == "PLAYED"
         and all(fact.get(k) == card.get(k) for k in ("zone_type", "zone_id", "trick_id")), "PLAYED_INITIAL_OWNER")
    return owner


def played_events(scope, facts):
    expected = {}; by_card = {}; by_trick_seat = {}
    for fact in facts.values():
        if fact.get("kind") != "PLAYED":
            continue
        card, owner, trick, at = (fact.get(k) for k in ("card", "seat", "trick_id", "played_at_ms"))
        need(card not in by_card or by_card[card] == (owner, trick, at), "PLAYED_EVENT_CONFLICT")
        by_card[card] = owner, trick, at
        if trick is not None:
            need(ref(trick) and ((trick, owner) not in by_trick_seat or by_trick_seat[trick, owner] == card), "TRICK_SEAT_CONFLICT")
            by_trick_seat[trick, owner] = card
        key = digest([scope["identity"][:4], trick, card, owner])
        need(fact.get("play_event_id") == key, "PLAYED_EVENT_BINDING")
        entry = expected.setdefault(key, dict(card=card, initial_owner=owner, trick_id=trick,
                                              played_at_ms=at, display_evidence_ids=[]))
        entry["display_evidence_ids"].append(fact["evidence_id"])
    need(scope.get("played_events") == expected, "PLAYED_EVENT_COVERAGE")
    for fact in facts.values():
        if fact.get("kind") == "IN_HAND" and fact["card"] in by_card:
            need(fact["timestamp_ms"] <= by_card[fact["card"]][2], "IN_HAND_AFTER_PLAY")


def component_binding(slot, frame):
    need(isinstance(slot, Mapping), "COMPONENT_SLOT")
    idx = slot.get("unknown_index"); unknown = frame.get("rejected_observations", [])
    need(type(idx) is int and type(unknown) is list and 0 <= idx < len(unknown)
         and slot.get("observation_sha256") == digest(unknown[idx]) and ref(slot.get("slot_id"))
         and box_ok(slot.get("box"), frame["raw_frame_size"])
         and all(ref(slot.get(k)) for k in ("physical_slot_ref", "layout_ref", "scale_ref", "no_occlusion_ref")), "COMPONENT_PHYSICAL_BINDING")
    parts = slot.get("components", {})
    allowed = {"rank": set("AKQJT98765432"), "suit": set("SHDC"), "owner": set(SEATS)}
    need(isinstance(parts, Mapping) and set(parts) <= set(allowed), "COMPONENT_PARTS")
    for name, item in parts.items():
        need(isinstance(item, Mapping) and type(item.get("domain")) is list and bool(item["domain"])
             and all(type(v) is str and v in allowed[name] for v in item["domain"])
             and len(set(item["domain"])) == len(item["domain"]) and ref(item.get("review_ref")), "COMPONENT_DOMAIN")


def component_view(view, slot, choices, pos, owners):
    allowed = {"rank": set("AKQJT98765432"), "suit": set("SHDC"), "owner": set(SEATS)}
    need(isinstance(view, Mapping) and view.get("observation_sha256") == slot["observation_sha256"], "COMPONENT_VIEW_HASH")
    for name, full in allowed.items():
        review = slot.get("components", {}).get(name)
        domain = set(review["domain"]) if review else full
        part = view.get(name, {})
        values = part.get("domain") if isinstance(part, Mapping) else None
        need(type(values) is list and bool(values) and all(type(v) is str for v in values)
             and set(values) <= domain and len(set(values)) == len(values)
             and part.get("provenance") == ([review["review_ref"]] if review else [])
             and part.get("confidence") is None, "COMPONENT_VIEW_PROVENANCE")
        actual = owners if name == "owner" else {c[pos][0 if name == "rank" else 1] for c in choices}
        need(set(actual) <= domain, "COMPONENT_CHOICE_DOMAIN")


def interval_binding(interval, receipts, frames, facts):
    need(isinstance(interval, Mapping) and interval.get("event_id") in receipts, "INTERVAL_EVENT")
    event = interval["event_id"]; proof = receipts[event]; frame = frames[event]
    slots = interval.get("slots"); choices = interval.get("possibilities")
    need(type(slots) is list and 1 <= len(slots) <= 13 and all(ref(s) for s in slots) and len(set(slots)) == len(slots)
         and type(choices) is list and bool(choices) and len(choices) <= 1716
         and all(type(c) is list and len(c) == len(slots) and all(type(v) is str and v in FULL_DECK for v in c) for c in choices)
         and interval.get("frame_sha256") == frame["frame_sha256"] and interval.get("timestamp_ms") == frame["timestamp_ms"], "INTERVAL_BINDING")
    row = interval.get("reviewed_row")
    if interval.get("method") == "INFERRED_COMPONENT_CONSTRAINT":
        need(isinstance(row, Mapping) and row in proof.get("component_slots", []) and slots == [row.get("slot_id")]
             and interval.get("row_id") == "component:" + slots[0] and interval.get("anchors") == [], "COMPONENT_INTERVAL")
        component_binding(row, frame)
        component_view(interval.get("component_evidence"), row, choices, 0, interval.get("owner_domain", []))
    else:
        need(interval.get("method") in ("INFERRED_ORDER_GAP", "INFERRED_ORDER_BOUNDARY")
             and isinstance(row, Mapping) and row in proof.get("rank_rows", [])
             and interval.get("row_id") == row.get("row_id") and interval.get("direction") == row.get("direction")
             and row.get("direction") in ("ASCENDING", "DESCENDING") and row.get("visible_complete_interval") is True
             and all(ref(row.get(k)) for k in ("layout_ref", "scale_ref", "sort_direction_ref", "same_suit_ref", "no_occlusion_ref"))
             and interval.get("suit") == row.get("suit") and row.get("suit") in ("S", "H", "D", "C")
             and interval.get("seat") == proof["screen_to_seat"].get(row.get("screen_seat")), "RANK_ROW_BINDING")
        physical = row.get("slots")
        need(type(physical) is list and all(isinstance(s, Mapping) for s in physical), "RANK_SLOTS")
        ids = [s.get("slot_id") for s in physical]
        need(len(set(ids)) == len(ids) and set(slots) <= set(ids), "RANK_SLOT_REFERENCE")
        indices = [ids.index(s) for s in slots]
        need(indices == list(range(indices[0], indices[-1] + 1)), "RANK_SLOT_INTERVAL")
        for pos in indices:
            slot = physical[pos]
            need(slot.get("status") == "UNKNOWN" and slot.get("suit") == row["suit"]
                 and ref(slot.get("physical_slot_ref")) and box_ok(slot.get("box"), frame["raw_frame_size"]), "RANK_PHYSICAL_SLOT")
        anchors = []
        for pos in (indices[0] - 1, indices[-1] + 1):
            if 0 <= pos < len(physical):
                slot = physical[pos]; evidence = event + ":" + str(slot.get("observation_index"))
                anchor = facts.get(evidence, {})
                need(slot.get("status") == "ANCHOR" and anchor.get("source", "VISUAL") == "VISUAL"
                     and anchor.get("kind") == "IN_HAND" and anchor.get("card") == slot.get("card")
                     and anchor.get("seat") == interval["seat"] and anchor["card"][1] == row["suit"], "RANK_ANCHOR")
                anchors.append(evidence)
        need(interval.get("anchors") == anchors and len(anchors) == (2 if interval["method"] == "INFERRED_ORDER_GAP" else 1), "RANK_ANCHOR_COVERAGE")
    for sid, view in interval.get("component_views", {}).items():
        matches = [s for s in proof.get("component_slots", []) if isinstance(s, Mapping) and s.get("slot_id") == sid]
        need(sid in slots and len(matches) == 1 and isinstance(view, Mapping), "COMPONENT_VIEW_REFERENCE")
        component_binding(matches[0], frame)
        component_view(view, matches[0], choices, slots.index(sid), interval.get("owner_domain", [interval.get("seat")]))


def derived_bindings(recovery, scope, receipts, frames, facts):
    derived = {r: f for r, f in facts.items() if f.get("source") in DERIVED_SOURCES}
    snapshot = recovery.get("rank_constraints_snapshot")
    if snapshot is None:
        if recovery.get("status") != "UNRESOLVED_LIMIT":
            need(not derived, "RANK_SNAPSHOT_REQUIRED")
            return
        # The producer can hit its limit before constructing a global snapshot,
        # while retaining independently grounded local gap facts. Admit these
        # only as lossless unresolved evidence; the renderer grants zero native
        # ownership. Do not invent a completed snapshot or rerun the search.
        intervals = scope.get("rank_intervals")
        need(type(intervals) is list and len(intervals) <= 52 * 2048, "UNRESOLVED_INTERVALS")
        for interval in intervals:
            interval_binding(interval, receipts, frames, facts)
        for evidence, fact in derived.items():
            d = fact.get("derivation")
            need(isinstance(d, Mapping) and fact.get("kind") == "ORDER_INFERENCE"
                 and fact.get("source") in ("INFERRED_ORDER_GAP", "INFERRED_ORDER_BOUNDARY")
                 and not d.get("global_constraint_probe"), "UNRESOLVED_LOCAL_DERIVATION")
            interval_binding(d, receipts, frames, facts)
            matches = [i for i in intervals if all(i.get(k) == d.get(k) for k in
                ("event_id", "row_id", "frame_sha256", "timestamp_ms", "slots", "anchors", "method", "reviewed_row"))]
            slots = [s for s in d["slots"] if evidence == d["event_id"] + ":rank:" + s]
            need(len(matches) == 1 and len(slots) == 1 and d.get("method") == fact["source"]
                 and fact.get("timestamp_ms") == d["timestamp_ms"] and fact["seat"] == d.get("seat")
                 and len(d["possibilities"]) == 1 and d["possibilities"][0][d["slots"].index(slots[0])] == fact["card"],
                 "UNRESOLVED_LOCAL_SUPPORT")
        return
    need(type(snapshot) is list and 1 <= len(snapshot) <= 64
         and recovery.get("rank_constraints_sha256") == digest(snapshot), "RANK_SNAPSHOT_HASH")
    for interval in snapshot:
        interval_binding(interval, receipts, frames, facts)
    physical = {}; boxes = {}
    for interval in snapshot:
        event = interval["event_id"]
        row = interval["reviewed_row"]
        slots = [row] if interval["method"] == "INFERRED_COMPONENT_CONSTRAINT" else row["slots"]
        for slot in slots:
            need(box_ok(slot.get("box"), frames[event]["raw_frame_size"]), "SNAPSHOT_SLOT_BOX")
            key = event, slot["slot_id"]
            box = tuple(slot["box"][k] for k in ("x", "y", "w", "h"))
            need((key not in physical or physical[key] == box)
                 and ((event, box) not in boxes or boxes[event, box] == slot["slot_id"]), "SNAPSHOT_PHYSICAL_ALIAS")
            physical[key] = box; boxes[event, box] = slot["slot_id"]
    dependencies = {}
    for evidence, fact in derived.items():
        source = fact["source"]; d = fact.get("derivation")
        need(isinstance(d, Mapping), "DERIVATION_REQUIRED")
        if fact.get("kind") == "ORDER_INFERENCE":
            interval_binding(d, receipts, frames, facts)
            need(d.get("method") == source and fact.get("timestamp_ms") == d["timestamp_ms"], "DERIVED_METHOD")
            matches = [i for i in snapshot if all(i.get(k) == d.get(k) for k in
                ("event_id", "row_id", "frame_sha256", "timestamp_ms", "slots", "anchors", "method", "reviewed_row"))]
            need(len(matches) == 1, "DERIVED_SNAPSHOT_INTERVAL")
            interval = matches[0]
            slots = [s for s in interval["slots"] if evidence == interval["event_id"] + ":rank:" + s]
            need(len(slots) == 1, "DERIVED_SLOT_REFERENCE")
            pos = interval["slots"].index(slots[0])
            need(any(c[pos] == fact["card"] for c in interval["possibilities"])
                 and fact["seat"] in interval.get("owner_domain", [interval.get("seat")]), "DERIVED_CARD_DOMAIN")
            if not d.get("global_constraint_probe"):
                need(len(d["possibilities"]) == 1 and d["possibilities"][0][pos] == fact["card"], "LOCAL_DERIVATION")
        else:
            need(fact.get("kind") == "CONSTRAINT_INFERENCE" and evidence == "rank-owner:" + fact["card"]
                 and source in ("INFERRED_ORDER_CONSTRAINT", "INFERRED_COMPONENT_CONSTRAINT")
                 and d.get("global_constraint_probe") is True, "GLOBAL_DERIVATION")
        if d.get("global_constraint_probe"):
            premises = d.get("premise_evidence_ids"); constraints = d.get("ownership_constraints", {})
            need(d.get("global_constraint_probe") is True and d.get("rank_constraints_ref") == "recovery.rank_constraints_snapshot"
                 and d.get("rank_constraints_sha256") == recovery["rank_constraints_sha256"]
                 and type(premises) is list and evidence not in premises
                 and all(type(r) is str and r in facts for r in premises) and len(set(premises)) == len(premises)
                 and isinstance(constraints, Mapping) and constraints.get("premise_evidence_ids") == premises
                 and constraints.get("deck") == recovery["constraints"]["deck"]
                 and constraints.get("each_card_exactly_once") is True
                 and constraints.get("original_hand_sizes") == dict.fromkeys(SEATS, 13), "GLOBAL_CONSTRAINT_BINDING")
        dependencies[evidence] = set(d.get("premise_evidence_ids", d.get("anchors", []))) & set(derived)
    # Provenance is a directed acyclic graph, not mutual self-authorization.
    pending = {key: len(values) for key, values in dependencies.items()}
    consumers = {key: [] for key in pending}
    for key, values in dependencies.items():
        for value in values:
            consumers[value].append(key)
    ready = [key for key, count in pending.items() if count == 0]
    visited = 0
    while ready:
        key = ready.pop(); visited += 1
        for consumer in consumers[key]:
            pending[consumer] -= 1
            if pending[consumer] == 0:
                ready.append(consumer)
    need(visited == len(pending), "CYCLIC_DERIVATION")
    for card, proof in recovery.get("inferred", {}).items():
        support = [r for r, f in derived.items() if f["card"] == card and f["seat"] == proof["seat"]]
        if support:
            support.sort()
            need(proof.get("premise_evidence_ids") == support and proof.get("method") == facts[support[0]]["source"]
                 and proof.get("derivations") == [facts[r]["derivation"] for r in support], "INFERRED_DERIVATION_PARTITION")
        else:
            need(proof.get("method") == "ONLY_FEASIBLE_OWNER"
                 and proof.get("premise_evidence_ids") == recovery["constraints"]["premise_evidence_ids"], "INFERRED_CONSTRAINT_SUPPORT")
