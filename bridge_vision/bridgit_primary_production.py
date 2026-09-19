"""Production wiring for the geometry-first Gambler recognizer.

This adapter is installed by r26 around the existing r25.16 master runner.  It
adds visual primary deals and evidence without changing the inherited ASR,
methodology, persistence, or publication route.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

from bridge_vision.bridgit_gold_profile import build_autonomous_gold_profile
from bridge_vision.bridgit_primary_video import recognize_video_primary
from bridge_vision.gambler_classic_reference import MAX_SPRITE_BYTES
from bridge_vision.gambler_reference_authority import PINNED_GAMBLER_CLASSIC_SPRITE_SHA256

_STATE: dict[str, Any] = {"deals": [], "shots": [], "qc": {"status": "NOT_RUN"}}
_INSTALLED_BASE_IDS: set[int] = set()


def _q(value: object) -> str:
    return str(value).replace("'", "\\'")


def _prepare_profile_seed(base, token: str, work: Path):
    candidates = base.io.search(token, "trashed=false and name='bridgit_gold_v2.zip'")
    failures = []
    for index, item in enumerate(sorted(candidates, key=lambda x: str(x.get("id") or ""))[:16]):
        target = work / f"bridgit-gold-v2-{index}.zip"
        try:
            base.io.download(token, item["id"], target)
            reference, profile_path, payload = build_autonomous_gold_profile(target, work / f"bridgit-gold-v2-{index}")
            return reference, profile_path, payload, item
        except Exception as exc:
            failures.append(type(exc).__name__)
            target.unlink(missing_ok=True)
    raise RuntimeError("CARD_PRIMARY_GOLD_PROFILE_UNAVAILABLE:" + ",".join(failures[:8]))


def _prepare_assets(base, token: str, work: Path) -> Path:
    roots = base.io.search(token, "trashed=false and mimeType='application/vnd.google-apps.folder' and name='classic'")
    expected = {int(k): v for k, v in PINNED_GAMBLER_CLASSIC_SPRITE_SHA256.items()}
    failures = []
    for root_index, root in enumerate(sorted(roots, key=lambda x: str(x.get("id") or ""))[:16]):
        children = base.io.search(token, f"'{_q(root['id'])}' in parents and trashed=false")
        folders = {str(item.get("name")): item for item in children if item.get("mimeType") == "application/vnd.google-apps.folder"}
        if any(str(v) not in folders for v in expected):
            continue
        asset_root = work / f"gambler-classic-{root_index}"
        asset_root.mkdir(parents=True, exist_ok=True)
        ok = True
        for variant, sha in sorted(expected.items()):
            files = base.io.search(token, f"'{_q(folders[str(variant)]['id'])}' in parents and trashed=false and name='all.png'")
            matched = False
            for candidate in files[:8]:
                try:
                    size = int(candidate.get("size") or 0)
                except (TypeError, ValueError):
                    size = 0
                if size <= 0 or size > MAX_SPRITE_BYTES:
                    continue
                temp = asset_root / f".candidate-v{variant}.png"
                try:
                    base.io.download(token, candidate["id"], temp)
                    if base.io.sha(temp) == sha:
                        temp.replace(asset_root / f"all-v{variant}.png")
                        matched = True
                        break
                finally:
                    temp.unlink(missing_ok=True)
            if not matched:
                failures.append(f"v{variant}")
                ok = False
                break
        if ok:
            return asset_root
        for path in asset_root.glob("*"):
            path.unlink(missing_ok=True)
    raise RuntimeError("CARD_PRIMARY_GAMBLER_ASSETS_UNAVAILABLE:" + ",".join(failures[:16]))


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
    except Exception as exc:
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
