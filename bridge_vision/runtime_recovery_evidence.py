"""Student-independent integrity helpers and fail-closed machine evidence gate."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
import json
import math
import re

from bridge_vision.original_deal_solver import RecoveryInputError

RUNTIME_VERSION = "bridge-runtime-evidence-v1"
MAX_ROW_BYTES = 2 * 1024 * 1024
MAX_FRAME_BYTES = 16 * 1024 * 1024
MAX_RECORDS = 2048
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def canonical_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise RecoveryInputError("RUNTIME_NON_JSON_INPUT") from exc


def _hash(value):
    return sha256(canonical_bytes(value)).hexdigest()


def _sha(value):
    return type(value) is str and bool(_SHA.fullmatch(value))


def _need(ok, reason):
    if not ok:
        raise RecoveryInputError("RUNTIME_" + reason)


def _number(value):
    return type(value) in (float, int) and abs(value) <= 10**15 and math.isfinite(value)


def _jpeg_size(raw):
    """Bound dimensions before handing saved JPEG bytes to OpenCV."""
    _need(raw[:2] == b"\xff\xd8", "JPEG_REQUIRED")
    offset = 2
    while offset + 4 <= len(raw):
        _need(raw[offset] == 255, "JPEG_HEADER")
        while offset < len(raw) and raw[offset] == 255:
            offset += 1
        _need(offset < len(raw), "JPEG_HEADER")
        marker = raw[offset]
        offset += 1
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        length = int.from_bytes(raw[offset:offset + 2], "big")
        _need(length >= 2 and offset + length <= len(raw), "JPEG_HEADER")
        if marker in (0xC0, 0xC1, 0xC2):
            _need(length >= 8, "JPEG_HEADER")
            height = int.from_bytes(raw[offset + 3:offset + 5], "big")
            width = int.from_bytes(raw[offset + 5:offset + 7], "big")
            _need(0 < width <= 8192 and 0 < height <= 8192 and width * height <= 16_777_216,
                  "FRAME_PIXEL_LIMIT")
            return width, height
        _need(marker != 0xDA, "JPEG_DIMENSIONS_MISSING")
        offset += length
    raise RecoveryInputError("RUNTIME_JPEG_DIMENSIONS_MISSING")


def _pixels(raw):
    import cv2
    import numpy as np
    _need(type(raw) is bytes and 0 < len(raw) <= MAX_FRAME_BYTES, "FRAME_BYTE_LIMIT")
    size = _jpeg_size(raw)
    decoded = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    _need(decoded is not None and decoded.shape[:2] == (size[1], size[0]), "JPEG_DECODE")
    return decoded


def _roi_check(item, pixels):
    """Recompute existing corner hashes; an equal ROI hash is not a glyph label."""
    receipt = item.get("receipt") or {}
    if not isinstance(receipt, Mapping):
        return ["MALFORMED_ROI_RECEIPT"]
    detail = item.get("factor_diagnostic") or receipt.get("factor_diagnostic") or {}
    candidates = [receipt, detail]
    checks = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            checks.append("MALFORMED_ROI_RECEIPT")
            continue
        box, expected = candidate.get("roi_xyxy"), candidate.get("roi_bgr_sha256")
        if box is None and expected is None:
            continue
        if (not isinstance(box, list) or len(box) != 4 or any(type(v) is not int for v in box)
                or not _sha(expected)):
            checks.append("MALFORMED_ROI_RECEIPT")
        elif pixels is None:
            checks.append("UNKNOWN_FRAME_BYTES_UNAVAILABLE")
        else:
            x1, y1, x2, y2 = box
            if not (0 <= x1 < x2 <= pixels.shape[1] and 0 <= y1 < y2 <= pixels.shape[0]):
                checks.append("ROI_OUT_OF_FRAME")
            else:
                actual = sha256(pixels[y1:y2, x1:x2].tobytes()).hexdigest()
                checks.append("MATCH" if actual == expected else "ROI_HASH_MISMATCH")
    return checks or ["UNKNOWN_ROI_RECEIPT_UNAVAILABLE"]


class RuntimeRecoveryEvidence:
    """Machine evidence gate; accepts no semantic facts in the legacy row ABI.

    Distinct from ReviewedRecoveryEvidence. Even a forged review/accepted flag
    in the machine payload cannot manufacture an independently reviewed fact.
    """
    def __init__(self, records):
        self._records = {record["input_row_sha256"]: deepcopy(record) for record in records}

    def bind(self, frame):
        record = self._records.get(frame.get("runtime_row_sha256"))
        if record is None:
            return None, "RUNTIME_EVIDENCE_MISSING"
        if record["integrity_conflicts"]:
            return None, "RUNTIME_INTEGRITY_CONFLICT"
        return None, "RUNTIME_SEMANTIC_GATES_UNKNOWN"

    def provenance(self):
        return {"version": RUNTIME_VERSION, "semantic_verification": "UNQUALIFIED_MACHINE_OUTPUT",
                "record_set_sha256": _hash(self._records)}
