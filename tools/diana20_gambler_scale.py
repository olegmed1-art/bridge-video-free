"""Pixel scale selection for the pinned Diana20 native-corner worker.

Only scale evidence enters selection. The existing observer requires native
pixels, so unavailable or nonnative sizes abstain instead of resizing cards.
No card identity, score, desired count, or previous frame enters this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from bridge_vision.gambler_card_scale import MAX_BOUNDARY_ERROR_PX, measure_card_scale
from bridge_vision.gambler_classic_reference import MAX_SPRITE_BYTES, VARIANT_CARD_SIZE, validate_sprite_bytes
from bridge_vision.gambler_reference_authority import pinned_sprite_sha256, variant_for_pinned_sprite_sha256

VERSION = "diana20-pixel-native-scale-v1"
PROFILE = SimpleNamespace(width=1920, height=1010,
                          horizontal_search={"N": (300, 1080, 25), "S": (300, 1080, 808)})


@dataclass(frozen=True)
class Selection:
    sprite: object | None
    evidence: dict


def load_native_sprite(path, *, expected_variant=None):
    """Bounded binary read with the original immutable asset allowlist.

    Keep platform I/O inside this application adapter; the frozen legacy
    reference loader and the native observer's pixel decoder stay unchanged.
    """
    import hashlib
    import os
    import stat

    path = Path(path)
    if any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())
           for p in (path, *path.parents)):
        raise ValueError("DIANA20_SPRITE_LINKED")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        meta = os.fstat(stream.fileno())
        if not stat.S_ISREG(meta.st_mode) or not 0 < meta.st_size <= MAX_SPRITE_BYTES:
            raise ValueError("DIANA20_SPRITE_BYTE_BOUND")
        raw = stream.read(MAX_SPRITE_BYTES + 1)
    if not 0 < len(raw) <= MAX_SPRITE_BYTES:
        raise ValueError("DIANA20_SPRITE_BYTE_BOUND")
    variant = variant_for_pinned_sprite_sha256(hashlib.sha256(raw).hexdigest())
    return validate_sprite_bytes(raw, expected_sha256=pinned_sprite_sha256(variant),
                                 expected_variant=variant if expected_variant is None else expected_variant)


def select_native_scale(image, sprites) -> Selection:
    measured = measure_card_scale(image, PROFILE)
    evidence = dict(measured.to_dict(), adapter_version=VERSION,
                    selection_status="UNKNOWN", selected_variant=None,
                    selected_sprite_sha256=None, native_size=None,
                    selection_reason=measured.reason,
                    matcher_attempts=0, template_transform="NONE_NATIVE_ONLY")
    if measured.status != "MEASURED":
        evidence["selection_status"] = measured.status
        return Selection(None, evidence)
    eligible = [s for s in sprites
                if abs(s.card_width - measured.width_px) <= MAX_BOUNDARY_ERROR_PX
                and abs(s.card_height - measured.height_px) <= MAX_BOUNDARY_ERROR_PX]
    if len(eligible) != 1:
        evidence["selection_status"] = "TIE" if len(eligible) > 1 else "UNKNOWN"
        evidence["selection_reason"] = ("ambiguous_native_sprite" if eligible
                                        else "native_sprite_geometry_unavailable")
        return Selection(None, evidence)
    sprite = eligible[0]
    evidence.update(selection_status="SELECTED", selection_reason="measured_native_geometry",
                    selected_variant=sprite.variant, selected_sprite_sha256=sprite.sprite_sha256,
                    native_size=[sprite.card_width, sprite.card_height])
    return Selection(sprite, evidence)


class NativeScaleSelector:
    """Load a bounded immutable inventory with the original SHA allowlist.

    The legacy --sprite argument is a single available source, never a default
    measurement. --asset-root explicitly opts into a bounded multi-size set.
    """
    def __init__(self, *, sprite_path, asset_root=None):
        sprites = []
        if asset_root is None:
            sprites.append(load_native_sprite(sprite_path))
        else:
            root = Path(asset_root)
            if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
                raise ValueError("DIANA20_ASSET_ROOT_LINKED")
            if not root.is_dir():
                raise ValueError("DIANA20_ASSET_ROOT_UNAVAILABLE")
            for variant in VARIANT_CARD_SIZE:
                paths = [p for p in (root / f"all-v{variant}.png", root / str(variant) / "all.png")
                         if p.exists()]
                loaded = [load_native_sprite(p, expected_variant=variant) for p in paths]
                if loaded:
                    sprites.append(loaded[0])
        self.sprites = tuple(sprites)

    def select(self, image):
        return select_native_scale(image, self.sprites)


def compose_without_margin(payload, candidate, layer, runtime):
    """Preserve HAND_CORNER unknowns while explicitly skipping v5-only math."""
    decisions = []
    for index, unknown in enumerate(payload["unknown"]):
        if unknown.get("kind") != "HAND_CORNER":
            continue
        ref = dict(frame_sha256=payload["frame_sha256"],
                   source_sha256=payload["source_video_sha256"],
                   timestamp_ms=payload["source_timestamp_ms"], unknown_index=index)
        decision = candidate.decide(unknown, {"observation_ref": ref})
        decision["reason"] = "NATIVE_SCALE_MARGIN_UNSUPPORTED"
        decisions.append(decision)
    return layer.compose(payload, decisions, candidate, runtime)
