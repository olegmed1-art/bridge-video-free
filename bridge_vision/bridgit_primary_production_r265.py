"""r26.5 production adapter for ASR-backed post-visual reconstruction.

This wrapper is additive over the isolated r26.4 recognizer.  It captures the
already-produced local ASR/Zoom transcript, extracts exact attributed teacher
card facts, and runs reconstruction only after the visual recognizer returns.
No source media, transcript, Canon, World, or active production state is
mutated by this module.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, Callable

from bridge_vision import bridgit_primary_production_r264 as previous
from bridge_vision.teacher_card_reconstruction_r265 import (
    DEFAULT_DEAL_SPEECH_WINDOW_SECONDS,
    extract_teacher_card_declarations,
    reconstruct_deal,
)

_STATE: dict[str, Any] = {
    "transcript": [],
    "extraction": {
        "status": "NOT_RUN",
        "declarations": [],
        "rejected": [],
    },
    "reconstruction": {
        "status": "NOT_RUN",
        "deal_count": 0,
        "full_count": 0,
        "partial_count": 0,
        "conflict_count": 0,
    },
}
_INSTALLED_BASE_IDS: set[int] = set()

ASR_CARD_PROMPT = (
    " Карты и руки бриджа: север, восток, юг, запад; "
    "туз, король, дама, валет, десятка, девятка, восьмёрка, семёрка, "
    "шестёрка, пятёрка, четвёрка, тройка, двойка; "
    "пики, черви, бубны, трефы. Не добавляй карту или руку, если она не произнесена."
)


def _visual_hands(deal: Mapping[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for seat in ("N", "E", "S", "W"):
        seat_raw = (deal.get("hands") or {}).get(seat) or {}
        if isinstance(seat_raw, Mapping):
            result[seat] = [
                str(rank).upper() + str(suit).upper()
                for suit, ranks in seat_raw.items()
                for rank in (ranks or [])
            ]
        else:
            result[seat] = [str(card).upper() for card in seat_raw]
    return result


def _deal_timestamps() -> dict[str, float]:
    return {
        str(shot.get("evidence_id")): float(shot["time"])
        for shot in previous._STATE.get("shots") or []
        if shot.get("evidence_id") is not None and shot.get("time") is not None
    }


def install(base, token_func: Callable[[], str]) -> None:
    base_id = id(base)
    if base_id in _INSTALLED_BASE_IDS:
        return

    original_obtain = base.obtain_transcript
    original_derive = base.derive_deals_decisions
    original_master = base.master_analysis_payload
    if ASR_CARD_PROMPT not in base.PROMPT:
        base.PROMPT += ASR_CARD_PROMPT

    def obtain_transcript(t, parent, name, video, work, dur, job):
        segments, info, warnings = original_obtain(t, parent, name, video, work, dur, job)
        extraction = extract_teacher_card_declarations(segments)
        _STATE["transcript"] = [dict(segment) for segment in segments]
        _STATE["extraction"] = extraction
        return segments, info, warnings

    def derive(episodes, job):
        deals, decisions = original_derive(episodes, job)
        declarations = (_STATE.get("extraction") or {}).get("declarations") or []
        timestamps = _deal_timestamps()
        try:
            speech_window = float(
                os.getenv(
                    "BRIDGE_TEACHER_CARD_SPEECH_WINDOW_SECONDS",
                    str(DEFAULT_DEAL_SPEECH_WINDOW_SECONDS),
                )
            )
        except ValueError:
            speech_window = DEFAULT_DEAL_SPEECH_WINDOW_SECONDS
        counts = {"deal_count": 0, "full_count": 0, "partial_count": 0, "conflict_count": 0}
        for deal in deals:
            if not isinstance(deal, dict) or not isinstance(deal.get("recognizer"), Mapping):
                continue
            evidence_ids = [str(value) for value in deal.get("evidence") or []]
            deal_timestamp = next(
                (timestamps[value] for value in evidence_ids if value in timestamps),
                None,
            )
            reconstruction = reconstruct_deal(
                _visual_hands(deal),
                declarations,
                deal_timestamp_seconds=deal_timestamp,
                speech_window_seconds=speech_window,
            )
            counts["deal_count"] += 1
            deal["reconstruction"] = reconstruction
            deal["recognizer"]["asr_text_reconstruction_enabled"] = True
            deal["recognizer"]["visual_cards_checked_against_speech"] = False
            deal["recognizer"]["teacher_card_speech_window_seconds"] = speech_window
            if reconstruction["status"] == "RECONSTRUCTED_FULL":
                deal.setdefault("visual_hands", deal.get("hands"))
                deal["hands"] = reconstruction["hands_by_suit"]
                deal["canonical_deal"] = reconstruction["deal"]
                deal["status"] = "EVIDENCE_FUSED_RECONSTRUCTED_FULL"
                deal["reconstruction_rule"] = (
                    "VISUAL_FIRST; TEACHER_SPEECH_FOR_UNKNOWN_ONLY; "
                    "DECK_COMPLEMENT_AFTER_THREE_COMPLETE_HANDS"
                )
                counts["full_count"] += 1
            elif reconstruction["status"] == "UNRESOLVED_CONFLICT":
                counts["conflict_count"] += 1
            else:
                counts["partial_count"] += 1
        _STATE["reconstruction"] = {"status": "COMPLETED", **counts}
        return deals, decisions

    def master_payload(*args, **kwargs):
        master = original_master(*args, **kwargs)
        technical = master.setdefault("technical_qc", {})
        extraction = _STATE.get("extraction") or {}
        technical["teacher_card_asr_extraction"] = {
            "schema": extraction.get("schema"),
            "status": extraction.get("status"),
            "transcript_segments": extraction.get("transcript_segments", 0),
            "declaration_count": len(extraction.get("declarations") or []),
            "rejected_count": len(extraction.get("rejected") or []),
            "asr_text_consumed": extraction.get("asr_text_consumed", False),
            "visual_cards_checked_against_speech": False,
        }
        technical["deal_reconstruction_r265"] = dict(_STATE.get("reconstruction") or {})
        master.setdefault("principles", {}).update({
            "visual_cards_do_not_require_speech_corroboration": True,
            "teacher_speech_fills_unknown_cards_only": True,
            "speech_and_visual_recognition_are_independent_sources": True,
            "reconstruction_runs_after_recognition": True,
        })
        return master

    base.obtain_transcript = obtain_transcript
    base.derive_deals_decisions = derive
    base.master_analysis_payload = master_payload
    _INSTALLED_BASE_IDS.add(base_id)


__all__ = ["ASR_CARD_PROMPT", "install"]
