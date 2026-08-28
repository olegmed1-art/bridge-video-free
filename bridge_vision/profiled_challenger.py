"""Opt-in, profile-specific card-recognition challenger.

This module implements the deterministic decision boundary learned from
controlled card readers (Dealer4/BridgeSorter) and modern reference retrieval:

* a human-verified interface/teach profile is mandatory;
* every frame is registered to the profile through a bounded homography gate;
* rank and suit are read separately;
* the composed card must agree with an independent reference-card match;
* seat ownership comes only from registered geometry;
* a card becomes an observation only after independent-frame consensus;
* cross-seat disagreement fails closed.

The pixel recognizer is deliberately injected.  This module does not pretend
that a backend exists and is never registered by the default runtime.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bridge_contracts.video_deal import (
    BridgeVideoDealContractError,
    canonicalize_video_deal,
)
from bridge_vision.native_cards import (
    NativeCardDetectorError,
    observations_from_backend,
)

PROFILE_SCHEMA = "bridge-vision-interface-profile/v1"
CHALLENGER_VERSION = "bridge-profiled-card-challenger-v1"
RANKS = tuple("AKQJT98765432")
SUITS = tuple("SHDC")
MAX_TRACKS = 32
MAX_FRAMES_PER_TRACK = 256
MAX_CARD_OBSERVATIONS_PER_FRAME = 104
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")

PixelRecognizer = Callable[[Path, Mapping[str, Any]], Mapping[str, Any]]


class ProfiledChallengerError(ValueError):
    pass


def _finite_float(value: Any, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ProfiledChallengerError(f"invalid {field_name}") from exc
    if not math.isfinite(number):
        raise ProfiledChallengerError(f"invalid {field_name}")
    return number


def _probability(value: Any, field_name: str) -> float:
    number = _finite_float(value, field_name)
    if not 0.0 <= number <= 1.0:
        raise ProfiledChallengerError(f"{field_name} outside [0,1]")
    return number


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ProfiledChallengerError(f"invalid {field_name}")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ProfiledChallengerError(f"invalid {field_name}") from exc
    if number < 1:
        raise ProfiledChallengerError(f"invalid {field_name}")
    return number


def _required_sha(value: Any, field_name: str) -> str:
    text = str(value or "").lower()
    if not _SHA256.fullmatch(text):
        raise ProfiledChallengerError(f"invalid {field_name}")
    return text


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _template_map(raw: Any, *, expected: Sequence[str], field_name: str) -> dict[str, str]:
    if not isinstance(raw, Mapping) or set(raw) != set(expected):
        raise ProfiledChallengerError(f"{field_name} must cover the complete symbol set")
    return {symbol: _required_sha(raw[symbol], f"{field_name}.{symbol}") for symbol in expected}


def _rect(raw: Any, field_name: str) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        raise ProfiledChallengerError(f"{field_name} must be an object")
    result = {key: _finite_float(raw.get(key), f"{field_name}.{key}") for key in ("x", "y", "w", "h")}
    if result["w"] <= 0 or result["h"] <= 0:
        raise ProfiledChallengerError(f"invalid {field_name} size")
    return result


@dataclass(frozen=True)
class InterfaceProfile:
    profile_id: str
    reference_frame_sha256: str
    reference_width: int
    reference_height: int
    table_region: dict[str, float]
    rank_templates: dict[str, str]
    suit_templates: dict[str, str]
    min_registration_inliers: int
    min_registration_inlier_ratio: float
    min_deal_match_inliers: int
    min_deal_match_inlier_ratio: float
    min_rank_confidence: float
    min_suit_confidence: float
    min_reference_confidence: float
    min_card_confidence: float
    min_temporal_observations: int
    seat_dead_zone: float
    template_set_sha256: str

    def recognizer_view(self) -> dict[str, Any]:
        """Return the immutable, decision-relevant view exposed to a backend."""
        return {
            "schema": PROFILE_SCHEMA,
            "profile_id": self.profile_id,
            "reference_frame_sha256": self.reference_frame_sha256,
            "reference_size": {"width": self.reference_width, "height": self.reference_height},
            "table_region": dict(self.table_region),
            "rank_templates": dict(self.rank_templates),
            "suit_templates": dict(self.suit_templates),
            "template_set_sha256": self.template_set_sha256,
        }


def parse_profile(raw: Mapping[str, Any]) -> InterfaceProfile:
    if not isinstance(raw, Mapping):
        raise ProfiledChallengerError("profile must be an object")
    if raw.get("schema") != PROFILE_SCHEMA:
        raise ProfiledChallengerError("unsupported profile schema")
    if raw.get("human_verified") is not True:
        raise ProfiledChallengerError("profile must be human verified")
    profile_id = str(raw.get("profile_id") or "")
    if not _PROFILE_ID.fullmatch(profile_id):
        raise ProfiledChallengerError("invalid profile_id")

    size = raw.get("reference_size")
    if not isinstance(size, Mapping):
        raise ProfiledChallengerError("reference_size must be an object")
    width = _positive_int(size.get("width"), "reference_size.width")
    height = _positive_int(size.get("height"), "reference_size.height")
    table = _rect(raw.get("table_region"), "table_region")
    if table["x"] < 0 or table["y"] < 0 or table["x"] + table["w"] > width or table["y"] + table["h"] > height:
        raise ProfiledChallengerError("table_region leaves reference frame")

    teach = raw.get("teach")
    if not isinstance(teach, Mapping) or teach.get("human_verified") is not True:
        raise ProfiledChallengerError("human-verified teach templates are required")
    ranks = _template_map(teach.get("rank_templates"), expected=RANKS, field_name="teach.rank_templates")
    suits = _template_map(teach.get("suit_templates"), expected=SUITS, field_name="teach.suit_templates")
    expected_template_sha = hashlib.sha256(
        json.dumps({"ranks": ranks, "suits": suits}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    claimed_template_sha = _required_sha(teach.get("template_set_sha256"), "teach.template_set_sha256")
    if claimed_template_sha != expected_template_sha:
        raise ProfiledChallengerError("teach template-set hash mismatch")

    gates = raw.get("gates")
    if not isinstance(gates, Mapping):
        raise ProfiledChallengerError("profile gates must be an object")
    dead_zone = _probability(gates.get("seat_dead_zone"), "gates.seat_dead_zone")
    if dead_zone >= 0.5:
        raise ProfiledChallengerError("seat dead zone must be below 0.5")
    temporal = _positive_int(gates.get("min_temporal_observations"), "gates.min_temporal_observations")
    if temporal < 2:
        raise ProfiledChallengerError("at least two independent frame observations are required")
    if temporal > MAX_FRAMES_PER_TRACK:
        raise ProfiledChallengerError("temporal observation gate exceeds track capacity")

    return InterfaceProfile(
        profile_id=profile_id,
        reference_frame_sha256=_required_sha(raw.get("reference_frame_sha256"), "reference_frame_sha256"),
        reference_width=width,
        reference_height=height,
        table_region=table,
        rank_templates=ranks,
        suit_templates=suits,
        min_registration_inliers=_positive_int(gates.get("min_registration_inliers"), "gates.min_registration_inliers"),
        min_registration_inlier_ratio=_probability(gates.get("min_registration_inlier_ratio"), "gates.min_registration_inlier_ratio"),
        min_deal_match_inliers=_positive_int(gates.get("min_deal_match_inliers"), "gates.min_deal_match_inliers"),
        min_deal_match_inlier_ratio=_probability(gates.get("min_deal_match_inlier_ratio"), "gates.min_deal_match_inlier_ratio"),
        min_rank_confidence=_probability(gates.get("min_rank_confidence"), "gates.min_rank_confidence"),
        min_suit_confidence=_probability(gates.get("min_suit_confidence"), "gates.min_suit_confidence"),
        min_reference_confidence=_probability(gates.get("min_reference_confidence"), "gates.min_reference_confidence"),
        min_card_confidence=_probability(gates.get("min_card_confidence"), "gates.min_card_confidence"),
        min_temporal_observations=temporal,
        seat_dead_zone=dead_zone,
        template_set_sha256=claimed_template_sha,
    )


def load_profile(path: Path) -> InterfaceProfile:
    return parse_profile(json.loads(path.read_text(encoding="utf-8")))


def build_teach_profile(
    *,
    profile_id: str,
    reference_frame_sha256: str,
    reference_size: Mapping[str, Any],
    table_region: Mapping[str, Any],
    rank_templates: Mapping[str, Any],
    suit_templates: Mapping[str, Any],
    gates: Mapping[str, Any],
) -> dict[str, Any]:
    """Build and validate a profile from already human-labelled template hashes.

    Template extraction is a pixel-backend concern.  This helper never guesses
    a label and therefore cannot turn an automatically harvested glyph into a
    trusted Teach template.
    """
    ranks = _template_map(rank_templates, expected=RANKS, field_name="rank_templates")
    suits = _template_map(suit_templates, expected=SUITS, field_name="suit_templates")
    template_sha = hashlib.sha256(
        json.dumps({"ranks": ranks, "suits": suits}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    profile = {
        "schema": PROFILE_SCHEMA,
        "profile_id": profile_id,
        "human_verified": True,
        "reference_frame_sha256": reference_frame_sha256,
        "reference_size": dict(reference_size),
        "table_region": dict(table_region),
        "teach": {
            "human_verified": True,
            "rank_templates": ranks,
            "suit_templates": suits,
            "template_set_sha256": template_sha,
        },
        "gates": dict(gates),
    }
    parse_profile(profile)
    return profile


def _matrix(raw: Any) -> tuple[tuple[float, float, float], ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) != 3:
        raise ProfiledChallengerError("invalid homography")
    rows: list[tuple[float, float, float]] = []
    for row in raw:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or len(row) != 3:
            raise ProfiledChallengerError("invalid homography")
        rows.append(tuple(_finite_float(value, "homography") for value in row))
    a, b, c = rows
    determinant = (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )
    if abs(determinant) < 1e-12:
        raise ProfiledChallengerError("singular homography")
    return tuple(rows)


def _project(matrix: tuple[tuple[float, float, float], ...], x: float, y: float) -> tuple[float, float]:
    denominator = matrix[2][0] * x + matrix[2][1] * y + matrix[2][2]
    if abs(denominator) < 1e-12:
        raise ProfiledChallengerError("homography projects outside finite plane")
    px = (matrix[0][0] * x + matrix[0][1] * y + matrix[0][2]) / denominator
    py = (matrix[1][0] * x + matrix[1][1] * y + matrix[1][2]) / denominator
    if not math.isfinite(px) or not math.isfinite(py):
        raise ProfiledChallengerError("homography projection is not finite")
    return px, py


def _registered_box(raw: Any, matrix: tuple[tuple[float, float, float], ...]) -> dict[str, float]:
    box = _rect(raw, "card.box")
    points = [
        _project(matrix, box["x"], box["y"]),
        _project(matrix, box["x"] + box["w"], box["y"]),
        _project(matrix, box["x"], box["y"] + box["h"]),
        _project(matrix, box["x"] + box["w"], box["y"] + box["h"]),
    ]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return {"x": min(xs), "y": min(ys), "w": max(xs) - min(xs), "h": max(ys) - min(ys)}


def _rank(raw: Any) -> tuple[str, float]:
    if not isinstance(raw, Mapping):
        raise ProfiledChallengerError("rank channel is missing")
    value = str(raw.get("value") or "").upper()
    if value == "10":
        value = "T"
    if value not in RANKS:
        raise ProfiledChallengerError("invalid rank channel value")
    return value, _probability(raw.get("confidence"), "rank confidence")


def _suit(raw: Any) -> tuple[str, float]:
    if not isinstance(raw, Mapping):
        raise ProfiledChallengerError("suit channel is missing")
    value = str(raw.get("value") or "").upper()
    value = {"♠": "S", "♥": "H", "♦": "D", "♣": "C"}.get(value, value)
    if value not in SUITS:
        raise ProfiledChallengerError("invalid suit channel value")
    return value, _probability(raw.get("confidence"), "suit confidence")


def _card(value: Any) -> str:
    deal = canonicalize_video_deal({"hands": {"N": [value]}}).to_dict()
    return deal["hands"]["N"]["cards"][0]


@dataclass
class _Vote:
    confidence: float
    box: dict[str, float]
    channel_evidence: dict[str, Any]


@dataclass
class _Track:
    frame_sha256s: set[str] = field(default_factory=set)
    votes: dict[tuple[str, str], dict[str, _Vote]] = field(default_factory=dict)


class ProfiledCardChallenger:
    """Stateful opt-in detector requiring temporal card+seat consensus."""

    def __init__(self, profile: InterfaceProfile, recognizer: PixelRecognizer):
        if not callable(recognizer):
            raise TypeError("recognizer must be callable")
        self.profile = profile
        self.recognizer = recognizer
        self._tracks: dict[str, _Track] = {}

    def _review(self, frame_sha: str, reason: str, **extra: Any) -> dict[str, Any]:
        return {
            "status": "REVIEW",
            "hands": {},
            "confidence": 0.0,
            "evidence": {
                "detector_version": CHALLENGER_VERSION,
                "profile_id": self.profile.profile_id,
                "frame_sha256": frame_sha,
                "reason": reason,
                "canonical_promotion_allowed": False,
                **extra,
            },
        }

    def _registration(self, raw: Any) -> tuple[tuple[tuple[float, float, float], ...], dict[str, Any]]:
        if not isinstance(raw, Mapping):
            raise ProfiledChallengerError("registration is missing")
        reference_sha = _required_sha(raw.get("reference_frame_sha256"), "registration reference sha256")
        if reference_sha != self.profile.reference_frame_sha256:
            raise ProfiledChallengerError("registration reference does not match profile")
        inliers = _positive_int(raw.get("inliers"), "registration inliers")
        ratio = _probability(raw.get("inlier_ratio"), "registration inlier ratio")
        if inliers < self.profile.min_registration_inliers or ratio < self.profile.min_registration_inlier_ratio:
            raise ProfiledChallengerError("registration below profile gate")
        return _matrix(raw.get("homography")), {"inliers": inliers, "inlier_ratio": ratio, "reference_frame_sha256": reference_sha}

    def _deal_key(self, raw: Any) -> tuple[str, dict[str, Any]]:
        if not isinstance(raw, Mapping):
            raise ProfiledChallengerError("deal identity is missing")
        kind = str(raw.get("kind") or "").upper()
        if kind == "EXPLICIT_BOARD":
            scope = str(raw.get("scope") or "").strip()
            value = str(raw.get("value") or "").strip()
            if not scope or not value or len(scope) > 128 or len(value) > 128:
                raise ProfiledChallengerError("invalid explicit board identity")
            identity = json.dumps({"scope": scope, "value": value}, sort_keys=True, separators=(",", ":"))
            return "explicit:" + hashlib.sha256(identity.encode("utf-8")).hexdigest(), {"kind": kind, "scope": scope, "value": value}
        if kind == "VISUAL_ANCHOR":
            anchor = _required_sha(raw.get("anchor_frame_sha256"), "deal anchor sha256")
            inliers = _positive_int(raw.get("inliers"), "deal match inliers")
            ratio = _probability(raw.get("inlier_ratio"), "deal match inlier ratio")
            if inliers < self.profile.min_deal_match_inliers or ratio < self.profile.min_deal_match_inlier_ratio:
                raise ProfiledChallengerError("deal match below profile gate")
            return f"visual:{anchor}", {"kind": kind, "anchor_frame_sha256": anchor, "inliers": inliers, "inlier_ratio": ratio}
        raise ProfiledChallengerError("unsupported deal identity")

    def __call__(self, frame: Path) -> dict[str, Any]:
        frame_sha = _sha256(frame)
        try:
            raw = self.recognizer(frame, self.profile.recognizer_view())
            if not isinstance(raw, Mapping):
                raise ProfiledChallengerError("recognizer returned non-object")
            if _required_sha(raw.get("frame_sha256"), "recognizer frame sha256") != frame_sha:
                raise ProfiledChallengerError("recognizer frame hash mismatch")
            matrix, registration = self._registration(raw.get("registration"))
            deal_key, deal_identity = self._deal_key(raw.get("deal_identity"))
        except ProfiledChallengerError as exc:
            return self._review(frame_sha, "FRAME_GATE_REJECTED", detail=str(exc))

        raw_cards = raw.get("cards")
        if not isinstance(raw_cards, Sequence) or isinstance(raw_cards, (str, bytes)):
            return self._review(frame_sha, "CARD_ARRAY_INVALID")
        if len(raw_cards) > MAX_CARD_OBSERVATIONS_PER_FRAME:
            return self._review(frame_sha, "CARD_ARRAY_LIMIT_EXCEEDED")

        geometry_cards: list[dict[str, Any]] = []
        channel_rejections: list[dict[str, Any]] = []
        channel_by_index: dict[int, dict[str, Any]] = {}
        for index, observation in enumerate(raw_cards):
            if not isinstance(observation, Mapping):
                channel_rejections.append({"index": index, "reason": "OBSERVATION_INVALID"})
                continue
            try:
                rank, rank_confidence = _rank(observation.get("rank"))
                suit, suit_confidence = _suit(observation.get("suit"))
                reference = observation.get("reference_match")
                if not isinstance(reference, Mapping):
                    raise ProfiledChallengerError("reference channel is missing")
                reference_card = _card(reference.get("card"))
                reference_confidence = _probability(reference.get("confidence"), "reference confidence")
                composed = _card(rank + suit)
                if composed != reference_card:
                    channel_rejections.append({"index": index, "reason": "CHANNEL_DISAGREEMENT", "rank_suit_card": composed, "reference_card": reference_card})
                    continue
                if rank_confidence < self.profile.min_rank_confidence:
                    channel_rejections.append({"index": index, "reason": "LOW_RANK_CONFIDENCE", "confidence": rank_confidence})
                    continue
                if suit_confidence < self.profile.min_suit_confidence:
                    channel_rejections.append({"index": index, "reason": "LOW_SUIT_CONFIDENCE", "confidence": suit_confidence})
                    continue
                if reference_confidence < self.profile.min_reference_confidence:
                    channel_rejections.append({"index": index, "reason": "LOW_REFERENCE_CONFIDENCE", "confidence": reference_confidence})
                    continue
                confidence = min(rank_confidence, suit_confidence, reference_confidence, registration["inlier_ratio"])
                registered_box = _registered_box(observation.get("box"), matrix)
            except (BridgeVideoDealContractError, ProfiledChallengerError, TypeError, ValueError) as exc:
                # Canonical card validation can raise a contract error; it is a
                # rejected pixel observation, never a job-level inferred fact.
                channel_rejections.append({"index": index, "reason": "OBSERVATION_INVALID", "detail": str(exc)})
                continue
            channel_evidence = {
                "rank": {"value": rank, "confidence": rank_confidence},
                "suit": {"value": suit, "confidence": suit_confidence},
                "reference_match": {"card": reference_card, "confidence": reference_confidence},
            }
            geometry_index = len(geometry_cards)
            channel_by_index[geometry_index] = channel_evidence
            geometry_cards.append({"card": composed, "confidence": confidence, "box": registered_box})

        try:
            frame_hands, geometry = observations_from_backend(
                {"table_region": self.profile.table_region, "cards": geometry_cards},
                min_card_confidence=self.profile.min_card_confidence,
                seat_dead_zone=self.profile.seat_dead_zone,
            )
        except (BridgeVideoDealContractError, NativeCardDetectorError, TypeError, ValueError) as exc:
            return self._review(frame_sha, "GEOMETRY_CONFLICT", detail=str(exc), channel_rejections=channel_rejections)

        if deal_key not in self._tracks:
            if len(self._tracks) >= MAX_TRACKS:
                return self._review(frame_sha, "TRACK_LIMIT_REACHED")
            self._tracks[deal_key] = _Track()
        track = self._tracks[deal_key]
        if frame_sha not in track.frame_sha256s and len(track.frame_sha256s) >= MAX_FRAMES_PER_TRACK:
            return self._review(frame_sha, "TRACK_FRAME_LIMIT_REACHED")
        track.frame_sha256s.add(frame_sha)

        accepted_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
        for item in geometry["accepted"]:
            accepted_by_pair[(item["seat"], item["card"])] = item
        for seat, cards in frame_hands.items():
            for card in cards:
                item = accepted_by_pair[(seat, card)]
                channel = channel_by_index[item["index"]]
                vote = _Vote(float(item["confidence"]), dict(geometry_cards[item["index"]]["box"]), channel)
                track.votes.setdefault((seat, card), {})[frame_sha] = vote

        seats_by_card: dict[str, set[str]] = {}
        for (seat, card), votes in track.votes.items():
            if votes:
                seats_by_card.setdefault(card, set()).add(seat)
        conflicts = [
            {"card": card, "seats": sorted(seats), "reason": "TEMPORAL_CROSS_SEAT_DISAGREEMENT"}
            for card, seats in sorted(seats_by_card.items())
            if len(seats) > 1
        ]
        if conflicts:
            return {
                "status": "CONFLICT",
                "hands": {},
                "confidence": 0.0,
                "conflicts": conflicts,
                "evidence": {
                    "detector_version": CHALLENGER_VERSION,
                    "profile_id": self.profile.profile_id,
                    "frame_sha256": frame_sha,
                    "deal_identity": deal_identity,
                    "registration": registration,
                    "canonical_promotion_allowed": False,
                },
            }

        consensus_hands: dict[str, list[str]] = {seat: [] for seat in ("N", "E", "S", "W")}
        consensus_evidence: list[dict[str, Any]] = []
        pending: list[dict[str, Any]] = []
        confidences: list[float] = []
        for (seat, card), votes in sorted(track.votes.items()):
            support = len(votes)
            if support < self.profile.min_temporal_observations:
                pending.append({"seat": seat, "card": card, "independent_frames": support})
                continue
            floor = min(vote.confidence for vote in votes.values())
            consensus_hands[seat].append(card)
            confidences.append(floor)
            consensus_evidence.append({
                "seat": seat,
                "card": card,
                "independent_frames": support,
                "frame_sha256s": sorted(votes),
                "confidence_floor": floor,
                "channels": [votes[key].channel_evidence for key in sorted(votes)],
            })

        consensus_hands = {seat: cards for seat, cards in consensus_hands.items() if cards}
        status = "PASS" if consensus_hands else "PENDING_TEMPORAL_CONSENSUS"
        return {
            "status": status,
            "hands": consensus_hands,
            "confidence": min(confidences, default=0.0),
            "evidence": {
                "detector_version": CHALLENGER_VERSION,
                "profile_id": self.profile.profile_id,
                "template_set_sha256": self.profile.template_set_sha256,
                "frame_sha256": frame_sha,
                "deal_identity": deal_identity,
                "registration": registration,
                "temporal_observations_required": self.profile.min_temporal_observations,
                "consensus": consensus_evidence,
                "pending": pending,
                "channel_rejections": channel_rejections,
                "geometry_rejections": geometry["rejected"],
                "canonical_promotion_allowed": False,
            },
        }


__all__ = [
    "CHALLENGER_VERSION",
    "MAX_CARD_OBSERVATIONS_PER_FRAME",
    "PROFILE_SCHEMA",
    "InterfaceProfile",
    "ProfiledCardChallenger",
    "ProfiledChallengerError",
    "build_teach_profile",
    "load_profile",
    "parse_profile",
]
