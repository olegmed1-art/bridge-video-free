"""Russian transcript + nearest-frame card observations for SHADOW review.

The observer is deliberately conservative.  Speech never creates a card by
itself: an exact affirmative card mention must agree with one unique visual
card+seat candidate in a frame from the same confirmed Bridgit board.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from bridge_contracts.video_deal import SEATS, canonicalize_video_deal

SCHEMA = "bridge-transcript-card-observer/v1"
DEFAULT_MAX_FRAME_GAP_SECONDS = 65.0
MAX_TRANSCRIPT_SEGMENTS = 20_000
MAX_MENTIONS = 2_000
OPPOSITE = {"N": "S", "S": "N", "E": "W", "W": "E"}

_RANK_PATTERNS = {
    "A": r"(?:туз(?:ом|а|у|е)?|тус)",
    "K": r"(?:корол(?:ь|я|ём|ем|ю|е))",
    "Q": r"(?:дам(?:а|ы|у|ой|е))",
    "J": r"(?:валет(?:ом|а|у|е)?)",
    "T": r"(?:десятк(?:а|ой|у|и|е)|10)",
    "9": r"(?:девятк(?:а|ой|у|и|е)|9)",
    "8": r"(?:восьм[её]рк(?:а|ой|у|и|е)|8)",
    "7": r"(?:сем[её]рк(?:а|ой|у|и|е)|7)",
    "6": r"(?:шест[её]рк(?:а|ой|у|и|е)|6)",
    "5": r"(?:пят[её]рк(?:а|ой|у|и|е)|5)",
    "4": r"(?:четв[её]рк(?:а|ой|у|и|е)|4)",
    "3": r"(?:тройк(?:а|ой|у|и|е)|3)",
    "2": r"(?:двойк(?:а|ой|у|и|е)|дв(?:а|е|ух|умя)|2)",
}
_SUIT_PATTERNS = {
    "S": r"(?:пик(?:а|и|у|ой|е|ами)?)",
    "H": r"(?:черв(?:а|и|у|ой|ей|я|е|ами)?)",
    "C": r"(?:треф(?:а|ы|у|ой|е|ами)?|трев(?:а|ы|у|ой|е|ами)?|триф(?:а|ы|у|ой|е|ами)?)",
    "D": r"(?:буб(?:на|ны|ну|ной|не|ен|ей|и|нами))",
}
_RANK = "|".join(f"(?P<r_{rank}>{pattern})" for rank, pattern in _RANK_PATTERNS.items())
_SUIT = "|".join(f"(?P<s_{suit}>{pattern})" for suit, pattern in _SUIT_PATTERNS.items())
_CONNECTOR = r"(?:\s+(?:в|на|по|из))?\s+"
_CARD_PATTERNS = (
    re.compile(rf"(?P<rank>{_RANK}){_CONNECTOR}(?P<suit>{_SUIT})", re.IGNORECASE),
    re.compile(rf"(?P<suit>{_SUIT}){_CONNECTOR}(?P<rank>{_RANK})", re.IGNORECASE),
)
_HYPOTHETICAL = re.compile(r"\b(?:если|например|представь|предполож|может\s+быть|скорее\s+всего)\b", re.IGNORECASE)
_AUCTION = re.compile(
    r"\b(?:торгов\w*|заяв\w*|открыл\w*|контракт\w*|пас(?:овал\w*)?|"
    r"без\s*козыр\w*|показал(?:а)?\s+\d+\s+очк\w*)\b",
    re.IGNORECASE,
)
_FACTUAL = re.compile(
    r"\b(?:вижу|видишь|есть|остал|пош[её]л|пошла|сыграл|сыграла|положил|положила|"
    r"бер[её]м|взял|взяла|ход|верн|выбить|контроль|добрать|постав|у\s+тебя|у\s+партн[её]ра|"
    r"на\s+столе|у\s+врага|показа(?:ть|л|ла))\b",
    re.IGNORECASE,
)
_DIRECT_SEATS = {
    "N": re.compile(r"\b(?:north|nord|норд|север)\b", re.IGNORECASE),
    "E": re.compile(r"\b(?:east|восток)\b", re.IGNORECASE),
    "S": re.compile(r"\b(?:south|зюйд|юг)\b", re.IGNORECASE),
    "W": re.compile(r"\b(?:west|запад)\b", re.IGNORECASE),
}


class TranscriptCardObserverError(ValueError):
    pass


def _matched_code(match: re.Match[str], prefix: str, choices: Mapping[str, str]) -> str:
    found = [code for code in choices if match.groupdict().get(f"{prefix}_{code}")]
    if len(found) != 1:
        raise TranscriptCardObserverError("card token is not uniquely normalized")
    return found[0]


def _negated(text: str, start: int, end: int) -> bool:
    context = text[max(0, start - 28) : min(len(text), end + 12)]
    return bool(re.search(r"\b(?:нет|не|без)\b", context, re.IGNORECASE))


def extract_russian_card_mentions(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Extract exact rank+suit mentions and retain rejected contexts explicitly."""
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        raise TranscriptCardObserverError("transcript rows must be an array")
    if len(rows) > MAX_TRANSCRIPT_SEGMENTS:
        raise TranscriptCardObserverError("transcript segment limit exceeded")
    mentions: list[dict[str, Any]] = []
    for segment, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise TranscriptCardObserverError("transcript row must be an object")
        text = str(row.get("text") or "").strip()
        try:
            start = float(row.get("start"))
            end = float(row.get("end"))
        except (TypeError, ValueError):
            continue
        if not text or not all(math.isfinite(value) for value in (start, end)) or start < 0 or end <= start:
            continue
        seen_spans: set[tuple[int, int]] = set()
        for pattern in _CARD_PATTERNS:
            for match in pattern.finditer(text):
                span = match.span()
                if span in seen_spans:
                    continue
                seen_spans.add(span)
                rank = _matched_code(match, "r", _RANK_PATTERNS)
                suit = _matched_code(match, "s", _SUIT_PATTERNS)
                reason = None
                if _negated(text, *span):
                    reason = "NEGATED_CARD_MENTION"
                elif _HYPOTHETICAL.search(text):
                    reason = "HYPOTHETICAL_CARD_MENTION"
                elif _AUCTION.search(text):
                    reason = "AUCTION_CONTEXT"
                elif not _FACTUAL.search(text):
                    reason = "NON_FACTUAL_CONTEXT"
                mentions.append({
                    "segment": segment,
                    "card": rank + suit,
                    "surface": match.group(0),
                    "text": text,
                    "start": start,
                    "end": end,
                    "speaker_id": str(row.get("speaker") or row.get("speaker_cluster") or "UNMAPPED"),
                    "speaker_role_candidate": str(row.get("speaker_role_candidate") or "UNMAPPED").upper(),
                    "speaker_assignment_confidence": row.get(
                        "speaker_role_confidence",
                        row.get("speaker_confidence"),
                    ),
                    "speaker_identity_verified": row.get("speaker_identity_verified") is True,
                    "speaker_role_verified": row.get("speaker_role_verified") is True,
                    "evidence_locator": f"transcript.jsonl#segment={segment}",
                    "extraction_status": "EXACT_AFFIRMATIVE" if reason is None else "REVIEW",
                    "reason": reason,
                })
                if len(mentions) >= MAX_MENTIONS:
                    return mentions
    return mentions


def _frame_gap(start: float, end: float, timestamp: float) -> float:
    if start <= timestamp <= end:
        return 0.0
    return min(abs(timestamp - start), abs(timestamp - end))


def _evidence_sources(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    sources: list[Mapping[str, Any]] = []
    for key in ("candidates", "diagnostics"):
        raw = record.get(key) or []
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            sources.extend(item for item in raw if isinstance(item, Mapping))
    return sources


def _deal_context(record: Mapping[str, Any]) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    identities: dict[str, dict[str, Any]] = {}
    positions: dict[tuple[tuple[str, str], ...], dict[str, str]] = {}
    confirmed = False
    for source in _evidence_sources(record):
        evidence = source.get("evidence") or {}
        if not isinstance(evidence, Mapping):
            continue
        identity = evidence.get("deal_identity") or {}
        if isinstance(identity, Mapping) and identity.get("kind") == "EXPLICIT_BOARD":
            key = "|".join(str(identity.get(field) or "") for field in ("kind", "scope", "value"))
            identities[key] = dict(identity)
        metadata = evidence.get("board_metadata") or {}
        if isinstance(metadata, Mapping) and metadata.get("status") == "CONFIRMED":
            confirmed = True
            seat_positions = metadata.get("seat_positions")
            if isinstance(seat_positions, Mapping):
                normalized = {str(key).lower(): str(value).upper() for key, value in seat_positions.items()}
                if set(normalized) == {"top", "right", "bottom", "left"} and set(normalized.values()) == set(SEATS):
                    positions[tuple(sorted(normalized.items()))] = normalized
    if len(identities) != 1 or len(positions) != 1 or not confirmed:
        return None, None
    return next(iter(identities.values())), next(iter(positions.values()))


def _visual_candidates(record: Mapping[str, Any], card: str) -> list[dict[str, Any]]:
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    for source in _evidence_sources(record):
        hands = source.get("hands") or {}
        if isinstance(hands, Mapping):
            try:
                normalized = canonicalize_video_deal({"hands": dict(hands)}).to_dict()["hands"]
            except (TypeError, ValueError):
                normalized = {}
            for seat in SEATS:
                if card in (normalized.get(seat) or {}).get("cards", []):
                    candidates[(seat, "ACCEPTED_VISUAL")] = {
                        "seat": seat,
                        "card": card,
                        "source": "ACCEPTED_VISUAL",
                        "confidence": source.get("confidence"),
                        "accepted_as_observation": True,
                    }
        evidence = source.get("evidence") or {}
        if not isinstance(evidence, Mapping):
            continue
        suggestions = evidence.get("layout_suggestions") or []
        if isinstance(suggestions, Sequence) and not isinstance(suggestions, (str, bytes)):
            for suggestion in suggestions:
                if not isinstance(suggestion, Mapping):
                    continue
                if (
                    suggestion.get("provenance_class") == "LAYOUT_SUGGESTION"
                    and suggestion.get("resolution") == "LAYOUT_UNIQUE_SUGGESTION"
                    and suggestion.get("accepted_as_observation") is False
                    and str(suggestion.get("suggested_card") or "").upper() == card
                    and str(suggestion.get("seat") or "").upper() in SEATS
                ):
                    seat = str(suggestion["seat"]).upper()
                    candidates[(seat, "LAYOUT_SUGGESTION")] = {
                        "seat": seat,
                        "card": card,
                        "source": "LAYOUT_SUGGESTION",
                        "layout_index": suggestion.get("index"),
                        "pointer_corroboration": suggestion.get("pointer_corroboration"),
                        "accepted_as_observation": False,
                    }
    return list(candidates.values())


def _linguistic_seat(text: str, mention_surface: str, positions: Mapping[str, str]) -> str | None:
    direct = [seat for seat, pattern in _DIRECT_SEATS.items() if pattern.search(text)]
    if len(direct) == 1:
        return direct[0]
    player = positions["bottom"]
    lowered = text.lower()
    surface_at = lowered.find(mention_surface.lower())
    before = lowered[max(0, surface_at - 55) : surface_at] if surface_at >= 0 else lowered
    around = lowered[max(0, surface_at - 35) : surface_at + len(mention_surface) + 35] if surface_at >= 0 else lowered
    if re.search(r"\b(?:у\s+тебя|ты|тебе|твой|твоя|твоего)\b", around):
        return player
    if re.search(r"\bпартн[её]р(?:а|у|ом)?\b", before) or re.search(r"\bу\s+партн[её]ра\b", around):
        return OPPOSITE[player]
    return None


def observe_transcript_cards(
    rows: Sequence[Mapping[str, Any]],
    frame_records: Sequence[Mapping[str, Any]],
    *,
    max_frame_gap_seconds: float = DEFAULT_MAX_FRAME_GAP_SECONDS,
) -> dict[str, Any]:
    """Match exact mentions to one nearest frame and require visual agreement."""
    if not math.isfinite(max_frame_gap_seconds) or not 0 <= max_frame_gap_seconds <= 120:
        raise ValueError("max frame gap outside [0,120]")
    frames: list[tuple[int, float, Mapping[str, Any]]] = []
    for index, record in enumerate(frame_records):
        if not isinstance(record, Mapping):
            raise TranscriptCardObserverError("frame record must be an object")
        try:
            timestamp = float(record.get("time"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(timestamp):
            frames.append((index, timestamp, record))
    mentions = extract_russian_card_mentions(rows)
    observations: list[dict[str, Any]] = []
    accepted = review = conflicts = 0
    for mention in mentions:
        event = dict(mention)
        if mention["extraction_status"] != "EXACT_AFFIRMATIVE":
            review += 1
            observations.append({**event, "status": "REVIEW", "accepted_as_observation": False})
            continue
        ranked = sorted(
            (_frame_gap(mention["start"], mention["end"], timestamp), index, timestamp, record)
            for index, timestamp, record in frames
        )
        if not ranked or ranked[0][0] > max_frame_gap_seconds:
            review += 1
            observations.append({**event, "status": "REVIEW", "reason": "NO_NEARBY_FRAME", "accepted_as_observation": False})
            continue
        if len(ranked) > 1 and ranked[1][0] == ranked[0][0]:
            review += 1
            observations.append({**event, "status": "REVIEW", "reason": "AMBIGUOUS_NEAREST_FRAME", "accepted_as_observation": False})
            continue
        gap, frame_index, timestamp, record = ranked[0]
        identity, positions = _deal_context(record)
        common = {
            **event,
            "frame_index": frame_index,
            "frame_file": record.get("frame_file"),
            "frame_sha256": record.get("frame_sha256"),
            "frame_time": timestamp,
            "frame_gap_seconds": gap,
            "deal_identity": identity,
        }
        if identity is None or positions is None:
            review += 1
            observations.append({**common, "status": "REVIEW", "reason": "UNCONFIRMED_BOARD_OR_COMPASS", "accepted_as_observation": False})
            continue
        visual = _visual_candidates(record, mention["card"])
        seats = {item["seat"] for item in visual}
        if len(seats) != 1:
            review += 1
            reason = "NO_VISUAL_CARD_CORROBORATION" if not seats else "AMBIGUOUS_VISUAL_SEAT"
            observations.append({**common, "status": "REVIEW", "reason": reason, "visual_candidates": visual, "accepted_as_observation": False})
            continue
        visual_seat = next(iter(seats))
        language_seat = _linguistic_seat(mention["text"], mention["surface"], positions)
        if language_seat is not None and language_seat != visual_seat:
            conflicts += 1
            observations.append({
                **common,
                "status": "CONFLICT",
                "reason": "SPEECH_VISUAL_SEAT_DISAGREEMENT",
                "linguistic_seat": language_seat,
                "visual_seat": visual_seat,
                "visual_candidates": visual,
                "accepted_as_observation": False,
            })
            continue
        sources = {item["source"] for item in visual}
        if "ACCEPTED_VISUAL" in sources:
            resolution = "CORROBORATES_ACCEPTED_VISUAL"
            provenance = "OBSERVED_VISUAL_WITH_SPEECH_CORROBORATION"
        elif sources == {"LAYOUT_SUGGESTION"}:
            pointer = visual[0].get("pointer_corroboration")
            role_verified = (
                mention.get("speaker_role_candidate") == "TEACHER"
                and mention.get("speaker_identity_verified") is True
                and mention.get("speaker_role_verified") is True
            )
            try:
                speaker_confidence = float(mention.get("speaker_assignment_confidence"))
            except (TypeError, ValueError):
                speaker_confidence = 0.0
            pointer_verified = (
                isinstance(pointer, Mapping)
                and pointer.get("source") == "VISUAL_POINTER"
                and pointer.get("accepted_as_card_observation") is False
                and isinstance(pointer.get("evidence_locator"), str)
                and bool(pointer.get("evidence_locator"))
                and isinstance(pointer.get("confidence"), (int, float))
                and float(pointer["confidence"]) >= 0.90
            )
            if not role_verified or speaker_confidence < 0.90 or not pointer_verified:
                review += 1
                observations.append({
                    **common,
                    "status": "REVIEW",
                    "reason": "LAYOUT_PROMOTION_REQUIRES_VERIFIED_TEACHER_AND_POINTER",
                    "visual_seat": visual_seat,
                    "linguistic_seat": language_seat,
                    "visual_candidates": visual,
                    "accepted_as_observation": False,
                    "canonical_promotion_allowed": False,
                })
                continue
            resolution = "OBSERVED_MULTIMODAL"
            provenance = "OBSERVED_MULTIMODAL"
        else:
            review += 1
            observations.append({**common, "status": "REVIEW", "reason": "VISUAL_SOURCE_NOT_PROMOTABLE", "visual_candidates": visual, "accepted_as_observation": False})
            continue
        accepted += 1
        observations.append({
            **common,
            "status": "PASS",
            "resolution": resolution,
            "provenance_class": provenance,
            "seat": visual_seat,
            "linguistic_seat": language_seat,
            "visual_candidates": visual,
            "accepted_as_observation": True,
            "canonical_promotion_allowed": False,
        })
    return {
        "schema": SCHEMA,
        "status": "CONFLICT" if conflicts else "REVIEW" if review else "PASS",
        "mentions": len(mentions),
        "accepted_observations": accepted,
        "review_observations": review,
        "conflict_observations": conflicts,
        "observations": observations,
        "canonical_promotion_allowed": False,
    }


__all__ = [
    "DEFAULT_MAX_FRAME_GAP_SECONDS",
    "SCHEMA",
    "TranscriptCardObserverError",
    "extract_russian_card_mentions",
    "observe_transcript_cards",
]
