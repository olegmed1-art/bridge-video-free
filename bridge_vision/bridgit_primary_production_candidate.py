"""Review-only wiring for the observation-guard v2 candidate.

No default runtime imports or installs this candidate adapter. It
adds visual primary deals and evidence without changing the inherited ASR,
methodology, persistence, or publication route.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import copy
import hashlib
import json
import os
import sys
import zipfile
from threading import Lock
from bridge_vision.auction_observer import speech_mentions
from bridge_vision.bridgit_gold_profile_r264 import BridgitGoldProfileError
from bridge_vision.bridgit_primary_inputs_candidate import (
    _prepare_profile_seed, _prepare_assets, PrimaryInputUnavailable,
)
from bridge_vision.bridgit_primary_video_candidate import PrimaryVideoInputError, recognize_video_primary
from bridge_vision.gambler_classic_reference import GamblerClassicReferenceError

# Native geometry temporarily patches shared rank helpers; serialize candidate passes.
PRIMARY_PASS_LOCK = Lock()
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



def _prepare_auction_export(auction_result, reference):
    """Replace ephemeral paths by attachments in the existing final PDF."""
    from bridge_vision.auction_observer import pixel_sha
    from bridge_vision import bridgit_rank_layout as rank
    exported = copy.deepcopy(auction_result)
    files, paths = {}, {}
    for auction in exported.get("auctions", []):
        for obs in auction.get("observations", []):
            path, sha = obs["frame_path"], obs["frame_sha256"]
            name = "auction-frame-" + sha + ".png"
            files.setdefault(name, {"name": name, "path": path, "sha256": sha, "kind": "FRAME"})
            paths[path] = {"type": "PDF_ATTACHMENT", "name": name, "sha256": sha}

    profile = exported.pop("calibration_profile", None)
    if profile is not None:
        payload = json.dumps(profile, sort_keys=True).encode()
        profile_sha = hashlib.sha256(payload).hexdigest()
        if len(payload) > 1024 * 1024 or profile_sha != exported["profile_sha256"]:
            raise CandidateInstallationError("auction calibration profile changed")
        name = "auction-profile-" + profile_sha + ".json"
        files[name] = {"name": name, "payload": payload, "sha256": profile_sha, "kind": "PROFILE"}
        raw = Path(reference).read_bytes()
        if len(raw) > 16 * 1024 * 1024:
            raise CandidateInstallationError("auction calibration reference too large")
        cv2, np = rank._pixel_runtime()
        decoded = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if decoded is None or pixel_sha(decoded) != exported["reference_pixel_sha256"]:
            raise CandidateInstallationError("auction calibration reference changed")
        sha = hashlib.sha256(raw).hexdigest()
        ref_name = "auction-reference-" + sha + ".png"
        files[ref_name] = {"name": ref_name, "path": str(reference), "sha256": sha, "kind": "REFERENCE"}
        exported["calibration_evidence"] = {
            "profile": {"type": "PDF_ATTACHMENT", "name": name, "sha256": profile_sha},
            "reference": {"type": "PDF_ATTACHMENT", "name": ref_name, "sha256": sha},
        }

    def replace_paths(value):
        if isinstance(value, dict):
            if "frame_path" in value:
                path = value.pop("frame_path")
                if path not in paths:
                    raise CandidateInstallationError("unregistered auction evidence")
                value["evidence_locator"] = paths[path]
            for child in value.values():
                replace_paths(child)
        elif isinstance(value, list):
            for child in value:
                replace_paths(child)
    replace_paths(exported)
    return exported, list(files.values())


def _embed_auction_evidence(pdf, master, files, original_embed):
    """Return only after all named attachments are present and hash-verifiable."""
    import fitz
    payloads = []
    total = 0
    for item in files:
        payload = item["payload"] if "payload" in item else Path(item["path"]).read_bytes()
        total += len(payload)
        if total > 145 * 1024 * 1024 or hashlib.sha256(payload).hexdigest() != item["sha256"]:
            raise CandidateInstallationError("auction evidence changed or exceeded export budget")
        payloads.append((item, payload))
    digest = original_embed(pdf, master)
    if payloads:
        with fitz.open(pdf) as document:
            existing = set(document.embfile_names())
            for item, payload in payloads:
                if item["name"] in existing:
                    raise CandidateInstallationError("auction attachment already exists")
                document.embfile_add(item["name"], payload, filename=item["name"])
            document.saveIncr()
        with fitz.open(pdf) as document:
            for item, _ in payloads:
                if hashlib.sha256(document.embfile_get(item["name"])).hexdigest() != item["sha256"]:
                    raise CandidateInstallationError("auction PDF attachment verification failed")
    return digest


def _run_primary(base, token: str, video: Path, work: Path, job: str):
    try:
        reference, profile_path, profile, gold = _prepare_profile_seed(base, token, work)
        assets = _prepare_assets(base, token, work)
        result = recognize_video_primary(
            video, reference_frame=reference, profile_path=profile_path,
            gambler_asset_root=assets, output_dir=work / "primary-card-recognizer",
            verified_card_width_px=109.0, verified_card_height_px=147.0,
            scan_ms=1000, attempt_gap_ms=15000, max_deals=64,
            auction_profile_path=Path(os.environ["BRIDGE_AUCTION_PROFILE_PATH"]) if os.environ.get("BRIDGE_AUCTION_PROFILE_PATH") else None,
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
    auction_result = result.get("auction_recognition") or {"status": "UNAVAILABLE", "auctions": []}
    # Temporal overlap is a candidate relation, not proved board identity.
    # Do not promote the auction/contract/declarer into a hand record.
    for deal, item in zip(deals, result.get("deals") or []):
        matching = [a for a in auction_result.get("auctions", [])
                    if a["start_ms"] <= item["timestamp_ms"] <= a["end_ms"]]
        deal["auction_candidates"] = [
            {"board_occurrence_id": a["board_occurrence_id"],
             "link_status": "UNVERIFIED_TEMPORAL_OVERLAP"} for a in matching
        ]
    qc = {k: result.get(k) for k in ("version", "status", "source_size", "gambler_variant", "gambler_sprite_sha256", "template_card_size", "template_resampled", "scan_ms", "attempt_gap_ms", "max_pending_pairs", "event_counts", "rejections")}
    auction_result, export_files = _prepare_auction_export(auction_result, reference)
    qc["auction_recognition"] = auction_result
    qc["_auction_export_files"] = export_files
    qc.update({"deal_count": len(deals), "gold_profile_id": profile.get("profile_id"), "gold_drive_id": gold.get("id")})
    return deals, shots, qc


def install(base, token_func: Callable[[], str]) -> None:
    installed = getattr(base, STATE_ATTRIBUTE, None)
    if installed is not None:
        if (not isinstance(installed, dict) or installed.get("token_func") is not token_func
                or installed.get("hooks") != (base.visual, base.derive_deals_decisions, base.master_analysis_payload)
                or installed.get("embed_hook") is not getattr(base, "embed_master", None)):
            raise CandidateInstallationError("candidate installation was replaced")
        return
    ensure_installable(base)
    original_visual = base.visual
    original_derive = base.derive_deals_decisions
    original_master = base.master_analysis_payload
    original_embed = getattr(base, "embed_master", None)
    # State belongs to this base installation, never another process/job/base.
    state = {"job": None, "deals": [], "shots": [], "attachments": [], "qc": {"status": "NOT_RUN"}}

    def visual(video, work, dur, critical, job):
        if not PRIMARY_PASS_LOCK.acquire(blocking=False):
            raise CandidateInstallationError("concurrent candidate visual jobs require separate processes")
        state.update({"job": job, "deals": [], "shots": [], "attachments": [], "qc": {"status": "RUNNING", "job_id": job}})
        try:
            p1, p2, shots = original_visual(video, work, dur, critical, job)
            deals, primary_shots, qc = _run_primary(base, token_func(), Path(video), Path(work), job)
            qc = dict(qc)
            attachments = qc.pop("_auction_export_files", [])
            if attachments and not callable(original_embed):
                raise CandidateInstallationError("auction evidence requires the final PDF embedding hook")
            state.update({"deals": deals, "shots": primary_shots, "attachments": attachments,
                          "qc": dict(qc, job_id=job)})
            return p1, p2, [*shots, *primary_shots]
        except Exception:
            # State cleanup only: the original error is still propagated.
            state.update({"deals": [], "shots": [], "attachments": [], "qc": {"status": "FAILED", "job_id": job}})
            raise
        finally:
            PRIMARY_PASS_LOCK.release()

    def derive(episodes, job):
        deals, decisions = original_derive(episodes, job)
        primary_deals = state["deals"] if state["job"] == job else []
        # Copy the inherited list so repeated derivation cannot append twice.
        return [*deals, *primary_deals], decisions

    def master_payload(*args, **kwargs):
        master = original_master(*args, **kwargs)
        job = kwargs.get("job_id", master.get("job_id"))
        current = job is not None and state["job"] == job
        qc = dict(state["qc"]) if current else {"status": "NOT_RUN", "job_id": job}
        master.setdefault("technical_qc", {})["card_recognizer"] = qc
        master["auction_observations"] = (qc.get("auction_recognition") or {}).get("auctions", [])
        master["auction_evidence_manifest"] = [
            {k: item[k] for k in ("name", "sha256", "kind")}
            for item in state["attachments"]
        ] if current else []
        master["auction_speech_mentions"] = speech_mentions(kwargs.get("transcript") or []) if current else []
        master.setdefault("content_quality", {})["primary_visual_deals"] = len(state["deals"]) if current else 0
        return master

    def embed_master(pdf, master):
        manifest = master.get("auction_evidence_manifest") or []
        same_job = state["job"] is not None and master.get("job_id") == state["job"]
        if manifest or (same_job and state["attachments"]):
            expected = [{k: item[k] for k in ("name", "sha256", "kind")} for item in state["attachments"]]
            if not same_job or manifest != expected:
                raise CandidateInstallationError("auction export belongs to another job or state")
            return _embed_auction_evidence(pdf, master, state["attachments"], original_embed)
        return original_embed(pdf, master)

    if callable(original_embed):
        base.embed_master = embed_master
    hooks = (visual, derive, master_payload)
    base.visual, base.derive_deals_decisions, base.master_analysis_payload = hooks
    setattr(base, STATE_ATTRIBUTE, {"token_func": token_func, "hooks": hooks, "embed_hook": getattr(base, "embed_master", None)})


__all__ = ["CandidateInstallationError", "ensure_installable", "install"]
