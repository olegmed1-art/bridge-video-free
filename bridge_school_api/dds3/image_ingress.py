"""Strict image ingress and local-vision boundary for DDS3."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .screenshot import ScreenshotDealObservation
from .service import DDS3Config, solve_screenshot_observation


class ImageIngressError(ValueError):
    pass


def validate_image_payload(
    image_bytes: bytes, *, media_type: str, filename: str | None = None
) -> dict[str, Any]:
    if not image_bytes:
        raise ImageIngressError("empty image")
    if media_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise ImageIngressError("unsupported image media type")
    signatures = {
        "image/jpeg": (b"\xff\xd8\xff",),
        "image/png": (b"\x89PNG\r\n\x1a\n",),
        "image/webp": (b"RIFF",),
    }
    if not any(image_bytes.startswith(signature) for signature in signatures[media_type]):
        raise ImageIngressError("image signature mismatch")
    if media_type == "image/webp" and (len(image_bytes) < 12 or image_bytes[8:12] != b"WEBP"):
        raise ImageIngressError("image signature mismatch")
    return {
        "sha256": hashlib.sha256(image_bytes).hexdigest(),
        "bytes": len(image_bytes),
        "media_type": media_type,
        "filename": filename,
    }


@dataclass(frozen=True)
class ImageEnvelope:
    image_bytes: bytes
    observation: ScreenshotDealObservation
    media_type: str = "image/jpeg"
    filename: str | None = None

    def validate_image(self) -> dict[str, Any]:
        return validate_image_payload(
            self.image_bytes, media_type=self.media_type, filename=self.filename
        )


def solve_image_envelope(
    envelope: ImageEnvelope, *, config: DDS3Config | None = None
) -> dict[str, Any]:
    """Actual image bytes + already-produced observation -> validation -> DDS3."""
    image = envelope.validate_image()
    result = solve_screenshot_observation(envelope.observation, config=config)
    result["image"] = image
    result["pipeline"] = "image->vision_observation->52_card_validation->DDS3"
    return result


def _extract_local_observation(
    image_bytes: bytes, *, media_type: str, filename: str | None
) -> ScreenshotDealObservation:
    """Try bounded local extractors only when the preceding layout is unsupported.

    Once a layout is recognized, an OCR/deck/metadata ambiguity fails closed and is not
    reinterpreted by a different extractor. This prevents layout fallback from becoming
    hidden bridge-inference repair.
    """
    from .vision_local import LocalVisionError, extract_federation_yellow_observation

    try:
        return extract_federation_yellow_observation(
            image_bytes, media_type=media_type, filename=filename
        )
    except LocalVisionError as exc:
        if not str(exc).startswith("UNSUPPORTED_LAYOUT_"):
            raise ImageIngressError(str(exc)) from exc

    from .vision_appeals_cross import AppealsCrossVisionError
    from .vision_appeals_cross_v4 import extract_appeals_cross_observation

    try:
        return extract_appeals_cross_observation(
            image_bytes, media_type=media_type, filename=filename
        )
    except AppealsCrossVisionError as exc:
        if not str(exc).startswith("UNSUPPORTED_LAYOUT_"):
            raise ImageIngressError(str(exc)) from exc

    from .vision_publication import PublicationVisionError, extract_publication_cross_observation

    try:
        return extract_publication_cross_observation(
            image_bytes, media_type=media_type, filename=filename
        )
    except PublicationVisionError as exc:
        if not str(exc).startswith("UNSUPPORTED_LAYOUT_"):
            raise ImageIngressError(str(exc)) from exc

    from .vision_publication_grid import (
        PublicationGridVisionError,
        extract_publication_grid_observation,
    )

    try:
        return extract_publication_grid_observation(
            image_bytes, media_type=media_type, filename=filename
        )
    except PublicationGridVisionError as exc:
        if not str(exc).startswith("UNSUPPORTED_LAYOUT_"):
            raise ImageIngressError(str(exc)) from exc

    from .vision_named_quadrant import (
        NamedQuadrantVisionError,
        extract_named_quadrant_observation,
    )

    try:
        return extract_named_quadrant_observation(
            image_bytes, media_type=media_type, filename=filename
        )
    except NamedQuadrantVisionError as exc:
        raise ImageIngressError(str(exc)) from exc


def _require_raw_vision_evidence(
    observation: ScreenshotDealObservation, *, image_sha256: str
) -> str:
    """Fail closed unless a raw-image extractor produced complete pixel evidence.

    Structured callers may still use the more permissive screenshot-observation contract,
    including board-derived metadata when that is explicitly intended. The *raw image*
    path is stricter: Board, Dealer and Vulnerability must all be directly emitted by the
    extractor with confidence, every hand/suit field must carry confidence, and the
    observation must be cryptographically bound to the exact input bytes. This prevents
    a future extractor from silently turning board-number inference or an unbound OCR
    object into a production DDS3 input.
    """

    for name in ("board_number", "dealer", "vulnerability"):
        field = getattr(observation, name)
        if field is None or field.value is None or str(field.value).strip() == "":
            raise ImageIngressError(f"RAW_VISION_METADATA_MISSING:{name}")
        try:
            confidence = float(field.confidence) if field.confidence is not None else None
        except (TypeError, ValueError) as exc:
            raise ImageIngressError(f"RAW_VISION_CONFIDENCE_INVALID:{name}") from exc
        if confidence is None or not 0.0 <= confidence <= 1.0:
            raise ImageIngressError(f"RAW_VISION_CONFIDENCE_MISSING:{name}")
        if not field.source:
            raise ImageIngressError(f"RAW_VISION_SOURCE_MISSING:{name}")

    if set(observation.hands) != set("NESW"):
        raise ImageIngressError("RAW_VISION_HANDS_INCOMPLETE")
    for seat in "NESW":
        if set(observation.hands[seat]) != set("SHDC"):
            raise ImageIngressError(f"RAW_VISION_HAND_INCOMPLETE:{seat}")
        confidence_by_suit = observation.hand_confidence.get(seat)
        if confidence_by_suit is None or set(confidence_by_suit) != set("SHDC"):
            raise ImageIngressError(f"RAW_VISION_HAND_CONFIDENCE_MISSING:{seat}")
        for suit in "SHDC":
            try:
                confidence = float(confidence_by_suit[suit])
            except (TypeError, ValueError) as exc:
                raise ImageIngressError(
                    f"RAW_VISION_HAND_CONFIDENCE_INVALID:{seat}:{suit}"
                ) from exc
            if not 0.0 <= confidence <= 1.0:
                raise ImageIngressError(
                    f"RAW_VISION_HAND_CONFIDENCE_INVALID:{seat}:{suit}"
                )

    digest_field = observation.extra_metadata.get("image_sha256")
    if digest_field is None or str(digest_field.value) != image_sha256:
        raise ImageIngressError("RAW_VISION_IMAGE_SHA256_MISMATCH")
    if digest_field.confidence is None or float(digest_field.confidence) != 1.0:
        raise ImageIngressError("RAW_VISION_IMAGE_SHA256_UNVERIFIED")

    extractor_field = observation.extra_metadata.get("vision_extractor")
    if extractor_field is None or not extractor_field.value:
        raise ImageIngressError("RAW_VISION_EXTRACTOR_MISSING")
    return str(extractor_field.value)


def solve_raw_image(
    image_bytes: bytes,
    *,
    media_type: str,
    filename: str | None = None,
    config: DDS3Config | None = None,
) -> dict[str, Any]:
    """Raw pixels -> bounded local/free vision -> strict validation -> DDS3.

    Supported layout families are explicit and local. Unsupported/ambiguous images do not
    fall back to a web solver, paid model, bridge inference, or a second numerical engine.
    The raw path additionally requires explicit Board/Dealer/Vulnerability, confidence for
    every extracted field, and an exact SHA-256 binding to the received image bytes.
    """
    image = validate_image_payload(image_bytes, media_type=media_type, filename=filename)
    observation = _extract_local_observation(
        image_bytes, media_type=media_type, filename=filename
    )
    extractor = _require_raw_vision_evidence(
        observation, image_sha256=str(image["sha256"])
    )
    result = solve_screenshot_observation(observation, config=config)
    result["image"] = image
    result["pipeline"] = "image->local_free_vision->52_card_validation->DDS3"
    result["vision"] = {
        "extractor": extractor,
        "fallback_used": False,
    }
    return result
