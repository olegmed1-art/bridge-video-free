"""Validated input loading for the v2 review candidate, without a v3 adapter.

The successor gold builder only validates the same pinned v2 geometry and
distinguishes output I/O faults. Importing it does not import or enable any
r264 rank/video recognizer, temporal union or hidden-hand reconstruction.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

from bridge_vision.bridgit_gold_profile_r264 import (
    BridgitGoldProfileError, build_autonomous_gold_profile,
)
from bridge_vision.gambler_classic_reference import MAX_SPRITE_BYTES
from bridge_vision.gambler_reference_authority import PINNED_GAMBLER_CLASSIC_SPRITE_SHA256


class PrimaryInputUnavailable(RuntimeError):
    """Required validated recognizer material is missing or unusable."""


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
        except (BridgitGoldProfileError, zipfile.BadZipFile) as exc:
            failures.append(type(exc).__name__)
            target.unlink(missing_ok=True)
    raise PrimaryInputUnavailable("CARD_PRIMARY_GOLD_PROFILE_UNAVAILABLE:" + ",".join(failures[:8]))


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
    raise PrimaryInputUnavailable("CARD_PRIMARY_GAMBLER_ASSETS_UNAVAILABLE:" + ",".join(failures[:16]))
