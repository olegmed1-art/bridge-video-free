"""Image ingress boundary for the user-facing DDS3 pipeline.

This module intentionally does not implement OCR or a model. It defines the strict
bridge between any vision-capable runtime and the deterministic DDS3 engine. The
runtime must supply the image bytes plus its structured observation of those same
bytes. This boundary fingerprints the actual bytes, validates the observation, and
only then permits DDS3 execution.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .screenshot import ScreenshotDealObservation
from .service import DDS3Config, solve_screenshot_observation


class ImageIngressError(ValueError):
    pass


@dataclass(frozen=True)
class ImageEnvelope:
    image_bytes: bytes
    observation: ScreenshotDealObservation
    media_type: str = "image/jpeg"
    filename: str | None = None

    def validate_image(self) -> dict[str, Any]:
        if not self.image_bytes:
            raise ImageIngressError("empty image")
        if self.media_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise ImageIngressError("unsupported image media type")
        # Basic signatures prevent a text payload from being mislabeled as an image.
        signatures = {"image/jpeg": (b"\xff\xd8\xff",), "image/png": (b"\x89PNG\r\n\x1a\n",), "image/webp": (b"RIFF",)}
        if not any(self.image_bytes.startswith(s) for s in signatures[self.media_type]):
            raise ImageIngressError("image signature mismatch")
        return {"sha256": hashlib.sha256(self.image_bytes).hexdigest(), "bytes": len(self.image_bytes), "media_type": self.media_type, "filename": self.filename}


def solve_image_envelope(envelope: ImageEnvelope, *, config: DDS3Config | None = None) -> dict[str, Any]:
    """Actual image bytes + vision observation -> validation -> DDS3.

    No DD value can be produced here without both a valid image payload and a valid
    52-card ScreenshotDealObservation.
    """
    image = envelope.validate_image()
    result = solve_screenshot_observation(envelope.observation, config=config)
    result["image"] = image
    result["pipeline"] = "image->vision_observation->52_card_validation->DDS3"
    return result
