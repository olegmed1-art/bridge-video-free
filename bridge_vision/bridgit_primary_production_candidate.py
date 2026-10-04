"""Review-only wiring for the observation-guard v2 candidate.

No default runtime imports or installs this candidate adapter. It
adds visual primary deals and evidence without changing the inherited ASR,
methodology, persistence, or publication route.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import sys
import zipfile
from threading import Lock
from bridge_vision.bridgit_gold_profile_r264 import BridgitGoldProfileError
from bridge_vision.bridgit_primary_inputs_candidate import (
    _prepare_profile_seed, _prepare_assets, PrimaryInputUnavailable,
)
from bridge_vision.bridgit_primary_video_candidate import PrimaryVideoInputError, recognize_video_primary
from bridge_vision.gambler_classic_reference import GamblerClassicReferenceError

STATE_ATTRIBUTE = "_bridge_v2_candidate_installation"
HISTORICAL_ADAPTERS = (
    "bridge_vision.bridgit_primary_production",
    "bridge_vision.bridgit_primary_production_r264",
    "bridge_vision.bridgit_primary_production_r265",
)


class CandidateInstallationError(RuntimeError):
    """Refuse mixed or replaced primary recognizer installations."""


def ensure_installable(base) -> None:
    if getattr(base, STATE_ATTRIBUTE, None) is not None:
        raise CandidateInstallationError("candidate is already installed")
    for name in HISTORICAL_ADAPTERS:
        module = sys.modules.get(name)
        if module is not None and id(base) in getattr(module, "_INSTALLED_BASE_IDS", ()):
            raise CandidateInstallationError("another primary adapter is already installed")


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
    except (PrimaryInputUnavailable, PrimaryVideoInputError, BridgitGoldProfileError, zipfile.BadZipFile, GamblerClassicReferenceError) as exc:
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
    qc = {k: result.get(k) for k in ("version", "status", "source_size", "gambler_variant", "gambler_sprite_sha256", "template_card_size", "template_resampled", "scan_ms", "attempt_gap_ms", "max_pending_pairs", "event_counts", "rejections")}
    qc.update({"deal_count": len(deals), "gold_profile_id": profile.get("profile_id"), "gold_drive_id": gold.get("id")})
    return deals, shots, qc


def install(base, token_func: Callable[[], str]) -> None:
    installed = getattr(base, STATE_ATTRIBUTE, None)
    if installed is not None:
        if (not isinstance(installed, dict) or installed.get("token_func") is not token_func
                or installed.get("hooks") != (base.visual, base.derive_deals_decisions, base.master_analysis_payload)):
            raise CandidateInstallationError("candidate installation was replaced")
        return
    ensure_installable(base)
    original_visual = base.visual
    original_derive = base.derive_deals_decisions
    original_master = base.master_analysis_payload
    # State belongs to this base installation, never another process/job/base.
    state = {"job": None, "deals": [], "shots": [], "qc": {"status": "NOT_RUN"}}
    lock = Lock()

    def visual(video, work, dur, critical, job):
        if not lock.acquire(blocking=False):
            raise CandidateInstallationError("concurrent visual jobs require separate installations")
        state.update({"job": job, "deals": [], "shots": [], "qc": {"status": "RUNNING", "job_id": job}})
        try:
            p1, p2, shots = original_visual(video, work, dur, critical, job)
            deals, primary_shots, qc = _run_primary(base, token_func(), Path(video), Path(work), job)
            state.update({"deals": deals, "shots": primary_shots, "qc": dict(qc, job_id=job)})
            return p1, p2, [*shots, *primary_shots]
        except Exception:
            # State cleanup only: the original error is still propagated.
            state.update({"deals": [], "shots": [], "qc": {"status": "FAILED", "job_id": job}})
            raise
        finally:
            lock.release()

    def derive(episodes, job):
        deals, decisions = original_derive(episodes, job)
        primary_deals = state["deals"] if state["job"] == job else []
        # Copy the inherited list so repeated derivation cannot append twice.
        return [*deals, *primary_deals], decisions

    def master_payload(*args, **kwargs):
        master = original_master(*args, **kwargs)
        master.setdefault("technical_qc", {})["card_recognizer"] = dict(state["qc"])
        master.setdefault("content_quality", {})["primary_visual_deals"] = len(state["deals"])
        return master

    hooks = (visual, derive, master_payload)
    base.visual, base.derive_deals_decisions, base.master_analysis_payload = hooks
    setattr(base, STATE_ATTRIBUTE, {"token_func": token_func, "hooks": hooks})


__all__ = ["CandidateInstallationError", "ensure_installable", "install"]
