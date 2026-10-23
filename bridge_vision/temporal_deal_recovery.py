"""Evidence-gated extension of the partial observer, never a pixel classifier.

The separate review manifest is a trust boundary, not a recognizer output.
Its author attests source-to-frame/PTS, deal occurrence, settled state, screen
orientation and individual cards. Source bytes are additionally hashed here.
No score, repeated frame or supplied SHA alone can create a reviewed fact.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from pathlib import Path
from typing import Any

from bridge_contracts.video_deal import FULL_DECK, SEATS
from bridge_vision.original_deal_solver import RecoveryInputError, recover_original_deal
from bridge_vision.bridgit_rank_order_evidence import reviewed_rank_rows, recover_with_rank_intervals
from bridge_vision.bridgit_component_evidence import factor_component_view, attach_reviewed_components
from bridge_vision.teacher_card_reconstruction_r265 import reviewed_speech_declarations
from bridge_vision.runtime_recovery_evidence import RuntimeRecoveryEvidence

TEMPORAL_VERSION = "bridge-temporal-recovery-v1"
MAX_EVENTS = 2048
_SHA = re.compile(r"^[0-9a-f]{64}$")
BINDINGS = ("source_video_sha256", "frame_sha256", "episode_id", "board_epoch",
            "board_number", "timestamp_ms", "observation_index", "raw_frame_size",
            "recognizer_version", "reference_profile_id", "data_class", "pixel_observer_version")


def _digest(value: Any) -> str:
    try:
        return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                 allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError) as exc:
        raise RecoveryInputError("NON_JSON_RECOVERY_INPUT") from exc


def _ref(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= 512


def _binding_shape(frame: Mapping[str, Any]) -> bool:
    size = frame.get("raw_frame_size")
    return (all(isinstance(frame.get(k), str) and _SHA.fullmatch(frame[k])
                for k in ("source_video_sha256", "frame_sha256"))
            and all(_ref(frame.get(k)) for k in ("episode_id", "recognizer_version", "reference_profile_id"))
            and all(type(frame.get(k)) is int and frame[k] >= 1 for k in ("board_epoch", "board_number"))
            and isinstance(size, Mapping)
            and all(type(size.get(k)) is int and 1 <= size[k] <= 32768 for k in ("width", "height"))
            and frame.get("data_class") in ("SYNTHETIC_REGRESSION", "REAL_SHADOW")
            and (frame["data_class"] != "REAL_SHADOW" or _ref(frame.get("pixel_observer_version"))))


def event_id(frame: Mapping[str, Any]) -> str:
    """Stable delivery identity, independent of pixels/payload to detect tampering."""
    return _digest([frame.get(k) for k in ("source_video_sha256", "episode_id", "observation_index")])


def _reviewed_colour_checks(frame, proof, facts):
    """Check separately reviewed calibrated measurements; never classify pixels.

    The missing historical colour extractor is NOT recreated here. Its eventual
    replacement must provide symbol ROI, explicit theme palette and exact cue
    binding. Background/card-back/highlight colours cannot enter constraints.
    """
    reviews = proof.get("colour_reviews", [])
    if not isinstance(reviews, list) or len(reviews) > 52:
        raise RecoveryInputError("COLOUR_REVIEW_LIMIT_OR_TYPE")
    accepted, rejected, conflicts = [], [], []
    by_ref = {fact["evidence_id"]: fact for fact in facts}
    for review in reviews:
        if not isinstance(review, Mapping):
            raise RecoveryInputError("COLOUR_REVIEW_INVALID")
        palette, box = review.get("palette"), review.get("symbol_roi")
        good_palette = (isinstance(palette, dict) and 1 <= len(palette) <= 4
            and all(isinstance(k, str) and isinstance(v, list) and v
                    and all(isinstance(s, str) and s in "SHDC" and len(s) == 1 for s in v)
                    for k, v in palette.items()))
        if good_palette:
            suits = [s for v in palette.values() for s in v]
            good_palette = len(suits) == 4 and set(suits) == set("SHDC")
        ref = review.get("card_evidence_id")
        fact = by_ref.get(ref) if isinstance(ref, str) else None
        valid = (good_palette and fact is not None and review.get("frame_sha256") == frame["frame_sha256"]
            and review.get("review_status") == "VERIFIED" and review.get("roi_kind") == "SUIT_SYMBOL"
            and isinstance(review.get("measured_colour"), str) and review["measured_colour"] in palette
            and all(_ref(review.get(k)) for k in ("calibration_ref", "measurement_ref", "slot_binding_ref"))
            and isinstance(box, dict) and all(type(box.get(k)) is int for k in ("x", "y", "w", "h"))
            and min(box["x"], box["y"]) >= 0 and min(box["w"], box["h"]) > 0
            and box["x"] + box["w"] <= frame["raw_frame_size"]["width"]
            and box["y"] + box["h"] <= frame["raw_frame_size"]["height"])
        if not valid:
            rejected.append({"reason": "COLOUR_CALIBRATION_ROI_OR_BINDING_UNPROVEN", "review": deepcopy(review)})
        else:
            accepted.append(deepcopy(review))
            if fact["card"][1] not in palette[review["measured_colour"]]:
                conflicts.append({"reason": "CALIBRATED_COLOUR_CARD_CONFLICT", "card": fact["card"],
                                  "card_evidence_id": ref, "colour_review": deepcopy(review)})
    return accepted, rejected, conflicts


def _played_zone_owner(frame, proof, card_proof):
    """No built-in Gambler mini-trick coordinates or screen-to-seat guesses."""
    zones = proof.get("played_zones", [])
    if not isinstance(zones, list) or len(zones) > 8:
        raise RecoveryInputError("PLAYED_ZONE_LIMIT_OR_TYPE")
    matches = [z for z in zones if isinstance(z, Mapping) and z.get("zone_id") == card_proof.get("zone_id")]
    if len(matches) != 1:
        return None
    zone = matches[0]
    mapping, boxes = zone.get("slot_to_seat"), zone.get("slot_boxes")
    if (zone.get("zone_type") not in ("previous_trick", "current_trick", "played_in_front_of_hand")
            or zone.get("zone_type") != card_proof.get("zone_type")
            or not all(_ref(zone.get(k)) for k in ("zone_id", "layout_ref", "calibration_ref", "orientation_ref", "orientation_id"))
            or not isinstance(mapping, dict) or len(mapping) != 4
            or any(not _ref(k) for k in mapping) or any(not isinstance(v, str) for v in mapping.values())
            or set(mapping.values()) != set(SEATS) or not isinstance(boxes, dict) or set(boxes) != set(mapping)
            or not isinstance(card_proof.get("zone_slot"), str) or card_proof["zone_slot"] not in mapping):
        return None
    seen = set()
    for box in boxes.values():
        if (not isinstance(box, dict) or any(type(box.get(k)) is not int for k in ("x", "y", "w", "h"))
                or min(box["x"], box["y"]) < 0 or min(box["w"], box["h"]) <= 0
                or box["x"] + box["w"] > frame["raw_frame_size"]["width"]
                or box["y"] + box["h"] > frame["raw_frame_size"]["height"]):
            return None
        key = tuple(box[k] for k in ("x", "y", "w", "h"))
        if key in seen:
            return None
        seen.add(key)
    if card_proof.get("zone_slot_box") != boxes[card_proof["zone_slot"]]:
        return None
    observed_box = frame["observations"][card_proof["observation_index"]].get("box")
    if observed_box is not None and observed_box != boxes[card_proof["zone_slot"]]:
        return None
    return mapping[card_proof["zone_slot"]]


@dataclass(frozen=True)
class ReviewedRecoveryBatch:
    """Dependency-injected runner input, bound to exact output-deal evidence IDs."""
    deal_evidence_ids: tuple[str, ...]
    frames: Sequence[Mapping[str, Any]]
    evidence: ReviewedRecoveryEvidence
    target_sha256: str


class ReviewedRecoveryEvidence:
    """Explicit independent-review input, with source bytes checked once.

    This does not authenticate a human or prove their labels correct. The caller
    must obtain this manifest independently of machine hypotheses and freeze it
    before evaluation. Never manufacture one from ACCEPTED_VISUAL or scores.
    """

    def __init__(self, source_path: Path, manifest: Mapping[str, Any]):
        if (not isinstance(manifest, Mapping) or manifest.get("version") != TEMPORAL_VERSION
                or manifest.get("authority") != "INDEPENDENT_REVIEW"
                or not _ref(manifest.get("reviewer_ref"))
                or not isinstance(manifest.get("events"), list)
                or not 1 <= len(manifest["events"]) <= MAX_EVENTS):
            raise RecoveryInputError("INDEPENDENT_REVIEW_MANIFEST_REQUIRED")
        # JSON round trip isolates the evidence from caller mutation and bounds
        # storage before processing an entire video or invoking any detector.
        try:
            encoded = json.dumps(dict(manifest), sort_keys=True, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise RecoveryInputError("REVIEW_MANIFEST_NOT_JSON") from exc
        if len(encoded) > 16 * 1024 * 1024:
            raise RecoveryInputError("REVIEW_MANIFEST_TOO_LARGE")
        frozen = json.loads(encoded)
        if not isinstance(source_path, Path) or not source_path.is_file():
            raise RecoveryInputError("SOURCE_BYTES_UNAVAILABLE")
        digest = sha256()
        with source_path.open("rb") as stream:
            before = source_path.stat()
            if not 0 < before.st_size <= 16 * 1024 ** 3:
                raise RecoveryInputError("SOURCE_BYTE_LIMIT")
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
            after = source_path.stat()
        if (before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns
                or digest.hexdigest() != frozen.get("source_video_sha256")):
            raise RecoveryInputError("SOURCE_BYTES_MISMATCH")
        self._source_sha = digest.hexdigest()
        self._manifest_sha = sha256(encoded.encode()).hexdigest()
        self._reviewer_ref = frozen["reviewer_ref"]
        self._events: dict[str, dict[str, Any]] = {}
        for receipt in frozen["events"]:
            if not isinstance(receipt, dict) or any(k not in receipt for k in BINDINGS):
                raise RecoveryInputError("REVIEW_EVENT_INVALID")
            key = event_id(receipt)
            if key in self._events:
                raise RecoveryInputError("REVIEW_EVENT_DUPLICATED")
            self._events[key] = receipt

    def bind(self, frame: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        if not _binding_shape(frame):
            return None, "FRAME_IDENTITY_UNPROVEN"
        proof = self._events.get(event_id(frame))
        if proof is None:
            return None, "FRAME_REVIEW_MISSING"
        if (frame.get("source_video_sha256") != self._source_sha
                or not _binding_shape(proof)
                or _digest({k: proof.get(k) for k in BINDINGS})
                != _digest({k: frame.get(k) for k in BINDINGS})):
            return None, "REVIEW_FRAME_BINDING_MISMATCH"
        if (proof.get("settled") is not True
                or any(not _ref(proof.get(k)) for k in
                       ("scene_id", "orientation_id", "source_frame_pts_ref", "deal_identity_ref",
                        "seat_assignment_ref", "settled_frame_ref"))):
            return None, "SOURCE_TIME_DEAL_SEAT_OR_SETTLED_PROOF_MISSING"
        seat_map = proof.get("screen_to_seat")
        if (not isinstance(seat_map, dict) or set(seat_map) != set(SEATS)
                or any(not isinstance(v, str) for v in seat_map.values())
                or set(seat_map.values()) != set(SEATS)):
            return None, "ORIENTATION_UNPROVEN"
        # Physical table seats maintain clockwise order. Mirrors/permutations
        # need a different qualified geometry model, not silent reassignment.
        mapped = [seat_map[s] for s in SEATS]
        if not any(mapped == list(SEATS[i:] + SEATS[:i]) for i in range(4)):
            return None, "ORIENTATION_UNPROVEN"
        verified = proof.get("cards")
        if (not isinstance(verified, list) or len(verified) > 52
                or any(not isinstance(c, dict) for c in verified)):
            return None, "CARD_REVIEW_INVALID"
        return deepcopy(proof), None

    def provenance(self) -> dict[str, str]:
        return {"manifest_sha256": self._manifest_sha, "reviewer_ref": self._reviewer_ref,
                "source_video_sha256": self._source_sha,
                "source_verification": "BYTES_HASHED",
                "semantic_verification": "INDEPENDENT_REVIEW_ASSERTIONS"}


def accumulate_recovery_frames(
    frames: Sequence[Mapping[str, Any]], *, evidence: ReviewedRecoveryEvidence | RuntimeRecoveryEvidence | None = None,
) -> dict[str, Any]:
    """Accumulate existing partial records under exact reviewed transition links.

    Unknown evidence closes scope. A board/scene/orientation/model change always
    resets it. Gaps, pauses and seeks cannot be bridged by elapsed time alone:
    an independent continuity receipt must name the exact previous event.
    """
    if not isinstance(frames, (list, tuple)) or len(frames) > MAX_EVENTS:
        raise RecoveryInputError("FRAME_LIMIT_OR_TYPE_INVALID")
    if evidence is not None and not isinstance(evidence, (ReviewedRecoveryEvidence, RuntimeRecoveryEvidence)):
        raise RecoveryInputError("REVIEW_EVIDENCE_TYPE_INVALID")
    ordered = []
    for frame in frames:
        if (not isinstance(frame, Mapping) or type(frame.get("observation_index")) is not int
                or frame["observation_index"] < 0
                or type(frame.get("timestamp_ms")) is not int or frame["timestamp_ms"] < 0
                or not isinstance(frame.get("observations"), list) or len(frame["observations"]) > 52):
            raise RecoveryInputError("RECOVERY_FRAME_INVALID")
        for field in ("rejected_observations", "detector_conflicts"):
            values = frame.get(field, [])
            if (not isinstance(values, list) or len(values) > 104
                    or any(not isinstance(v, Mapping) for v in values)):
                raise RecoveryInputError("RECOVERY_DIAGNOSTICS_INVALID")
        declarations = frame.get("teacher_declarations", [])
        if (not isinstance(declarations, list) or len(declarations) > 52
                or any(not isinstance(item, Mapping) for item in declarations)):
            raise RecoveryInputError("RECOVERY_SPEECH_INVALID")
        record = {k: v for k, v in frame.items() if k != "frame_path"}
        if len(json.dumps(record, default=str)) > 128 * 1024:
            raise RecoveryInputError("RECOVERY_FRAME_TOO_LARGE")
        ordered.append((event_id(record), _digest(record), record))
    # Sequence is the source order. Delivery order cannot change a result.
    ordered.sort(key=lambda x: (str(x[2].get("source_video_sha256")),
                               str(x[2].get("episode_id")), x[2]["observation_index"], x[1]))
    payloads: dict[str, set[str]] = {}
    for key, digest, _ in ordered:
        payloads.setdefault(key, set()).add(digest)
    seen: set[tuple[str, str]] = set()
    scopes: list[dict[str, Any]] = []
    history = []
    current = None
    previous = None
    for key, digest, frame in ordered:
        if (key, digest) in seen:
            continue  # Exact redelivery is idempotent, not an extra event/vote.
        seen.add((key, digest))
        proof, reason = evidence.bind(frame) if evidence else (None, "QUALIFIED_EVIDENCE_UNAVAILABLE")
        history.append({"event_id": key, "payload_sha256": digest, "frame": deepcopy(frame),
                        "component_observations": [factor_component_view(item) for item in frame.get("rejected_observations", [])],
                        "review_status": reason or "BOUND_TO_REVIEW"})
        if len(payloads[key]) != 1:
            reason = "EVENT_PAYLOAD_CONFLICT"
        if reason:
            current = None
            previous = None
            scopes.append({"status": "CONFLICT" if reason in ("EVENT_PAYLOAD_CONFLICT", "RUNTIME_INTEGRITY_CONFLICT") else "CLOSED_UNKNOWN",
                           "reason": reason, "event_ids": [key], "recovery": None})
            continue
        assert proof is not None
        identity = [frame.get(k) for k in ("source_video_sha256", "episode_id", "board_epoch",
                    "board_number", "raw_frame_size", "recognizer_version", "reference_profile_id", "data_class",
                    "pixel_observer_version")]
        identity += [proof["scene_id"], proof["orientation_id"], proof["screen_to_seat"]]
        continuous = (current is not None and current["identity"] == identity and previous is not None
                      and proof.get("continuous_from_event_id") == previous["event_id"]
                      and _ref(proof.get("continuity_ref"))
                      and frame["observation_index"] == previous["index"] + 1
                      and frame["timestamp_ms"] > previous["time"])
        if not continuous:
            current = {"identity": identity, "status": "SCOPED_OBSERVATIONS", "event_ids": [],
                       "facts": [], "review_observations": [], "conflicts": [], "visual_units": set(),
                       "review_receipts": {},
                       "rank_intervals": [],
                       "colour_evidence": [],
                       "teacher_candidates": [],
                       "evidence": evidence.provenance()}
            scopes.append(current)
        current["event_ids"].append(key)
        current["review_receipts"][key] = deepcopy(proof)
        scope_identity = {k: frame[k] for k in ("source_video_sha256", "episode_id", "board_epoch", "board_number")}
        scope_identity.update({k: proof.get(k) for k in ("deal_start_ms", "deal_end_ms")})
        if frame.get("teacher_declarations") and not (
                type(scope_identity["deal_start_ms"]) is int and type(scope_identity["deal_end_ms"]) is int
                and scope_identity["deal_start_ms"] <= frame["timestamp_ms"] < scope_identity["deal_end_ms"]):
            scope_identity = None
        declarations, rejected = reviewed_speech_declarations(
            frame.get("teacher_declarations", []), scope=scope_identity, reviews=proof.get("speech_reviews", []))
        current["teacher_candidates"].extend(declarations)
        current["review_observations"].extend(rejected)
        current["visual_units"].add(frame["frame_sha256"])
        current["review_observations"].extend(deepcopy(frame.get("rejected_observations") or []))
        # Machine screen-seat conflicts are review cues. Qualified played-zone
        # mappings can explain duplicate visible instances at different places.
        current["review_observations"].extend(deepcopy(frame.get("detector_conflicts") or []))
        observed = frame["observations"]
        reviewed_indices = set()
        for card_proof in proof["cards"]:
            idx = card_proof.get("observation_index")
            if (type(idx) is not int or not 0 <= idx < len(observed)
                    or idx in reviewed_indices or not _ref(card_proof.get("evidence_ref"))):
                current["conflicts"].append({"reason": "CARD_REVIEW_BINDING_INVALID", "event_id": key})
                continue
            reviewed_indices.add(idx)
            item = observed[idx]
            if (not isinstance(item, Mapping) or item.get("status") != "ACCEPTED_VISUAL"
                    or item.get("source") != "VISUAL"
                    or not isinstance(item.get("card"), str) or item["card"] not in FULL_DECK
                    or not isinstance(item.get("seat"), str) or item["seat"] not in SEATS
                    or card_proof.get("card") != item["card"]
                    or card_proof.get("screen_seat") != item["seat"]):
                current["conflicts"].append({"reason": "CARD_REVIEW_BINDING_MISMATCH", "event_id": key})
                continue
            kind = card_proof.get("kind")
            seat = proof["screen_to_seat"][item["seat"]]
            if kind == "PLAYED":
                if card_proof.get("trick_id") is not None and not _ref(card_proof["trick_id"]):
                    current["review_observations"].append({"reason": "PLAYED_TRICK_ID_INVALID", "observation": item})
                    continue
                if card_proof.get("zone_type") is not None:
                    seat = _played_zone_owner(frame, proof, card_proof)
                    if seat is None or not _ref(card_proof.get("trick_id")):
                        current["review_observations"].append({"reason": "PLAYED_ZONE_MAPPING_UNPROVEN", "observation": item})
                        continue
                at = card_proof.get("played_at_ms")
                if (type(at) is not int or not 0 <= at <= frame["timestamp_ms"]
                        or card_proof.get("played_by") != seat
                        or not _ref(card_proof.get("seat_time_ref"))):
                    current["review_observations"].append({"reason": "PLAYED_SEAT_TIME_UNPROVEN", "observation": item})
                    continue
                if card_proof.get("zone_type") is not None or "deal_start_ms" in proof or "deal_end_ms" in proof:
                    start, end = proof.get("deal_start_ms"), proof.get("deal_end_ms")
                    if (type(start) is not int or type(end) is not int
                            or not 0 <= start <= at <= frame["timestamp_ms"] < end):
                        current["review_observations"].append({"reason": "PLAYED_BOARD_INTERVAL_UNPROVEN", "observation": item})
                        continue
            elif kind != "IN_HAND":
                current["review_observations"].append({"reason": "CARD_ROLE_UNPROVEN", "observation": item})
                continue
            current["facts"].append({"card": item["card"], "seat": seat,
                                     "evidence_id": key + ":" + str(idx),
                                     "kind": kind, "review_ref": card_proof["evidence_ref"],
                                     "initial_owner": seat, "membership": kind,
                                     "frame_sha256": frame["frame_sha256"],
                                     "timestamp_ms": frame["timestamp_ms"],
                                     "played_at_ms": card_proof.get("played_at_ms"),
                                     "zone_type": card_proof.get("zone_type"), "zone_id": card_proof.get("zone_id"),
                                     "trick_id": card_proof.get("trick_id"),
                                     "seat_time_ref": card_proof.get("seat_time_ref")})
        for idx, item in enumerate(observed):
            if idx not in reviewed_indices:
                current["review_observations"].append({"reason": "CARD_NOT_INDEPENDENTLY_REVIEWED", "observation": deepcopy(item)})
        rank_result = reviewed_rank_rows(frame, proof, current["facts"], key)
        attach_reviewed_components(frame, proof, key, rank_result)
        current["facts"].extend(rank_result["facts"])
        current["rank_intervals"].extend(rank_result["intervals"])
        current["review_observations"].extend(rank_result["rejected"])
        current["conflicts"].extend(rank_result["conflicts"])
        previous = {"event_id": key, "index": frame["observation_index"], "time": frame["timestamp_ms"]}
    for scope in scopes:
        if "facts" not in scope:
            continue
        scope["visual_unit_count"] = len(scope.pop("visual_units"))
        scope["independent_vote_count"] = 0  # No voting or confidence accumulation.
        visual_cards = {fact["card"] for fact in scope["facts"] if fact.get("source", "VISUAL") == "VISUAL"}
        # Preserve r265 visual-authoritative precedence. Speech only fills cards
        # still unknown after all reviewed visual frames in this exact scope.
        for declaration in scope.pop("teacher_candidates"):
            if declaration["card"] in visual_cards:
                scope["review_observations"].append({"reason": "VISUAL_CARD_ALREADY_RESOLVED",
                                                     "declaration": declaration})
                continue
            scope["facts"].append({"card": declaration["card"], "seat": declaration["seat"],
                                   "source": "TEACHER_SPEECH", "kind": "TEACHER_SPEECH",
                                   "evidence_id": declaration["review_evidence"]["evidence_ref"],
                                   "declaration": declaration})
        played = {}
        played_tricks = {}
        trick_owner_cards = {}
        play_events = {}
        for fact in scope["facts"]:
            if fact["kind"] == "PLAYED":
                trick = fact.get("trick_id")
                if trick is not None and fact["card"] in played_tricks and played_tricks[fact["card"]] != trick:
                    scope["conflicts"].append({"reason": "CARD_PLAYED_IN_MULTIPLE_TRICKS", "card": fact["card"]})
                if trick is not None:
                    played_tricks[fact["card"]] = trick
                    trick_seat = (trick, fact["seat"])
                    if trick_seat in trick_owner_cards and trick_owner_cards[trick_seat] != fact["card"]:
                        scope["conflicts"].append({"reason": "MULTIPLE_CARDS_FROM_SEAT_IN_ONE_TRICK", "trick_id": trick, "seat": fact["seat"]})
                    trick_owner_cards[trick_seat] = fact["card"]
                play_key = _digest([scope["identity"][:4], fact.get("trick_id"), fact["card"], fact["seat"]])
                entry = play_events.setdefault(play_key, {"card": fact["card"], "initial_owner": fact["seat"],
                    "trick_id": fact.get("trick_id"), "played_at_ms": fact["played_at_ms"], "display_evidence_ids": []})
                entry["display_evidence_ids"].append(fact["evidence_id"])
                fact["play_event_id"] = play_key
                previous_play = played.get(fact["card"])
                if previous_play is not None and previous_play != fact["played_at_ms"]:
                    scope["conflicts"].append({"reason": "CARD_PLAYED_TWICE", "card": fact["card"]})
                played[fact["card"]] = fact["played_at_ms"]
        scope["played_events"] = play_events
        for fact in scope["facts"]:
            if (fact["kind"] == "IN_HAND" and fact["card"] in played
                    and fact["timestamp_ms"] > played[fact["card"]]):
                scope["conflicts"].append({"reason": "CARD_IN_HAND_AFTER_PLAY", "card": fact["card"]})
        (scope["recovery"], scope["facts"], scope["rank_intervals"], scope["propagation_trace"],
         scope["propagation_complete"]) = recover_with_rank_intervals(scope["facts"], scope["rank_intervals"])
        for history_item in history:
            key = history_item["event_id"]
            if key in scope["review_receipts"]:
                colours, rejected, conflicts = _reviewed_colour_checks(
                    history_item["frame"], scope["review_receipts"][key], scope["facts"])
                scope["colour_evidence"].extend(colours)
                scope["review_observations"].extend(rejected)
                scope["conflicts"].extend(conflicts)
        if scope["conflicts"]:
            scope["recovery"].update(status="CONFLICT", solution_status="CONFLICT", inferred={}, domains={}, deal=None)
            scope["recovery"]["conflicts"].extend(scope["conflicts"])
        scope["status"] = scope["recovery"]["status"]
    status = ("CONFLICT" if any(s["status"] == "CONFLICT" for s in scopes)
              else "SCOPED_OBSERVATIONS" if any("facts" in s for s in scopes) else "CLOSED_UNKNOWN")
    return {"version": TEMPORAL_VERSION, "status": status, "scopes": scopes, "history": history,
            "canonical_promotion_allowed": False, "real_video_accuracy_evaluated": False,
            "geometry_enabled": False}
