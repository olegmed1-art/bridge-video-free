"""Build the pinned human-reviewed Bridgit geometry profile from gold-v2.

The package supplies only the reviewed UI geometry/profile authority. Production
rank pixels are replaced later by the approved original Gambler classic asset.
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from bridge_vision import bridgit_rank_layout as rank_layout

PROFILE_ID = "bridgit.desktop.1920x1010.gold-v2"
GOLD_MANIFEST_SHA256 = "8c3a71cdb3f5125c1cdc31cfd5b4378fb5441393da77d23e64352147130c198d"
GOLD_TEMPLATE_SET_SHA256 = "c763a35745c8817141d573e235c28c14c1c3343644650a4c4a73b9ccb1593d7c"
MAX_GOLD_ZIP_BYTES = 64 * 1024 * 1024
MAX_GOLD_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_GOLD_ENTRIES = 512


class BridgitGoldProfileError(ValueError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _safe_extract(archive_path: Path, destination: Path) -> Path:
    if not archive_path.is_file() or archive_path.stat().st_size > MAX_GOLD_ZIP_BYTES:
        raise BridgitGoldProfileError("gold-v2 package violates input bound")
    total = 0
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        if not 1 <= len(members) <= MAX_GOLD_ENTRIES:
            raise BridgitGoldProfileError("gold-v2 package entry count is invalid")
        for member in members:
            p = PurePosixPath(member.filename)
            if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0] != "bridgit_gold_v2":
                raise BridgitGoldProfileError("gold-v2 package contains unsafe path")
            total += int(member.file_size)
            if total > MAX_GOLD_EXPANDED_BYTES:
                raise BridgitGoldProfileError("gold-v2 expanded bytes exceed bound")
        archive.extractall(destination)
    package = destination / "bridgit_gold_v2"
    if not package.is_dir():
        raise BridgitGoldProfileError("gold-v2 package root is missing")
    return package


def build_autonomous_gold_profile(
    gold_zip: Path, output_dir: Path
) -> tuple[Path, Path, dict[str, Any]]:
    """Return reference path, profile path and parsed profile payload."""
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError as exc:
        raise RuntimeError("OpenCV and NumPy are required") from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    package = _safe_extract(Path(gold_zip), output_dir / "gold")
    manifest_path = package / "manifest.json"
    validation_path = package / "validation.json"
    integrity_path = package / "integrity.json"
    if _sha256(manifest_path) != GOLD_MANIFEST_SHA256:
        raise BridgitGoldProfileError("gold-v2 manifest identity mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    integrity = json.loads(integrity_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "HUMAN_VERIFIED":
        raise BridgitGoldProfileError("gold-v2 is not human verified")
    if validation.get("deck_bijection") != "PASS" or validation.get("two_variants_per_card") != "PASS":
        raise BridgitGoldProfileError("gold-v2 validation did not pass")
    if int(validation.get("rank_separation", {}).get("correct_rank_predictions", 0)) != 104:
        raise BridgitGoldProfileError("gold-v2 rank validation is incomplete")
    if integrity.get("template_set_sha256") != GOLD_TEMPLATE_SET_SHA256:
        raise BridgitGoldProfileError("gold-v2 template set identity mismatch")

    try:
        source = next(item for item in manifest["sources"] if item["source_id"] == "deal_02")
    except (KeyError, StopIteration) as exc:
        raise BridgitGoldProfileError("gold-v2 geometry source is missing") from exc
    width, height = int(source["width"]), int(source["height"])
    if (width, height) != (1920, 1010):
        raise BridgitGoldProfileError("gold-v2 geometry changed")
    canvas = np.full((height, width, 3), 255, dtype=np.uint8)
    slots: list[dict[str, Any]] = []
    for item in manifest.get("templates", []):
        if item.get("source_id") != "deal_02":
            continue
        rank_path = package / str(item.get("rank_path") or "")
        try:
            rank_path.resolve(strict=True).relative_to(package.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise BridgitGoldProfileError("gold-v2 rank glyph path escapes package") from exc
        raw = rank_path.read_bytes()
        glyph = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if glyph is None:
            raise BridgitGoldProfileError("gold-v2 rank glyph could not be decoded")
        x, y = int(item["source_glyph"]["x"]), int(item["source_glyph"]["y"])
        gh, gw = glyph.shape[:2]
        if (gw, gh) != (19, 22) or x < 0 or y < 0 or x + gw > width or y + gh > height:
            raise BridgitGoldProfileError("gold-v2 rank glyph geometry is invalid")
        canvas[y : y + gh, x : x + gw] = cv2.cvtColor(glyph, cv2.COLOR_GRAY2BGR)
        slots.append({"card": str(item["card"]), "x": x, "y": y})
    if len(slots) != 52 or len({item["card"] for item in slots}) != 52:
        raise BridgitGoldProfileError("gold-v2 does not contain one slot per card")

    reference = output_dir / "gold-v2-reference.png"
    if not cv2.imwrite(str(reference), canvas):
        raise BridgitGoldProfileError("cannot write gold-v2 reference")
    reference_sha = _sha256(reference)
    profile: dict[str, Any] = {
        "schema": rank_layout.PROFILE_SCHEMA,
        "human_verified": True,
        "profile_id": PROFILE_ID,
        "reference_frame_sha256": reference_sha,
        "verification": {
            "method": "AUTONOMOUS_HASH_GEOMETRY_V1",
            "reference_frame_sha256": reference_sha,
            "manifest_sha256": GOLD_MANIFEST_SHA256,
            "template_set_sha256": GOLD_TEMPLATE_SET_SHA256,
            "deck_bijection": "PASS",
            "rank_separation_predictions": 104,
        },
        "frame_size": {"width": width, "height": height},
        "ordering": {"suits": list(rank_layout.SUITS), "ranks": list(rank_layout.RANKS)},
        "template_slots": slots,
        "geometry": {
            "anchors": {
                "N": {"H": {"x": 355, "y": 28}, "C": {"x": 495, "y": 28}, "D": {"x": 709, "y": 28}, "S": {"x": 920, "y": 28}},
                "E": {suit: {"x": 1320, "y": y} for suit, y in zip("HCDS", (312, 356, 400, 444))},
                "S": {"H": {"x": 353, "y": 813}, "C": {"x": 595, "y": 813}, "D": {"x": 804, "y": 813}, "S": {"x": 953, "y": 813}},
                "W": {suit: {"x": 19, "y": y} for suit, y in zip("HCDS", (312, 356, 400, 444))},
            },
            "horizontal_search": {
                "N": {"x_min": 300, "x_max": 1050, "y": 28},
                "S": {"x_min": 300, "x_max": 1050, "y": 813},
            },
            "vertical_search": {
                "W": {"x_min": 10, "x_max": 145, "edge_x": 19},
                "E": {"x_min": 1180, "x_max": 1335, "edge_x": 1320},
            },
            "interface_anchor": None,
        },
        "gates": {
            "glyph_width": 19,
            "glyph_height": 22,
            "local_registration_px": 2,
            "binary_threshold": 200,
            "min_template_score": 0.40,
            "min_peak_score": 0.70,
            "min_peak_prominence": 0.03,
            "min_rank_ink_fraction": 0.12,
            "min_assignment_margin": 0.06,
            "min_independent_frames": 2,
        },
        "gold": {
            "manifest_sha256": GOLD_MANIFEST_SHA256,
            "template_set_sha256": GOLD_TEMPLATE_SET_SHA256,
            "bridge_logic_weighting": False,
        },
    }
    profile["profile_sha256"] = _canonical_hash(profile)
    rank_layout.parse_profile(profile)
    profile_path = output_dir / "gold-v2-profile.json"
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return reference, profile_path, profile


__all__ = [
    "BridgitGoldProfileError",
    "GOLD_MANIFEST_SHA256",
    "GOLD_TEMPLATE_SET_SHA256",
    "PROFILE_ID",
    "build_autonomous_gold_profile",
]
