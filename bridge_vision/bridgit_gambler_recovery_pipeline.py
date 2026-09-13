"""Original-Gambler successor pipeline for unresolved card recovery.

The primary recognizer remains separate.  This helper is called only when a
stable deal is incomplete: it loads the verified native Gambler sprite variant,
scans only unresolved card identities on bounded neighbouring registered
frames, aggregates independent visual retry evidence, and then permits exact
deck complement only when the remaining owner seat is mathematically unique.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from bridge_vision.bridgit_unresolved_card_recovery import (
    MAX_RETRY_FRAMES,
    recover_unresolved_deal,
    scan_unresolved_in_registered_frame,
    unresolved_cards,
)
from bridge_vision.gambler_classic_reference import (
    GamblerClassicReferenceError,
    bank_provenance,
    load_sprite,
    select_variant_for_card_size,
)

PIPELINE_VERSION = "bridgit-gambler-recovery-pipeline-v1"


class GamblerRecoveryPipelineError(ValueError):
    """The original-asset retry pipeline cannot proceed safely."""


def recover_with_original_gambler_deck(
    primary_by_seat: Mapping[str, Sequence[str]],
    registered_frames: Sequence[Mapping[str, Any]],
    *,
    gambler_sprite_path: Path,
    gambler_sprite_sha256: str,
    verified_card_width_px: float,
    verified_card_height_px: float,
    allow_exact_complement: bool = True,
) -> dict[str, Any]:
    """Retry unresolved cards with original assets before exact completion.

    ``registered_frames`` are event-selected neighbouring frames, not timer
    samples.  Each entry contains an already decoded ``image`` plus frame/pixel
    SHA-256 identities and may contain a registered ``game_window`` receipt.
    """
    try:
        variant = select_variant_for_card_size(
            verified_card_width_px, verified_card_height_px
        )
        sprite = load_sprite(
            Path(gambler_sprite_path),
            expected_sha256=gambler_sprite_sha256,
            expected_variant=variant,
        )
    except GamblerClassicReferenceError as exc:
        raise GamblerRecoveryPipelineError(
            f"invalid Gambler classic reference: {exc}"
        ) from exc

    missing = unresolved_cards(primary_by_seat)
    candidates: list[dict[str, Any]] = []
    seen_pixels: set[str] = set()
    retry_frame_count = 0
    for raw in registered_frames[:MAX_RETRY_FRAMES]:
        if not isinstance(raw, Mapping):
            raise GamblerRecoveryPipelineError(
                "registered retry frame must be an object"
            )
        pixel_sha = str(raw.get("decoded_pixel_sha256") or "")
        if pixel_sha in seen_pixels:
            continue
        seen_pixels.add(pixel_sha)
        retry_frame_count += 1
        candidates.extend(
            scan_unresolved_in_registered_frame(
                raw.get("image"),
                sprite,
                missing,
                frame_sha256=str(raw.get("frame_sha256") or ""),
                decoded_pixel_sha256=pixel_sha,
                game_window=(
                    raw.get("game_window")
                    if isinstance(raw.get("game_window"), Mapping)
                    else None
                ),
            )
        )

    result = recover_unresolved_deal(
        primary_by_seat,
        candidates,
        allow_exact_complement=allow_exact_complement,
    )
    result = dict(result)
    result["pipeline_version"] = PIPELINE_VERSION
    result["template_source"] = {
        **bank_provenance(sprite),
        "selection_basis": "VERIFIED_REGISTERED_CARD_SCALE",
        "primary_reference": "ORIGINAL_GAMBLER_CLASSIC",
        "unresolved_retry_reference": "ORIGINAL_GAMBLER_CLASSIC",
        "verified_card_width_px": float(verified_card_width_px),
        "verified_card_height_px": float(verified_card_height_px),
    }
    result["retry_frame_count"] = retry_frame_count
    result["retry_candidate_count"] = len(candidates)
    result["event_driven_retry"] = True
    result["mouse_cursor_used"] = False
    result["canonical_promotion_allowed"] = False
    return result


__all__ = [
    "GamblerRecoveryPipelineError",
    "PIPELINE_VERSION",
    "recover_with_original_gambler_deck",
]
