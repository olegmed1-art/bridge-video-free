"""Explicit SHADOW adapters for ready-made 52-class playing-card models.

World models are deliberately limited to the independent full-card reference
channel.  A school-owned glyph backend must still provide rank and suit as
separate visual observations plus frame registration.  The composer joins the
channels by geometry and never lets a vendor model assign a bridge seat.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence

import requests

RANKS = "AKQJT98765432"
SUITS = "SHDC"
CARDS = frozenset(rank + suit for rank in RANKS for suit in SUITS)
WORLD_REFERENCE_CHANNEL = "world-52-class-reference-v1"
MAX_WORLD_PREDICTIONS = 104
_MODEL_ID = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


class WorldCardBackendError(RuntimeError):
    pass


def normalize_card_class(value: Any) -> str:
    text = str(value or "").strip().upper().replace("10", "T")
    aliases = {"SPADES": "S", "HEARTS": "H", "DIAMONDS": "D", "CLUBS": "C"}
    for word, suit in aliases.items():
        text = text.replace(word, suit)
    text = re.sub(r"[^A-Z0-9]", "", text)
    if len(text) == 2 and text in CARDS:
        return text
    if len(text) == 2 and text[::-1] in CARDS:
        return text[::-1]
    raise WorldCardBackendError("world model emitted an invalid card class")


def _probability(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise WorldCardBackendError(f"invalid {field}") from exc
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise WorldCardBackendError(f"invalid {field}")
    return result


def _box(raw: Mapping[str, Any]) -> dict[str, float]:
    try:
        result = {key: float(raw[key]) for key in ("x", "y", "w", "h")}
    except (KeyError, TypeError, ValueError) as exc:
        raise WorldCardBackendError("invalid world-model box") from exc
    if not all(math.isfinite(value) for value in result.values()) or result["w"] <= 0 or result["h"] <= 0:
        raise WorldCardBackendError("invalid world-model box")
    return result


@dataclass(frozen=True)
class WorldCardPrediction:
    card: str
    confidence: float
    box: dict[str, float]
    model_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "card": self.card,
            "confidence": self.confidence,
            "box": dict(self.box),
            "model_id": self.model_id,
        }


def _iou(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    lx1, ly1 = left["x"], left["y"]
    lx2, ly2 = lx1 + left["w"], ly1 + left["h"]
    rx1, ry1 = right["x"], right["y"]
    rx2, ry2 = rx1 + right["w"], ry1 + right["h"]
    width = max(0.0, min(lx2, rx2) - max(lx1, rx1))
    height = max(0.0, min(ly2, ry2) - max(ly1, ry1))
    intersection = width * height
    union = left["w"] * left["h"] + right["w"] * right["h"] - intersection
    return intersection / union if union > 0 else 0.0


class RoboflowCardDetector:
    """Bounded hosted challenger. Frames leave the server when called."""

    def __init__(self, *, model: str, version: int, api_key: str, timeout: float = 30.0):
        if not _MODEL_ID.fullmatch(model) or int(version) < 1 or not str(api_key).strip():
            raise WorldCardBackendError("invalid Roboflow configuration")
        self.model = model
        self.version = int(version)
        self.api_key = str(api_key)
        self.timeout = float(timeout)
        self.model_id = f"roboflow:{model}/{version}"

    def __call__(self, frame: Path) -> list[WorldCardPrediction]:
        if not frame.is_file():
            raise WorldCardBackendError("Roboflow frame is unavailable")
        response = requests.post(
            f"https://detect.roboflow.com/{self.model}/{self.version}",
            params={"api_key": self.api_key, "confidence": 1, "overlap": 30},
            files={"file": (frame.name, frame.read_bytes(), "application/octet-stream")},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("predictions") if isinstance(payload, Mapping) else None
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or len(rows) > MAX_WORLD_PREDICTIONS:
            raise WorldCardBackendError("invalid Roboflow response")
        predictions: list[WorldCardPrediction] = []
        for raw in rows:
            if not isinstance(raw, Mapping):
                raise WorldCardBackendError("invalid Roboflow prediction")
            width, height = float(raw.get("width")), float(raw.get("height"))
            predictions.append(WorldCardPrediction(
                card=normalize_card_class(raw.get("class")),
                confidence=_probability(raw.get("confidence"), "Roboflow confidence"),
                box=_box({
                    "x": float(raw.get("x")) - width / 2.0,
                    "y": float(raw.get("y")) - height / 2.0,
                    "w": width,
                    "h": height,
                }),
                model_id=self.model_id,
            ))
        return predictions


class LgdGen3OnnxDetector:
    """Local YOLO-style ONNX challenger for the LGD gen3 52-class model."""

    def __init__(self, model_path: Path, *, input_size: int = 640, confidence: float = 0.01):
        if not model_path.is_file():
            raise WorldCardBackendError("LGD model is unavailable")
        self.model_path = model_path
        self.input_size = int(input_size)
        self.confidence = _probability(confidence, "LGD confidence")
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise WorldCardBackendError("onnxruntime is unavailable") from exc
        self.session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        metadata = self.session.get_modelmeta().custom_metadata_map or {}
        try:
            names_raw = json.loads(metadata.get("names", "{}"))
        except json.JSONDecodeError as exc:
            raise WorldCardBackendError("LGD model class metadata is invalid") from exc
        if isinstance(names_raw, list):
            names = {index: value for index, value in enumerate(names_raw)}
        elif isinstance(names_raw, Mapping):
            names = {int(index): value for index, value in names_raw.items()}
        else:
            names = {}
        self.names = {index: normalize_card_class(value) for index, value in names.items()}
        if set(self.names.values()) != CARDS or len(self.names) != 52:
            raise WorldCardBackendError("LGD model must expose exactly 52 unique card classes")
        self.model_id = "lgd-cards-gen3:onnx"

    def __call__(self, frame: Path) -> list[WorldCardPrediction]:
        try:
            import cv2
            import numpy as np
        except ImportError as exc:
            raise WorldCardBackendError("LGD image dependencies are unavailable") from exc
        image = cv2.imread(str(frame))
        if image is None:
            raise WorldCardBackendError("LGD frame cannot be decoded")
        height, width = image.shape[:2]
        scale = min(self.input_size / width, self.input_size / height)
        resized = cv2.resize(image, (round(width * scale), round(height * scale)))
        canvas = np.full((self.input_size, self.input_size, 3), 114, dtype=np.uint8)
        offset_x = (self.input_size - resized.shape[1]) // 2
        offset_y = (self.input_size - resized.shape[0]) // 2
        canvas[offset_y:offset_y + resized.shape[0], offset_x:offset_x + resized.shape[1]] = resized
        tensor = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype("float32") / 255.0
        output = np.asarray(self.session.run(None, {self.input_name: tensor})[0]).squeeze()
        if output.ndim != 2:
            raise WorldCardBackendError("unsupported LGD output shape")
        if output.shape[0] == 56 and output.shape[1] != 56:
            output = output.T
        if output.shape[1] != 56:
            raise WorldCardBackendError("unsupported LGD class output shape")
        predictions: list[WorldCardPrediction] = []
        for row in output:
            class_id = int(row[4:].argmax())
            confidence = float(row[4 + class_id])
            if confidence < self.confidence:
                continue
            cx, cy, box_width, box_height = map(float, row[:4])
            predictions.append(WorldCardPrediction(
                card=self.names[class_id],
                confidence=confidence,
                box=_box({
                    "x": (cx - box_width / 2.0 - offset_x) / scale,
                    "y": (cy - box_height / 2.0 - offset_y) / scale,
                    "w": box_width / scale,
                    "h": box_height / scale,
                }),
                model_id=self.model_id,
            ))
            if len(predictions) > MAX_WORLD_PREDICTIONS:
                raise WorldCardBackendError("LGD prediction cap exceeded")
        return predictions


class ProfiledWorldReferenceComposer:
    """Join separate school glyph observations with one world reference model."""

    def __init__(
        self,
        glyph_backend: Callable[[Path, Mapping[str, Any]], Mapping[str, Any]],
        world_detector: Callable[[Path], Sequence[WorldCardPrediction]],
        *,
        min_iou: float = 0.50,
    ):
        self.glyph_backend = glyph_backend
        self.world_detector = world_detector
        self.min_iou = _probability(min_iou, "world match IoU")

    def __call__(self, frame: Path, profile: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = dict(self.glyph_backend(frame, profile))
        reference_channel = str(profile.get("reference_channel_id") or WORLD_REFERENCE_CHANNEL)
        cards = payload.get("cards")
        if not isinstance(cards, Sequence) or isinstance(cards, (str, bytes)):
            raise WorldCardBackendError("glyph backend cards are invalid")
        references = list(self.world_detector(frame))
        used: set[int] = set()
        composed: list[dict[str, Any]] = []
        for raw in cards:
            if not isinstance(raw, Mapping):
                raise WorldCardBackendError("glyph observation is invalid")
            glyph_box = _box(raw.get("box") if isinstance(raw.get("box"), Mapping) else {})
            candidates = [
                (index, prediction, _iou(glyph_box, prediction.box))
                for index, prediction in enumerate(references)
                if index not in used and _iou(glyph_box, prediction.box) >= self.min_iou
            ]
            candidates.sort(key=lambda item: (item[2], item[1].confidence), reverse=True)
            if not candidates:
                continue
            if len(candidates) > 1 and abs(candidates[0][2] - candidates[1][2]) < 1e-9:
                continue
            index, reference, _ = candidates[0]
            used.add(index)
            item = dict(raw)
            item["reference_match"] = {
                "card": reference.card,
                "confidence": reference.confidence,
                "channel_id": reference_channel,
                "model_id": reference.model_id,
            }
            composed.append(item)
        payload["cards"] = composed
        payload["world_reference"] = {
            "channel_id": reference_channel,
            "prediction_count": len(references),
            "matched_count": len(composed),
            "unmatched_count": len(references) - len(used),
            "external_frame_transfer": any(item.model_id.startswith("roboflow:") for item in references),
        }
        return payload


__all__ = [
    "LgdGen3OnnxDetector",
    "ProfiledWorldReferenceComposer",
    "RoboflowCardDetector",
    "WORLD_REFERENCE_CHANNEL",
    "WorldCardBackendError",
    "WorldCardPrediction",
    "normalize_card_class",
]
