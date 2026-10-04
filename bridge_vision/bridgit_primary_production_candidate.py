"""Review-only wiring for the observation-guard v2 candidate.

No default runtime imports or installs this candidate adapter. It
adds visual primary deals and evidence without changing the inherited ASR,
methodology, persistence, or publication route.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import zipfile
from bridge_vision.bridgit_gold_profile_r264 import BridgitGoldProfileError
from bridge_vision.bridgit_primary_production_r264 import (
    _prepare_profile_seed, _prepare_assets, PrimaryInputUnavailable,
)
from bridge_vision.bridgit_primary_video_candidate import recognize_video_primary
from bridge_vision.gambler_classic_reference import GamblerClassicReferenceError

_STATE: dict[str, Any] = {"deals": [], "shots": [], "qc": {"status": "NOT_RUN"}}
_INSTALLED_BASE_IDS: set[int] = set()


def _run_primary(base, token: str, video: Path, work: Path, job: str):
    try:
        reference, profile_path, profile, gold = _prepare_profile_seed(base, token, work)
        assets = _prepare_assets(base, token, work)
        result = recognize_video_primary(
            video, reference_frame=reference, profile_path=profile_path,
            gambler_asset_root=assets, output_dir=work / "primary-card-recognizer",
            verified_card_width_px=109.0, verified_card_height_px=147.0,
            scan_ms=1000, attempt_gap_ms=15000, max_deals=64,
        )
    except (PrimaryInputUnavailable, BridgitGoldProfileError, zipfile.BadZipFile, GamblerClassicReferenceError) as exc:
        return [], [], {"status": "UNAVAILABLE", "reason": type(exc).__name__, "detail": str(exc)[:160]}
    deals, shots = [], []
    for item in result.get("deals") or []:
        evidence_id = base.stable_entity_id("frame", job, f"primary-card|{item['layout_sha256']}|{item['timestamp_ms']}")
        shots.append({"evidence_id": evidence_id, "time": item["timestamp_ms"] / 1000.0, "path": str(item["screenshot"]), "sha256": item["screenshot_sha256"], "source": "primary_card_recognizer"})
        deals.append({
            "deal_id": base.stable_entity_id("deal", job, "primary-card|" + item["layout_sha256"]),
            "episode_id": None, "status": "VISUAL_PRIMARY_RECOGNIZED", "hands": item["hands"],
            "auction": None, "contract": None, "declarer": None, "opening_lead": None, "result": None,
            "reconstruction_rule": "VISUAL_ONLY; NO_DECK_COMPLEMENT", "statement_type": "FACT",
            "evidence": [evidence_id], "recognizer": {
                "version": result.get("version"), "backend_status": item.get("backend_status"),
                "minimum_assigned_score": item.get("minimum_assigned_score"), "median_assigned_score": item.get("median_assigned_score"),
                "gambler_variant": result.get("gambler_variant"), "gambler_sprite_sha256": result.get("gambler_sprite_sha256"),
                "template_card_size": result.get("template_card_size"), "template_resampled": result.get("template_resampled"),
                "canonical_promotion_allowed": False,
            },
        })
    qc = {k: result.get(k) for k in ("version", "status", "source_size", "gambler_variant", "gambler_sprite_sha256", "template_card_size", "template_resampled", "scan_ms", "attempt_gap_ms", "event_counts", "rejections")}
    qc.update({"deal_count": len(deals), "gold_profile_id": profile.get("profile_id"), "gold_drive_id": gold.get("id")})
    return deals, shots, qc


def install(base, token_func: Callable[[], str]) -> None:
    base_id = id(base)
    if base_id in _INSTALLED_BASE_IDS:
        return
    original_visual = base.visual
    original_derive = base.derive_deals_decisions
    original_master = base.master_analysis_payload

    def visual(video, work, dur, critical, job):
        p1, p2, shots = original_visual(video, work, dur, critical, job)
        deals, primary_shots, qc = _run_primary(base, token_func(), Path(video), Path(work), job)
        _STATE.update({"deals": deals, "shots": primary_shots, "qc": qc})
        shots.extend(primary_shots)
        return p1, p2, shots

    def derive(episodes, job):
        deals, decisions = original_derive(episodes, job)
        deals.extend(_STATE.get("deals") or [])
        return deals, decisions

    def master_payload(*args, **kwargs):
        master = original_master(*args, **kwargs)
        master.setdefault("technical_qc", {})["card_recognizer"] = dict(_STATE.get("qc") or {})
        master.setdefault("content_quality", {})["primary_visual_deals"] = len(_STATE.get("deals") or [])
        return master

    base.visual = visual
    base.derive_deals_decisions = derive
    base.master_analysis_payload = master_payload
    _INSTALLED_BASE_IDS.add(base_id)


__all__ = ["install"]
