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

    from .vision_publication import PublicationVisionError, extract_publication_cross_observation

    try:
        return extract_publication_cross_observation(
            image_bytes, media_type=media_type, filename=filename
        )
    except PublicationVisionError as exc:
        raise ImageIngressError(str(exc)) from exc


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
    """
    image = validate_image_payload(image_bytes, media_type=media_type, filename=filename)
    observation = _extract_local_observation(
        image_bytes, media_type=media_type, filename=filename
    )
    result = solve_screenshot_observation(observation, config=config)
    result["image"] = image
    result["pipeline"] = "image->local_free_vision->52_card_validation->DDS3"
    extractor = "unknown_local_extractor"
    field = observation.extra_metadata.get("vision_extractor")
    if field is not None and field.value:
        extractor = str(field.value)
    result["vision"] = {
        "extractor": extractor,
        "fallback_used": False,
    }
    return result
