"""Dependency-free bridge frame-recognition boundary for Universal Video.

This adapter matches the existing `bridge_report_board_reconstruction.parse_image`
shape. It does not perform computer vision itself; it validates recognizer output
and converts only supported observations into the canonical video-deal contract.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from .video_deal import CanonicalVideoDeal, canonicalize_video_deal

BRIDGE_VIDEO_FRAME_CONTRACT_VERSION = "bridge-video-frame-v2"
PARSER_STATUSES = frozenset({"PARTIAL_BOARD_OBSERVATION", "INSUFFICIENT", "CONFLICT", "UNAVAILABLE"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_STATE_FP_RE = re.compile(r"^[0-9a-f]{20}$")


class BridgeVideoFrameContractError(ValueError):
    pass


@dataclass(frozen=True)
class CanonicalVideoFrame:
    parser_status: str
    recognized_card_count: int
    state_fingerprint: str | None
    deal: CanonicalVideoDeal | None
    time: float | None = None
    frame_file: str | None = None
    frame_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": BRIDGE_VIDEO_FRAME_CONTRACT_VERSION,
            "parser_status": self.parser_status,
            "recognized_card_count": self.recognized_card_count,
            "state_fingerprint": self.state_fingerprint,
            "time": self.time,
            "frame_file": self.frame_file,
            "frame_sha256": self.frame_sha256,
            "deal": self.deal.to_dict() if self.deal is not None else None,
        }


def _optional_time(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise BridgeVideoFrameContractError("frame time must be numeric") from exc
    if out < 0:
        raise BridgeVideoFrameContractError("frame time must be non-negative")
    return out


def _optional_text(value: Any, field: str, *, max_len: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise BridgeVideoFrameContractError(f"{field} must be a string")
    text = value.strip()
    if not text or len(text) > max_len:
        raise BridgeVideoFrameContractError(f"invalid {field}")
    return text


def canonicalize_frame_recognition(
    recognition: Any,
    *,
    time: Any = None,
    frame_file: Any = None,
    frame_sha256: Any = None,
    derive_fourth_hand: bool = True,
) -> CanonicalVideoFrame:
    if not isinstance(recognition, Mapping):
        raise BridgeVideoFrameContractError("recognition must be an object")

    status = str(recognition.get("status") or "").strip().upper()
    if status not in PARSER_STATUSES:
        raise BridgeVideoFrameContractError("unsupported parser status")

    hands = recognition.get("hands")
    if not isinstance(hands, Mapping):
        raise BridgeVideoFrameContractError("recognition hands must be an object")

    actual_count = 0
    for cards in hands.values():
        if not isinstance(cards, (list, tuple)):
            raise BridgeVideoFrameContractError("recognition hand must be an array")
        actual_count += len(cards)

    reported = recognition.get("recognized_card_count")
    if reported is None:
        recognized_count = actual_count
    else:
        try:
            recognized_count = int(reported)
        except (TypeError, ValueError) as exc:
            raise BridgeVideoFrameContractError("recognized_card_count must be an integer") from exc
        if recognized_count != actual_count:
            raise BridgeVideoFrameContractError("recognized_card_count does not match hands")

    if status == "PARTIAL_BOARD_OBSERVATION" and recognized_count < 4:
        raise BridgeVideoFrameContractError("partial board observation has fewer than four recognized cards")
    if status in {"CONFLICT", "UNAVAILABLE"} and recognized_count:
        raise BridgeVideoFrameContractError(f"{status} must not expose recognized cards")

    state_fingerprint = recognition.get("state_fingerprint")
    if state_fingerprint is not None:
        state_fingerprint = _optional_text(state_fingerprint, "state_fingerprint", max_len=20)
        if not _STATE_FP_RE.fullmatch(state_fingerprint.lower()):
            raise BridgeVideoFrameContractError("invalid state_fingerprint")
        state_fingerprint = state_fingerprint.lower()

    deal = None
    if recognized_count:
        deal = canonicalize_video_deal(
            {"hands": dict(hands)},
            derive_fourth_hand=derive_fourth_hand,
        )

    sha = _optional_text(frame_sha256, "frame_sha256", max_len=64)
    if sha is not None:
        sha = sha.lower()
        if not _SHA256_RE.fullmatch(sha):
            raise BridgeVideoFrameContractError("invalid frame_sha256")

    return CanonicalVideoFrame(
        parser_status=status,
        recognized_card_count=recognized_count,
        state_fingerprint=state_fingerprint,
        deal=deal,
        time=_optional_time(time),
        frame_file=_optional_text(frame_file, "frame_file", max_len=255),
        frame_sha256=sha,
    )


__all__ = [
    "BRIDGE_VIDEO_FRAME_CONTRACT_VERSION",
    "BridgeVideoFrameContractError",
    "CanonicalVideoFrame",
    "PARSER_STATUSES",
    "canonicalize_frame_recognition",
]
