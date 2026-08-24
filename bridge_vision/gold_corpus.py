"""Human-verified gold corpus contract for native Bridge Vision training/evaluation."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from bridge_contracts.video_deal import SEATS, canonicalize_video_deal

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
GOLD_CORPUS_VERSION = "bridge-vision-gold-v1"


class GoldCorpusError(ValueError):
    pass


def validate_case(case: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(case, Mapping):
        raise GoldCorpusError("gold case must be an object")
    if case.get("human_verified") is not True:
        raise GoldCorpusError("gold case must be explicitly human_verified")
    frame = str(case.get("frame") or "").strip()
    if not frame or Path(frame).name != frame:
        raise GoldCorpusError("frame must be a basename")
    frame_sha256 = str(case.get("frame_sha256") or "").lower()
    if not _SHA256.fullmatch(frame_sha256):
        raise GoldCorpusError("frame_sha256 must be 64 lowercase hex characters")
    hands = case.get("hands") or {}
    if not isinstance(hands, Mapping):
        raise GoldCorpusError("hands must be an object")
    unknown = set(hands) - set(SEATS)
    if unknown:
        raise GoldCorpusError("unsupported seat in gold case")
    canonical = canonicalize_video_deal({"hands": dict(hands)}).to_dict()["hands"]
    return {
        "schema": GOLD_CORPUS_VERSION,
        "frame": frame,
        "frame_sha256": frame_sha256,
        "source_id": str(case.get("source_id") or "").strip() or None,
        "human_verified": True,
        "hands": {seat: canonical[seat]["cards"] for seat in SEATS},
        "notes": str(case.get("notes") or "").strip() or None,
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen_sha: set[str] = set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            case = validate_case(raw)
        except (json.JSONDecodeError, GoldCorpusError) as exc:
            raise GoldCorpusError(f"invalid gold corpus line {lineno}: {exc}") from exc
        sha = case["frame_sha256"]
        if sha in seen_sha:
            raise GoldCorpusError(f"duplicate frame_sha256 on line {lineno}")
        seen_sha.add(sha)
        cases.append(case)
    if not cases:
        raise GoldCorpusError("gold corpus is empty")
    return cases


def to_detector_cases(cases: Iterable[Mapping[str, Any]], frames_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in cases:
        case = validate_case(raw)
        frame_path = (frames_dir / case["frame"]).resolve()
        try:
            frame_path.relative_to(frames_dir.resolve())
        except ValueError as exc:
            raise GoldCorpusError("frame escapes gold frames directory") from exc
        if not frame_path.is_file():
            raise GoldCorpusError(f"gold frame missing: {case['frame']}")
        out.append({"frame": str(frame_path), "hands": case["hands"]})
    return out


__all__ = ["GOLD_CORPUS_VERSION", "GoldCorpusError", "load_jsonl", "to_detector_cases", "validate_case"]
