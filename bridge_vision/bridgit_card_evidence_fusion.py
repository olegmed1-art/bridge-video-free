"""Fail-closed fusion of visual card candidates, suit colour, and teacher speech.

The visual recognizer remains primary. Teacher speech and theme-calibrated
suit colour may resolve weak visual ambiguity only when the claimed card is
already a compatible visual candidate. Mouse cursor position, deck complement,
turn order, and hidden-hand reconstruction are never used.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from bridge_vision.bridgit_suit_color import SUIT_FAMILY
from bridge_vision.bridgit_teacher_speech import accumulate_teacher_speech

SCHEMA = "bridgit-card-evidence-fusion/v1"
VERSION = "bridgit-card-evidence-fusion-v1"
RESULT_SCOPE = "SHADOW_ONLY"

RANKS = tuple("AKQJT98765432")
SUITS = tuple("HCDS")
SEATS = tuple("NESW")
_CARD_RE = re.compile(r"^(A|K|Q|J|T|9|8|7|6|5|4|3|2)(H|C|D|S)$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

MIN_VISUAL_SUPPORT = 0.20
STRONG_VISUAL_SCORE = 0.88
STRONG_VISUAL_MARGIN = 0.18
MIN_FUSED_SCORE = 0.45
MIN_FUSED_MARGIN = 0.05
MIN_SPEECH_FLIP_CONFIDENCE = 0.78
MIN_COLOR_FLIP_CONFIDENCE = 0.85
SPEECH_BIND_LOOKBACK_MS = 12_000
SPEECH_BIND_LOOKAHEAD_MS = 5_000


class CardEvidenceFusionError(ValueError):
    """Evidence cannot be fused without weakening the shadow boundary."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _seal(value: dict[str, Any]) -> dict[str, Any]:
    value["receipt_sha256"] = hashlib.sha256(
        _canonical_bytes(
            {key: item for key, item in value.items() if key != "receipt_sha256"}
        )
    ).hexdigest()
    return value


def _confidence(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise CardEvidenceFusionError(f"{field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CardEvidenceFusionError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise CardEvidenceFusionError(f"{field} is outside [0,1]")
    return number


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CardEvidenceFusionError(f"{field} must be a non-negative integer")
    return value


def _seat(value: Any, field: str) -> str:
    text = str(value or "").upper()
    if text not in SEATS:
        raise CardEvidenceFusionError(f"{field} is not a logical seat")
    return text


def _card(value: Any, field: str) -> str:
    text = str(value or "").upper().replace("10", "T")
    if not _CARD_RE.fullmatch(text):
        raise CardEvidenceFusionError(f"{field} is not a canonical card")
    return text


def _combine(values: Sequence[float]) -> float:
    residual = 1.0
    for value in values:
        residual *= 1.0 - max(0.0, min(0.995, float(value)))
    return min(0.995, 1.0 - residual)


def _normalize_visual_slots(
    visual_slots: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(visual_slots, Sequence) or isinstance(
        visual_slots, (str, bytes)
    ):
        raise CardEvidenceFusionError("visual_slots must be an array")
    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(visual_slots):
        if not isinstance(raw, Mapping):
            raise CardEvidenceFusionError(f"visual_slots[{index}] must be an object")
        slot_id = str(raw.get("slot_id") or "").strip()
        if not slot_id or len(slot_id) > 160 or slot_id in seen_ids:
            raise CardEvidenceFusionError("slot_id must be non-empty and unique")
        seen_ids.add(slot_id)
        seat = _seat(raw.get("seat"), f"{slot_id}.seat")
        timestamp_ms = _nonnegative_int(
            raw.get("timestamp_ms"), f"{slot_id}.timestamp_ms"
        )
        frame_sha = str(raw.get("frame_sha256") or "")
        if not _SHA256_RE.fullmatch(frame_sha):
            raise CardEvidenceFusionError(f"{slot_id}.frame_sha256 is invalid")
        raw_candidates = raw.get("candidates")
        if (
            not isinstance(raw_candidates, Sequence)
            or isinstance(raw_candidates, (str, bytes))
            or not 1 <= len(raw_candidates) <= 52
        ):
            raise CardEvidenceFusionError(
                f"{slot_id}.candidates must contain 1..52 items"
            )
        candidates: list[dict[str, Any]] = []
        seen_cards: set[str] = set()
        for candidate_index, item in enumerate(raw_candidates):
            if not isinstance(item, Mapping):
                raise CardEvidenceFusionError(
                    f"{slot_id}.candidates[{candidate_index}] must be an object"
                )
            card = _card(
                item.get("card"), f"{slot_id}.candidates[{candidate_index}].card"
            )
            if card in seen_cards:
                raise CardEvidenceFusionError(f"{slot_id} repeats candidate {card}")
            seen_cards.add(card)
            candidates.append(
                {
                    "card": card,
                    "score": _confidence(
                        item.get("score"), f"{slot_id}.{card}.score"
                    ),
                }
            )
        candidates.sort(key=lambda item: (-item["score"], item["card"]))
        result.append(
            {
                "slot_id": slot_id,
                "seat": seat,
                "timestamp_ms": timestamp_ms,
                "frame_sha256": frame_sha,
                "candidates": candidates,
            }
        )
    return result


def _normalize_color_evidence(
    color_evidence: Sequence[Mapping[str, Any]],
    slot_ids: set[str],
) -> dict[str, dict[str, Any]]:
    if not isinstance(color_evidence, Sequence) or isinstance(
        color_evidence, (str, bytes)
    ):
        raise CardEvidenceFusionError("color_evidence must be an array")
    result: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(color_evidence):
        if not isinstance(raw, Mapping):
            raise CardEvidenceFusionError(
                f"color_evidence[{index}] must be an object"
            )
        slot_id = str(raw.get("slot_id") or "").strip()
        if slot_id not in slot_ids or slot_id in result:
            raise CardEvidenceFusionError(
                "color evidence must reference one unique visual slot"
            )
        family = str(raw.get("family") or "").upper()
        if family not in {"RED", "BLACK", "UNKNOWN"}:
            raise CardEvidenceFusionError(f"{slot_id}.color family is invalid")
        result[slot_id] = {
            "family": family,
            "confidence": _confidence(
                raw.get("confidence"), f"{slot_id}.color confidence"
            ),
            "calibrated": raw.get("calibrated") is True,
        }
    return result


def _top_two(
    candidates: Sequence[Mapping[str, Any]], score_key: str
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    ordered = sorted(
        candidates, key=lambda item: (-float(item[score_key]), item["card"])
    )
    return dict(ordered[0]), dict(ordered[1]) if len(ordered) > 1 else None


def _bind_speech(
    claims: Sequence[Mapping[str, Any]],
    slots: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    bound = {slot["slot_id"]: [] for slot in slots}
    conflicts: list[dict[str, Any]] = []
    for raw in claims:
        claim = dict(raw)
        if claim.get("retracted") or claim.get("seat") is None:
            continue
        candidate_slots: list[str] = []
        for slot in slots:
            if slot["seat"] != claim["seat"]:
                continue
            if slot["timestamp_ms"] < claim["first_ms"] - SPEECH_BIND_LOOKBACK_MS:
                continue
            if slot["timestamp_ms"] > claim["last_ms"] + SPEECH_BIND_LOOKAHEAD_MS:
                continue
            compatible = False
            for candidate in slot["candidates"]:
                if candidate["score"] < MIN_VISUAL_SUPPORT:
                    continue
                card = candidate["card"]
                if claim.get("rank") is not None and card[0] != claim["rank"]:
                    continue
                if claim.get("suit") is not None and card[1] != claim["suit"]:
                    continue
                compatible = True
                break
            if compatible:
                candidate_slots.append(slot["slot_id"])
        if len(candidate_slots) == 1:
            bound[candidate_slots[0]].append(claim)
        elif len(candidate_slots) > 1:
            conflicts.append(
                {
                    "kind": "AMBIGUOUS_SPEECH_BINDING",
                    "claim_id": claim["claim_id"],
                    "seat": claim["seat"],
                    "candidate_slots": sorted(candidate_slots),
                }
            )
    return bound, conflicts


def fuse_card_evidence(
    visual_slots: Sequence[Mapping[str, Any]],
    *,
    teacher_speech: Sequence[Mapping[str, Any]] = (),
    color_evidence: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Fuse visual candidates with colour and teacher speech.

    A fused card must already exist among the visual candidates with score
    >= MIN_VISUAL_SUPPORT. Speech never creates an unseen card.
    """

    slots = _normalize_visual_slots(visual_slots)
    colors = _normalize_color_evidence(
        color_evidence, {slot["slot_id"] for slot in slots}
    )
    speech = accumulate_teacher_speech(teacher_speech)
    bound_speech, conflicts = _bind_speech(speech["claims"], slots)

    decisions: list[dict[str, Any]] = []
    for slot in slots:
        baseline_best, baseline_second = _top_two(slot["candidates"], "score")
        baseline_margin = baseline_best["score"] - (
            baseline_second["score"] if baseline_second is not None else 0.0
        )
        strong_visual = (
            baseline_best["score"] >= STRONG_VISUAL_SCORE
            and baseline_margin >= STRONG_VISUAL_MARGIN
        )
        color = colors.get(slot["slot_id"])

        candidates: list[dict[str, Any]] = []
        for raw_candidate in slot["candidates"]:
            candidate = dict(raw_candidate)
            fused_score = candidate["score"]
            color_delta = 0.0
            if (
                color is not None
                and color["calibrated"]
                and color["family"] != "UNKNOWN"
            ):
                expected = SUIT_FAMILY[candidate["card"][1]]
                if expected == color["family"]:
                    color_delta = 0.08 * color["confidence"] * (1.0 - fused_score)
                else:
                    color_delta = -0.20 * color["confidence"]
                fused_score = min(1.0, max(0.0, fused_score + color_delta))
            candidate.update(
                {
                    "color_delta": round(color_delta, 6),
                    "speech_delta": 0.0,
                    "fused_score": fused_score,
                }
            )
            candidates.append(candidate)

        slot_claims = bound_speech[slot["slot_id"]]
        full_speech: dict[str, float] = {}
        rank_speech: dict[str, float] = {}
        suit_speech: dict[str, float] = {}
        for claim in slot_claims:
            confidence = float(claim["speech_confidence"])
            if claim.get("card") is not None:
                card = str(claim["card"])
                full_speech[card] = _combine(
                    [full_speech.get(card, 0.0), confidence]
                )
            elif claim.get("rank") is not None:
                rank = str(claim["rank"])
                rank_speech[rank] = _combine(
                    [rank_speech.get(rank, 0.0), confidence]
                )
            elif claim.get("suit") is not None:
                suit = str(claim["suit"])
                suit_speech[suit] = _combine(
                    [suit_speech.get(suit, 0.0), confidence]
                )

        for candidate in candidates:
            card = candidate["card"]
            speech_delta = 0.0
            if card in full_speech:
                speech_delta += (
                    0.22
                    * full_speech[card]
                    * (1.0 - candidate["fused_score"])
                )
            if card[0] in rank_speech:
                speech_delta += (
                    0.10
                    * rank_speech[card[0]]
                    * (1.0 - candidate["fused_score"])
                )
            if card[1] in suit_speech:
                speech_delta += (
                    0.06
                    * suit_speech[card[1]]
                    * (1.0 - candidate["fused_score"])
                )
            candidate["speech_delta"] = round(speech_delta, 6)
            candidate["fused_score"] = min(
                1.0, max(0.0, candidate["fused_score"] + speech_delta)
            )

        fused_best, fused_second = _top_two(candidates, "fused_score")
        fused_margin = fused_best["fused_score"] - (
            fused_second["fused_score"] if fused_second is not None else 0.0
        )
        slot_conflicts: list[dict[str, Any]] = []

        if strong_visual:
            chosen = baseline_best["card"]
            for speech_card, speech_confidence in full_speech.items():
                if speech_card != chosen and speech_confidence >= 0.75:
                    slot_conflicts.append(
                        {
                            "kind": "SPEECH_VISUAL_CONFLICT",
                            "visual_card": chosen,
                            "speech_card": speech_card,
                            "speech_confidence": round(
                                speech_confidence, 6
                            ),
                        }
                    )
            if (
                color is not None
                and color["calibrated"]
                and color["family"] != "UNKNOWN"
                and SUIT_FAMILY[chosen[1]] != color["family"]
                and color["confidence"] >= MIN_COLOR_FLIP_CONFIDENCE
            ):
                slot_conflicts.append(
                    {
                        "kind": "COLOR_VISUAL_CONFLICT",
                        "visual_card": chosen,
                        "color_family": color["family"],
                        "color_confidence": color["confidence"],
                    }
                )

            used_speech = chosen in full_speech
            used_color = bool(
                color
                and color["calibrated"]
                and color["family"] == SUIT_FAMILY[chosen[1]]
                and color["confidence"] >= 0.50
            )
            if used_speech and used_color:
                provenance = (
                    "VISUAL_PLUS_SUIT_COLOR_PLUS_TEACHER_SPEECH"
                )
            elif used_speech:
                provenance = "VISUAL_PLUS_TEACHER_SPEECH"
            elif used_color:
                provenance = "VISUAL_PLUS_SUIT_COLOR"
            else:
                provenance = "VISUAL"
            status = "ACCEPTED_STRONG_VISUAL"
            confidence = baseline_best["score"]
        else:
            chosen = fused_best["card"]
            original_score = next(
                candidate["score"]
                for candidate in slot["candidates"]
                if candidate["card"] == chosen
            )
            baseline_top3 = {
                candidate["card"] for candidate in slot["candidates"][:3]
            }
            changed = chosen != baseline_best["card"]
            speech_flip_ok = bool(
                chosen in full_speech
                and full_speech[chosen] >= MIN_SPEECH_FLIP_CONFIDENCE
                and chosen in baseline_top3
            )
            color_flip_ok = False
            if (
                color is not None
                and color["calibrated"]
                and color["family"] != "UNKNOWN"
            ):
                color_flip_ok = bool(
                    color["confidence"] >= MIN_COLOR_FLIP_CONFIDENCE
                    and SUIT_FAMILY[chosen[1]] == color["family"]
                    and SUIT_FAMILY[baseline_best["card"][1]]
                    != color["family"]
                    and baseline_best["score"] - original_score <= 0.12
                )

            accepted = bool(
                original_score >= MIN_VISUAL_SUPPORT
                and fused_best["fused_score"] >= MIN_FUSED_SCORE
                and fused_margin >= MIN_FUSED_MARGIN
                and (not changed or speech_flip_ok or color_flip_ok)
            )
            if not accepted:
                chosen = "UNKNOWN"
                provenance = "UNKNOWN"
                status = "UNKNOWN"
                confidence = 0.0
            else:
                used_speech = bool(
                    chosen in full_speech or chosen[0] in rank_speech
                )
                used_color = bool(
                    color
                    and color["calibrated"]
                    and color["family"] == SUIT_FAMILY[chosen[1]]
                    and color["confidence"] >= 0.50
                )
                if used_speech and used_color:
                    provenance = (
                        "VISUAL_PLUS_SUIT_COLOR_PLUS_TEACHER_SPEECH"
                    )
                elif used_speech:
                    provenance = "VISUAL_PLUS_TEACHER_SPEECH"
                elif used_color:
                    provenance = "VISUAL_PLUS_SUIT_COLOR"
                else:
                    provenance = "VISUAL"
                status = "ACCEPTED_FUSED"
                confidence = fused_best["fused_score"]

        decisions.append(
            {
                "slot_id": slot["slot_id"],
                "seat": slot["seat"],
                "card": chosen,
                "status": status,
                "confidence": round(float(confidence), 6),
                "provenance": provenance,
                "baseline_visual_card": baseline_best["card"],
                "baseline_visual_score": round(
                    float(baseline_best["score"]), 6
                ),
                "baseline_visual_margin": round(
                    float(baseline_margin), 6
                ),
                "fused_margin": round(float(fused_margin), 6),
                "speech_claim_ids": [
                    claim["claim_id"] for claim in slot_claims
                ],
                "color_evidence": color,
                "cursor_evidence_used": False,
                "conflicts": slot_conflicts,
            }
        )
        conflicts.extend(
            {"slot_id": slot["slot_id"], **conflict}
            for conflict in slot_conflicts
        )

    owners: dict[str, list[str]] = {}
    seat_counts = {seat: 0 for seat in SEATS}
    for decision in decisions:
        if decision["card"] == "UNKNOWN":
            continue
        owners.setdefault(decision["card"], []).append(decision["slot_id"])
        seat_counts[decision["seat"]] += 1

    for card, slot_ids in sorted(owners.items()):
        if len(slot_ids) > 1:
            conflicts.append(
                {
                    "kind": "DUPLICATE_CARD_DECISION",
                    "card": card,
                    "slot_ids": sorted(slot_ids),
                }
            )
    for seat, count in seat_counts.items():
        if count > 13:
            conflicts.append(
                {
                    "kind": "HAND_OVERFLOW",
                    "seat": seat,
                    "accepted_card_count": count,
                }
            )

    return _seal(
        {
            "schema": SCHEMA,
            "version": VERSION,
            "result_scope": RESULT_SCOPE,
            "status": "NEEDS_REVIEW" if conflicts else "FUSED",
            "decisions": decisions,
            "teacher_speech_claims": speech["claims"],
            "normalized_teacher_segments": speech["segments"],
            "conflicts": conflicts,
            "hidden_hand_inference_used": False,
            "deck_complement_used": False,
            "mouse_cursor_used": False,
            "canonical_promotion_allowed": False,
        }
    )
