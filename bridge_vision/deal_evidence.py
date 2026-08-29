"""Source-bound SHADOW evidence intake for stable Video 3.1 FREE.

The adapter consumes a normalized evidence bundle produced by a card/auction
observer.  It does not run a detector and cannot invent missing cards.  Every
accepted card is bound to the exact source video and to one or more local,
hash-verified evidence frames.  Machine observations additionally require
independent rank, suit, and full-card channels plus temporal confirmation.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from bridge_contracts.video_auction import validate_auction_prefix
from bridge_contracts.video_deal import SEATS, canonicalize_video_deal

SCHEMA = "bridge-3.1-free-deal-evidence/v1"
RESULT_SCOPE = "SHADOW_ONLY"
MIN_MACHINE_CONFIDENCE = 0.90
MIN_MACHINE_FRAMES = 2
MAX_BUNDLE_BYTES = 8 * 1024 * 1024
MAX_DEALS = 256
MAX_CARD_OBSERVATIONS = 52
MAX_FRAMES_PER_OBSERVATION = 8
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class DealEvidenceError(ValueError):
    pass


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def evidence_payload_sha256(bundle: Mapping[str, Any]) -> str:
    payload = dict(bundle)
    payload.pop("payload_sha256", None)
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sequence(value: Any, name: str, *, maximum: int) -> list[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise DealEvidenceError(f"{name} must be an array")
    if len(value) > maximum:
        raise DealEvidenceError(f"{name} exceeds its limit")
    return list(value)


def _probability(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise DealEvidenceError(f"invalid {name}") from exc
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise DealEvidenceError(f"{name} outside [0,1]")
    return result


def _positive_int(value: Any, name: str, *, maximum: int) -> int:
    if isinstance(value, bool):
        raise DealEvidenceError(f"invalid {name}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise DealEvidenceError(f"invalid {name}") from exc
    if not math.isfinite(number) or not number.is_integer() or not 1 <= number <= maximum:
        raise DealEvidenceError(f"invalid {name}")
    return int(number)


def _normalise_card(value: Any, seat: str) -> str:
    try:
        hand = canonicalize_video_deal({"hands": {seat: [value]}}).to_dict()["hands"][seat]
    except Exception as exc:
        raise DealEvidenceError(f"invalid card observation for {seat}") from exc
    return hand["cards"][0]


def _verify_source(bundle: Mapping[str, Any], source: Mapping[str, Any]) -> None:
    raw = bundle.get("source")
    if not isinstance(raw, Mapping):
        raise DealEvidenceError("evidence source binding is missing")
    try:
        expected = {
            "driveId": str(source.get("driveId") or ""),
            "sha256": str(source.get("sha256") or "").lower(),
            "sizeBytes": int(source.get("sizeBytes") or 0),
        }
        actual = {
            "driveId": str(raw.get("driveId") or ""),
            "sha256": str(raw.get("sha256") or "").lower(),
            "sizeBytes": int(raw.get("sizeBytes") or 0),
        }
    except (TypeError, ValueError) as exc:
        raise DealEvidenceError("deal evidence source size is invalid") from exc
    if not expected["driveId"] or not _SHA256.fullmatch(expected["sha256"]):
        raise DealEvidenceError("runtime source passport is incomplete")
    if actual != expected:
        raise DealEvidenceError("deal evidence does not match the exact source video")


def _verify_producer(bundle: Mapping[str, Any]) -> dict[str, Any]:
    raw = bundle.get("producer")
    if not isinstance(raw, Mapping):
        raise DealEvidenceError("evidence producer is missing")
    kind = str(raw.get("kind") or "").upper()
    revision = str(raw.get("revision") or "")
    if kind not in {"PROFILED_PIXEL_BACKEND", "HUMAN_REVIEW", "MIXED_REVIEW"}:
        raise DealEvidenceError("unsupported evidence producer kind")
    if not _SAFE_ID.fullmatch(revision):
        raise DealEvidenceError("invalid evidence producer revision")
    result = {"kind": kind, "revision": revision}
    if kind in {"PROFILED_PIXEL_BACKEND", "MIXED_REVIEW"}:
        for key in ("backend_sha256", "profile_sha256", "config_sha256"):
            digest = str(raw.get(key) or "").lower()
            if not _SHA256.fullmatch(digest):
                raise DealEvidenceError(f"producer {key} is missing")
            result[key] = digest
    return result


def _verified_shots(shots: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for shot in shots:
        if not isinstance(shot, Mapping):
            raise DealEvidenceError("evidence shot must be an object")
        evidence_id = str(shot.get("evidence_id") or "")
        expected = str(shot.get("sha256") or "").lower()
        path = Path(str(shot.get("path") or ""))
        try:
            timestamp = float(shot.get("time"))
        except (TypeError, ValueError) as exc:
            raise DealEvidenceError("evidence shot has invalid time") from exc
        if (
            not _SAFE_ID.fullmatch(evidence_id)
            or not _SHA256.fullmatch(expected)
            or not path.is_absolute()
            or path.is_symlink()
            or not path.is_file()
            or _sha256(path) != expected
            or not math.isfinite(timestamp)
            or timestamp < 0
        ):
            raise DealEvidenceError("evidence shot failed hash/path validation")
        if evidence_id in result:
            raise DealEvidenceError("duplicate evidence shot id")
        result[evidence_id] = {
            "evidence_id": evidence_id,
            "sha256": expected,
            "time": timestamp,
            "path": str(path),
        }
    return result


def _frames(raw: Any, shots: Mapping[str, Mapping[str, Any]], name: str) -> list[dict[str, Any]]:
    values = _sequence(raw, name, maximum=MAX_FRAMES_PER_OBSERVATION)
    if not values:
        raise DealEvidenceError(f"{name} must not be empty")
    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in values:
        if not isinstance(item, Mapping):
            raise DealEvidenceError(f"{name} entry must be an object")
        evidence_id = str(item.get("evidence_id") or "")
        digest = str(item.get("sha256") or "").lower()
        try:
            timestamp = float(item.get("time"))
        except (TypeError, ValueError) as exc:
            raise DealEvidenceError(f"{name} entry has invalid time") from exc
        shot = shots.get(evidence_id)
        if (
            shot is None
            or digest != shot["sha256"]
            or not math.isfinite(timestamp)
            or abs(timestamp - float(shot["time"])) > 0.001
        ):
            raise DealEvidenceError(f"{name} is not bound to a verified local frame")
        if evidence_id in seen_ids:
            raise DealEvidenceError(f"{name} repeats an evidence frame")
        seen_ids.add(evidence_id)
        result.append({"evidence_id": evidence_id, "sha256": digest, "time": timestamp})
    return result


def _verification(raw: Any, frames: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise DealEvidenceError("deal verification must be an object")
    seats = {str(value).upper() for value in _sequence(raw.get("verified_seats"), "verified_seats", maximum=4)}
    if not seats or not seats.issubset(set(SEATS)):
        raise DealEvidenceError("verified_seats contains an unsupported seat")
    method = str(raw.get("method") or "").strip()
    reviewer = str(raw.get("reviewer") or "").strip()
    verified_at = str(raw.get("verified_at") or "").strip()
    reference_sha = str(raw.get("reference_frame_sha256") or "").lower()
    if not method or len(method) > 160 or not reviewer or len(reviewer) > 160:
        raise DealEvidenceError("human verification lacks method or reviewer")
    if not _UTC.fullmatch(verified_at) or not _SHA256.fullmatch(reference_sha):
        raise DealEvidenceError("human verification lacks exact timestamp or frame hash")
    if reference_sha not in {str(item.get("sha256")) for item in frames}:
        raise DealEvidenceError("human verification frame is not part of deal evidence")
    return {
        "status": "HUMAN_VERIFIED",
        "verified_seats": sorted(seats),
        "method": method,
        "reviewer": reviewer,
        "verified_at": verified_at,
        "reference_frame_sha256": reference_sha,
    }


def _machine_channels(raw: Mapping[str, Any]) -> tuple[dict[str, float], dict[str, str]]:
    confidences = raw.get("confidence")
    channels = raw.get("channels")
    if not isinstance(confidences, Mapping) or not isinstance(channels, Mapping):
        raise DealEvidenceError("machine card observation lacks confidence/channels")
    confidence = {
        key: _probability(confidences.get(key), f"{key} confidence")
        for key in ("rank", "suit", "reference")
    }
    if min(confidence.values()) < MIN_MACHINE_CONFIDENCE:
        raise DealEvidenceError("machine card confidence below 0.90 gate")
    channel_ids = {key: str(channels.get(key) or "").strip() for key in confidence}
    if any(not value or len(value) > 128 for value in channel_ids.values()):
        raise DealEvidenceError("invalid machine card channel id")
    if len(set(channel_ids.values())) != 3:
        raise DealEvidenceError("rank, suit, and reference channels must be independent")
    return confidence, channel_ids


def _auction(raw: Any, shots: Mapping[str, Mapping[str, Any]]) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise DealEvidenceError("auction evidence must be an object")
    dealer = str(raw.get("dealer") or "").upper()
    calls = _sequence(raw.get("calls"), "auction calls", maximum=80)
    legality = validate_auction_prefix(calls, dealer=dealer)
    frames = _frames(raw.get("frames"), shots, "auction frames")
    status = str(raw.get("status") or "").upper()
    if status == "COMPLETE_CONFIRMED":
        if not legality["terminated"] or len({item["sha256"] for item in frames}) < 2:
            raise DealEvidenceError("confirmed auction lacks legal termination or two frames")
    elif status != "PARTIAL_REVIEW":
        raise DealEvidenceError("unsupported auction evidence status")
    return {
        "status": status,
        "dealer": legality["dealer"],
        "calls": legality["normalized_calls"],
        "complete": bool(legality["terminated"]),
        "termination": legality["termination"],
        "frame_confirmations": frames,
        "accepted_as_standard_pbn": status == "COMPLETE_CONFIRMED",
        "canonical_promotion_allowed": False,
    }


def apply_deal_evidence_bundle(
    bundle: Mapping[str, Any],
    *,
    source: Mapping[str, Any],
    shots: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate a bundle and return stable-master deals plus an audit summary."""
    if not isinstance(bundle, Mapping):
        raise DealEvidenceError("deal evidence bundle must be an object")
    if bundle.get("schema") != SCHEMA:
        raise DealEvidenceError("unsupported deal evidence schema")
    if bundle.get("result_scope") != RESULT_SCOPE:
        raise DealEvidenceError("deal evidence must remain SHADOW_ONLY")
    if bundle.get("canonical_promotion_allowed") is not False:
        raise DealEvidenceError("deal evidence enables canonical promotion")
    if bundle.get("production_activation_allowed") is not False:
        raise DealEvidenceError("deal evidence enables production activation")
    claimed_digest = str(bundle.get("payload_sha256") or "").lower()
    if not _SHA256.fullmatch(claimed_digest) or claimed_digest != evidence_payload_sha256(bundle):
        raise DealEvidenceError("deal evidence payload digest mismatch")
    _verify_source(bundle, source)
    producer = _verify_producer(bundle)
    verified_shots = _verified_shots(shots)
    deals_raw = _sequence(bundle.get("deals"), "deals", maximum=MAX_DEALS)

    deals: list[dict[str, Any]] = []
    seen_deals: set[str] = set()
    total_cards = machine_cards = human_cards = complete_deals = 0
    for raw in deals_raw:
        if not isinstance(raw, Mapping):
            raise DealEvidenceError("deal evidence entry must be an object")
        deal_id = str(raw.get("deal_id") or "")
        if not _SAFE_ID.fullmatch(deal_id) or deal_id in seen_deals:
            raise DealEvidenceError("invalid or duplicate deal_id")
        seen_deals.add(deal_id)
        board_number = _positive_int(raw.get("board_number"), "board_number", maximum=9999)
        observations = _sequence(
            raw.get("card_observations"),
            "card_observations",
            maximum=MAX_CARD_OBSERVATIONS,
        )
        all_deal_frames: dict[str, dict[str, Any]] = {}
        normalized_observations: list[dict[str, Any]] = []
        hands: dict[str, list[str]] = {seat: [] for seat in SEATS}
        seen_cards: set[str] = set()
        deal_human_cards = 0
        deal_human_seats: set[str] = set()
        for observation in observations:
            if not isinstance(observation, Mapping):
                raise DealEvidenceError("card observation must be an object")
            seat = str(observation.get("seat") or "").upper()
            if seat not in SEATS:
                raise DealEvidenceError("card observation has invalid seat")
            card = _normalise_card(observation.get("card"), seat)
            if card in seen_cards:
                raise DealEvidenceError("card is observed more than once in a deal")
            seen_cards.add(card)
            evidence_class = str(observation.get("evidence_class") or "").upper()
            frames = _frames(observation.get("frames"), verified_shots, "card frames")
            for frame in frames:
                all_deal_frames[frame["evidence_id"]] = frame
            item = {
                "seat": seat,
                "card": card,
                "evidence_class": evidence_class,
                "frames": frames,
                "accepted_as_observation": True,
            }
            if evidence_class == "OBSERVED_MACHINE":
                if producer["kind"] not in {"PROFILED_PIXEL_BACKEND", "MIXED_REVIEW"}:
                    raise DealEvidenceError("machine card is not backed by a pixel producer")
                if len({frame["sha256"] for frame in frames}) < MIN_MACHINE_FRAMES:
                    raise DealEvidenceError("machine card lacks two independent frames")
                confidence, channels = _machine_channels(observation)
                item.update({"confidence": confidence, "channels": channels})
                machine_cards += 1
            elif evidence_class == "HUMAN_VERIFIED":
                if producer["kind"] not in {"HUMAN_REVIEW", "MIXED_REVIEW"}:
                    raise DealEvidenceError("human card is not backed by a review producer")
                human_cards += 1
                deal_human_cards += 1
                deal_human_seats.add(seat)
            else:
                raise DealEvidenceError("unsupported card evidence class")
            hands[seat].append(card)
            normalized_observations.append(item)
        auction = _auction(raw.get("auction"), verified_shots)
        if auction:
            for frame in auction["frame_confirmations"]:
                all_deal_frames[frame["evidence_id"]] = frame
        verification = _verification(raw.get("verification"), list(all_deal_frames.values()))
        if deal_human_cards and verification is None:
            raise DealEvidenceError("human-verified cards require deal verification metadata")
        if verification is not None and not deal_human_seats.issubset(set(verification["verified_seats"])):
            raise DealEvidenceError("human card seat is not listed in verified_seats")
        try:
            canonical = canonicalize_video_deal({"hands": hands}).to_dict()
        except Exception as exc:
            raise DealEvidenceError("deal observations violate the 52-card contract") from exc
        observed_count = sum(len(canonical["hands"][seat]["cards"]) for seat in SEATS)
        total_cards += observed_count
        complete = observed_count == 52
        complete_deals += int(complete)
        evidence_ids = sorted(all_deal_frames)
        if verification is not None:
            reference_sha = verification["reference_frame_sha256"]
            reference_ids = [
                evidence_id
                for evidence_id in evidence_ids
                if all_deal_frames[evidence_id]["sha256"] == reference_sha
            ]
            evidence_ids = reference_ids + [
                evidence_id for evidence_id in evidence_ids if evidence_id not in reference_ids
            ]
        if not observations and auction is None:
            raise DealEvidenceError("deal evidence contains neither cards nor auction")
        deals.append({
            "deal_id": deal_id,
            "board_number": board_number,
            "status": "OBSERVED_COMPLETE" if complete else "OBSERVED_PARTIAL",
            "hands": {seat: canonical["hands"][seat]["cards"] for seat in SEATS},
            "auction": auction,
            "dealer": auction.get("dealer") if auction else raw.get("dealer"),
            "verification": verification,
            "evidence": evidence_ids,
            "card_observations": normalized_observations,
            "deal_evidence": {
                "schema": SCHEMA,
                "result_scope": RESULT_SCOPE,
                "producer": producer,
                "observed_card_count": observed_count,
                "complete_without_derivation": complete,
                "canonical_promotion_allowed": False,
                "production_activation_allowed": False,
            },
            "statement_type": "EVIDENCE_REVIEW",
            "reconstruction_rule": "39_TO_13_ONLY; not applied during evidence intake",
        })

    return {
        "schema": SCHEMA,
        "result_scope": RESULT_SCOPE,
        "canonical_promotion_allowed": False,
        "production_activation_allowed": False,
        "payload_sha256": claimed_digest,
        "producer": producer,
        "deals": deals,
        "summary": {
            "deal_count": len(deals),
            "observed_card_count": total_cards,
            "machine_card_count": machine_cards,
            "human_verified_card_count": human_cards,
            "observed_complete_deal_count": complete_deals,
            "auction_complete_confirmed_count": sum(
                1 for deal in deals if (deal.get("auction") or {}).get("status") == "COMPLETE_CONFIRMED"
            ),
        },
    }


__all__ = [
    "DealEvidenceError",
    "MAX_BUNDLE_BYTES",
    "MIN_MACHINE_CONFIDENCE",
    "MIN_MACHINE_FRAMES",
    "RESULT_SCOPE",
    "SCHEMA",
    "apply_deal_evidence_bundle",
    "evidence_payload_sha256",
]
