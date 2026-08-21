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
    if media_type == "image/webp" and (
        len(image_bytes) < 12 or image_bytes[8:12] != b"WEBP"
    ):
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
) -> tuple[ScreenshotDealObservation, str]:
    """Select only among positively recognized local layout families.

    A yellow-panel image that is recognized as that family but fails card/metadata
    QC stops immediately. Only the explicit "no yellow panel" layout miss permits
    trying the independent BridgeCourse detector. This avoids turning a validation
    failure into a cross-layout repair attempt.
    """
    from .vision_local import LocalVisionError, extract_federation_yellow_observation

    try:
        observation = extract_federation_yellow_observation(
            image_bytes, media_type=media_type, filename=filename
        )
        return observation, "local_tesseract_federation_yellow_v1"
    except LocalVisionError as federation_error:
        if str(federation_error) != "UNSUPPORTED_LAYOUT_NO_YELLOW_PANEL":
            raise ImageIngressError(str(federation_error)) from federation_error

    from .vision_bridgecourse import (
        BridgeCourseVisionError,
        extract_bridgecourse_observation,
    )

    try:
        observation = extract_bridgecourse_observation(
            image_bytes, media_type=media_type, filename=filename
        )
        return observation, "local_tesseract_bridgecourse_slide_v1"
    except BridgeCourseVisionError as bridgecourse_error:
        raise ImageIngressError(str(bridgecourse_error)) from bridgecourse_error


def solve_raw_image(
    image_bytes: bytes,
    *,
    media_type: str,
    filename: str | None = None,
    config: DDS3Config | None = None,
) -> dict[str, Any]:
    """Raw pixels -> proven local/free layout extractor -> validation -> DDS3.

    The router currently supports only the separately field-tested federation yellow
    panel and BridgeCourse slide families. Unsupported or ambiguous images fail
    closed. There is no web/paid vision fallback, bridge-inference repair, or second
    numerical solver.
    """
    image = validate_image_payload(
        image_bytes, media_type=media_type, filename=filename
    )
    observation, extractor = _extract_local_observation(
        image_bytes, media_type=media_type, filename=filename
    )
    result = solve_screenshot_observation(observation, config=config)
    result["image"] = image
    result["pipeline"] = "image->local_free_vision->52_card_validation->DDS3"
    result["vision"] = {
        "extractor": extractor,
        "fallback_used": False,
        "paid_cloud_used": False,
    }
    return result
