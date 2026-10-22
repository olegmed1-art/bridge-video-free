"""r26.5 production adapter for ASR-backed post-visual reconstruction.

This wrapper is additive over the isolated r26.4 recognizer.  It captures the
already-produced local ASR/Zoom transcript, extracts exact attributed teacher
card facts, and runs reconstruction only after the visual recognizer returns.
No source media, transcript, Canon, World, or active production state is
mutated by this module.
"""
from __future__ import annotations

import os
import hashlib
import json
from pathlib import Path
from collections.abc import Mapping
from typing import Any, Callable

from bridge_vision import bridgit_primary_production_r264 as previous
from bridge_vision.teacher_card_reconstruction_r265 import (
    DEFAULT_DEAL_SPEECH_WINDOW_SECONDS,
    extract_teacher_card_declarations,
    reconstruct_deal,
)
from bridge_vision.temporal_deal_recovery import ReviewedRecoveryBatch, ReviewedRecoveryEvidence, accumulate_recovery_frames

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


def install(base, token_func: Callable[[], str], *, recovery_provider=None) -> None:
    base_id = id(base)
    installed = getattr(base, "_r265_recovery_install", None)
    if installed is not None:
        if installed[0] is not recovery_provider:
            raise ValueError("R265_RECOVERY_PROVIDER_RECONFIGURATION_REQUIRES_FRESH_BASE")
        return

    original_obtain = base.obtain_transcript
    original_derive = base.derive_deals_decisions
    original_master = base.master_analysis_payload
    if ASR_CARD_PROMPT not in base.PROMPT:
        base.PROMPT += ASR_CARD_PROMPT
    run_context = {"source_video_sha256": None, "extraction": {}}

    def obtain_transcript(t, parent, name, video, work, dur, job):
        run_context.update(source_video_sha256=None, extraction={})
        if recovery_provider is not None and isinstance(video, (str, Path)):
            try:
                path = Path(video)
                before = path.stat()
                if 0 < before.st_size <= 16 * 1024 ** 3:
                    with path.open("rb") as stream:
                        digest = hashlib.file_digest(stream, "sha256").hexdigest()
                    after = path.stat()
                    if (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns):
                        run_context["source_video_sha256"] = digest
            except OSError:
                pass
        segments, info, warnings = original_obtain(t, parent, name, video, work, dur, job)
        extraction = extract_teacher_card_declarations(segments)
        _STATE["transcript"] = [dict(segment) for segment in segments]
        _STATE["extraction"] = extraction
        run_context["extraction"] = extraction
        return segments, info, warnings

    def derive(episodes, job):
        deals, decisions = original_derive(episodes, job)
        declarations = run_context["extraction"].get("declarations") or []
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
            # A provider is a caller-owned trusted dependency, never request JSON
            # or an environment flag. No source/seat/boundary proof is invented
            # from the first screenshot timestamp or the +/-120 second window.
            temporal = {"status": "CLOSED_UNKNOWN", "reason": "REVIEWED_RECOVERY_PROVIDER_UNAVAILABLE", "scopes": []}
            if recovery_provider is not None and run_context["source_video_sha256"]:
                target = {"source_video_sha256": run_context["source_video_sha256"], "deal": deal, "job": job,
                          "shots": [shot for shot in previous._STATE.get("shots", [])
                                    if str(shot.get("evidence_id")) in evidence_ids]}
                target_sha = hashlib.sha256(json.dumps(target, sort_keys=True, allow_nan=False,
                                                      separators=(",", ":")).encode()).hexdigest()
                batch = recovery_provider(deal, run_context["extraction"], {"target_sha256": target_sha,
                                          "source_video_sha256": run_context["source_video_sha256"]})
                if (not isinstance(batch, ReviewedRecoveryBatch) or not evidence_ids
                        or not isinstance(batch.evidence, ReviewedRecoveryEvidence)
                        or not isinstance(batch.deal_evidence_ids, (list, tuple))
                        or not all(isinstance(v, str) and v for v in batch.deal_evidence_ids)
                        or batch.target_sha256 != target_sha
                        or tuple(sorted(batch.deal_evidence_ids)) != tuple(sorted(evidence_ids))
                        or batch.evidence.provenance()["source_video_sha256"] != run_context["source_video_sha256"]):
                    temporal = {"status": "CLOSED_UNKNOWN", "reason": "RECOVERY_TARGET_BINDING_MISMATCH", "scopes": []}
                else:
                    temporal = accumulate_recovery_frames(batch.frames, evidence=batch.evidence)
                    for scope in temporal["scopes"]:
                        receipts = scope.get("review_receipts", {})
                        if (not receipts or any(p.get("output_deal_sha256") != target_sha
                                or not isinstance(p.get("output_deal_binding_ref"), str)
                                or not p["output_deal_binding_ref"].strip() for p in receipts.values())):
                            temporal = {"status": "CLOSED_UNKNOWN", "reason": "OUTPUT_DEAL_REVIEW_BINDING_MISSING", "scopes": []}
                            break
            deal["temporal_recovery"] = temporal
            verified = None
            if temporal["status"] == "SCOPED_OBSERVATIONS" and len(temporal["scopes"]) == 1:
                verified = temporal["scopes"][0].get("recovery")
                if verified and any(verified["domains"].get(c) != [seat]
                                    for seat, cards in _visual_hands(deal).items() for c in cards):
                    temporal["status"] = "CONFLICT"
                    temporal["reason"] = "CURRENT_VISUAL_HANDS_CONFLICT_WITH_RECOVERY"
                    verified = None
            deal["verified_recovery"] = verified
            counts["deal_count"] += 1
            deal["reconstruction"] = reconstruction
            deal["recognizer"]["asr_text_reconstruction_enabled"] = True
            deal["recognizer"]["visual_cards_checked_against_speech"] = False
            deal["recognizer"]["teacher_card_speech_window_seconds"] = speech_window
            if verified is not None and verified["status"] == "VERIFIED_FULL":
                # Export consumers must use verified_recovery, preserving its
                # domains and provenance. Legacy hands/canonical_deal stay raw.
                counts["full_count"] += 1
            elif temporal["status"] == "CONFLICT" or reconstruction["status"] == "UNRESOLVED_CONFLICT":
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
    base._r265_recovery_install = (recovery_provider,)


__all__ = ["ASR_CARD_PROMPT", "install"]
