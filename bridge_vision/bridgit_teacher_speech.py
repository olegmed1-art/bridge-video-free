"""Teacher-speech evidence for Bridgit card recognition.

This module consumes timestamped ASR segments from the teacher and turns them
into bounded, auditable card claims. It carries short-lived hand context,
combines partial rank/suit mentions, deduplicates overlapping ASR repeats, and
allows an explicit correction to retract the preceding claim.

It never uses mouse cursor position and never creates a visual observation.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

RANKS = tuple("AKQJT98765432")
SUITS = tuple("HCDS")
SEATS = tuple("NESW")
ACTIVE_SEAT_TTL_MS = 30_000
OPEN_CLAIM_TTL_MS = 15_000
_WORD_RE = re.compile(r"[a-zа-я0-9]+", re.IGNORECASE)

_SEAT_WORDS = {
    "n": "N", "north": "N", "север": "N", "севера": "N", "северу": "N", "севером": "N",
    "e": "E", "east": "E", "восток": "E", "востока": "E", "востоку": "E", "востоком": "E",
    "s": "S", "south": "S", "юг": "S", "юга": "S", "югу": "S", "югом": "S",
    "w": "W", "west": "W", "запад": "W", "запада": "W", "западу": "W", "западом": "W",
}
_RANK_WORDS = {
    "a": "A", "ace": "A", "туз": "A", "туза": "A", "тузом": "A", "тузу": "A",
    "k": "K", "king": "K", "король": "K", "короля": "K", "королем": "K", "королю": "K",
    "q": "Q", "queen": "Q", "дама": "Q", "дамы": "Q", "дамой": "Q", "даму": "Q",
    "j": "J", "jack": "J", "валет": "J", "валета": "J", "валетом": "J", "валету": "J",
    "t": "T", "10": "T", "ten": "T", "десятка": "T", "десятку": "T", "десяткой": "T",
    "9": "9", "nine": "9", "девятка": "9", "девятку": "9", "девяткой": "9",
    "8": "8", "eight": "8", "восьмерка": "8", "восьмерку": "8", "восьмеркой": "8",
    "7": "7", "seven": "7", "семерка": "7", "семерку": "7", "семеркой": "7",
    "6": "6", "six": "6", "шестерка": "6", "шестерку": "6", "шестеркой": "6",
    "5": "5", "five": "5", "пятерка": "5", "пятерку": "5", "пятеркой": "5",
    "4": "4", "four": "4", "четверка": "4", "четверку": "4", "четверкой": "4",
    "3": "3", "three": "3", "тройка": "3", "тройку": "3", "тройкой": "3",
    "2": "2", "two": "2", "двойка": "2", "двойку": "2", "двойкой": "2",
}
_SUIT_WORDS = {
    "h": "H", "heart": "H", "hearts": "H", "черва": "H", "червы": "H", "червей": "H",
    "червовая": "H", "червовый": "H", "червовую": "H", "червовой": "H", "черве": "H", "червях": "H",
    "c": "C", "club": "C", "clubs": "C", "треф": "C", "трефа": "C", "трефы": "C",
    "трефовая": "C", "трефовый": "C", "крест": "C", "крести": "C", "крестовая": "C",
    "d": "D", "diamond": "D", "diamonds": "D", "бубен": "D", "бубны": "D", "бубна": "D",
    "бубновая": "D", "бубновый": "D", "бубновую": "D",
    "s": "S", "spade": "S", "spades": "S", "пик": "S", "пики": "S", "пиковая": "S",
    "пиковый": "S", "пиковую": "S", "пиковой": "S", "пиках": "S",
}
_CORRECTION_MARKERS = (
    "нет", "точнее", "вернее", "исправляю", "поправка", "correct", "correction"
)


class TeacherSpeechError(ValueError):
    """Teacher-speech evidence is malformed or ambiguous."""


def _confidence(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise TeacherSpeechError(f"{field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TeacherSpeechError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise TeacherSpeechError(f"{field} is outside [0,1]")
    return number


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TeacherSpeechError(f"{field} must be a non-negative integer")
    return value


def _seat(value: Any, field: str) -> str:
    text = str(value or "").upper()
    if text not in SEATS:
        raise TeacherSpeechError(f"{field} is not a logical seat")
    return text


def _rank(value: Any, field: str) -> str:
    text = str(value or "").upper().replace("10", "T")
    if text not in RANKS:
        raise TeacherSpeechError(f"{field} is not a rank")
    return text


def _suit(value: Any, field: str) -> str:
    text = str(value or "").upper()
    if text not in SUITS:
        raise TeacherSpeechError(f"{field} is not a suit")
    return text


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        raise TeacherSpeechError("speech text must be a string")
    return " ".join(_WORD_RE.findall(value.lower().replace("ё", "е")))


def _correction_tail(text: str) -> tuple[str, bool]:
    best_index = -1
    best_marker = ""
    for marker in _CORRECTION_MARKERS:
        index = text.rfind(marker)
        if index > best_index:
            best_index = index
            best_marker = marker
    if best_index < 0:
        return text, False
    return text[best_index + len(best_marker):], True


def _unique_entity(tokens: Sequence[str], lexicon: Mapping[str, str]) -> str | None:
    found = {lexicon[token] for token in tokens if token in lexicon}
    return next(iter(found)) if len(found) == 1 else None


def _combine(values: Sequence[float]) -> float:
    residual = 1.0
    for value in values:
        residual *= 1.0 - max(0.0, min(0.995, value))
    return min(0.995, 1.0 - residual)


def _dedupe_overlapping(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for event in events:
        duplicate = None
        for index in range(len(result) - 1, -1, -1):
            previous = result[index]
            if event["start_ms"] - previous["end_ms"] > 1_500:
                break
            if (
                event["text"]
                and event["text"] == previous["text"]
                and abs(event["start_ms"] - previous["start_ms"]) <= 1_200
                and abs(event["end_ms"] - previous["end_ms"]) <= 1_200
            ):
                duplicate = index
                break
        if duplicate is None:
            result.append(event)
        elif event["asr_confidence"] > result[duplicate]["asr_confidence"]:
            result[duplicate] = event
    return result


def accumulate_teacher_speech(
    segments: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return normalized ASR events plus accumulated partial/full card claims."""

    if not isinstance(segments, Sequence) or isinstance(segments, (str, bytes)):
        raise TeacherSpeechError("segments must be an array")

    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(segments):
        if not isinstance(raw, Mapping):
            raise TeacherSpeechError(f"segments[{index}] must be an object")
        if str(raw.get("speaker_role") or "").upper() != "TEACHER":
            continue
        event_id = str(raw.get("event_id") or "").strip()
        if not event_id or len(event_id) > 160:
            raise TeacherSpeechError(f"segments[{index}].event_id is invalid")
        start_ms = _nonnegative_int(raw.get("start_ms"), f"{event_id}.start_ms")
        end_ms = _nonnegative_int(raw.get("end_ms"), f"{event_id}.end_ms")
        if end_ms < start_ms:
            raise TeacherSpeechError(f"{event_id}.end_ms precedes start_ms")
        asr_confidence = _confidence(raw.get("asr_confidence"), f"{event_id}.asr_confidence")
        text = _normalize_text(raw.get("text", ""))
        parse_text, detected_correction = _correction_tail(text)
        tokens = _WORD_RE.findall(parse_text)
        seat = (
            _seat(raw["seat_hint"], f"{event_id}.seat_hint")
            if raw.get("seat_hint") is not None
            else _unique_entity(tokens, _SEAT_WORDS)
        )
        rank = (
            _rank(raw["rank_hint"], f"{event_id}.rank_hint")
            if raw.get("rank_hint") is not None
            else _unique_entity(tokens, _RANK_WORDS)
        )
        suit = (
            _suit(raw["suit_hint"], f"{event_id}.suit_hint")
            if raw.get("suit_hint") is not None
            else _unique_entity(tokens, _SUIT_WORDS)
        )
        normalized.append(
            {
                "event_id": event_id,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "text": text,
                "asr_confidence": asr_confidence,
                "explicit_seat": seat,
                "rank": rank,
                "suit": suit,
                "is_correction": bool(raw.get("is_correction", False) or detected_correction),
            }
        )

    normalized.sort(key=lambda item: (item["start_ms"], item["end_ms"], item["event_id"]))
    normalized = _dedupe_overlapping(normalized)

    active_seat: str | None = None
    active_seat_until = -1
    claims: list[dict[str, Any]] = []

    def latest_claim(seat: str | None, now_ms: int) -> dict[str, Any] | None:
        if seat is None:
            return None
        for claim in reversed(claims):
            if (
                claim["seat"] == seat
                and not claim["retracted"]
                and now_ms - claim["last_ms"] <= OPEN_CLAIM_TTL_MS
            ):
                return claim
        return None

    for event in normalized:
        if event["explicit_seat"] is not None:
            active_seat = event["explicit_seat"]
            active_seat_until = event["end_ms"] + ACTIVE_SEAT_TTL_MS

        seat = event["explicit_seat"]
        binding = 1.0
        if seat is None and active_seat is not None and event["start_ms"] <= active_seat_until:
            seat = active_seat
            binding = 0.86

        if event["is_correction"]:
            previous = latest_claim(seat, event["start_ms"])
            if previous is not None:
                previous["retracted"] = True
                previous["retracted_by"] = event["event_id"]

        rank = event["rank"]
        suit = event["suit"]
        if rank is None and suit is None:
            continue

        specificity = 1.0 if rank is not None and suit is not None else 0.72
        effective = min(
            0.995,
            event["asr_confidence"] * specificity * (binding if seat is not None else 0.0),
        )
        support = {
            "event_id": event["event_id"],
            "start_ms": event["start_ms"],
            "end_ms": event["end_ms"],
            "text": event["text"],
            "asr_confidence": event["asr_confidence"],
            "specificity": specificity,
            "context_binding_confidence": binding if seat is not None else 0.0,
            "effective_confidence": effective,
        }

        current = latest_claim(seat, event["start_ms"])
        compatible = bool(
            current is not None
            and (rank is None or current["rank"] in (None, rank))
            and (suit is None or current["suit"] in (None, suit))
        )

        if not compatible:
            claims.append(
                {
                    "claim_id": f"speech-claim-{len(claims) + 1}",
                    "seat": seat,
                    "rank": rank,
                    "suit": suit,
                    "first_ms": event["start_ms"],
                    "last_ms": event["end_ms"],
                    "supports": [support],
                    "retracted": False,
                    "retracted_by": None,
                }
            )
            continue

        if current["rank"] is None:
            current["rank"] = rank
        if current["suit"] is None:
            current["suit"] = suit
        current["last_ms"] = event["end_ms"]
        current["supports"].append(support)

    for claim in claims:
        claim["card"] = (
            claim["rank"] + claim["suit"]
            if claim["rank"] is not None and claim["suit"] is not None
            else None
        )
        claim["complete"] = bool(claim["seat"] is not None and claim["card"] is not None)
        claim["speech_confidence"] = round(
            _combine([item["effective_confidence"] for item in claim["supports"]]), 6
        )
        claim["context_binding_confidence"] = round(
            max((item["context_binding_confidence"] for item in claim["supports"]), default=0.0),
            6,
        )

    return {
        "segments": normalized,
        "claims": claims,
        "mouse_cursor_used": False,
    }
