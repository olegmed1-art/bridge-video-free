#!/usr/bin/env python3
"""Post-process Universal Video keyframes through the school-owned Bridge Vision engine.

Native Bridge Vision is the default. The old BBO screenshot parser remains an
explicit opt-in legacy adapter only; it is never silently selected.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from bridge_vision import BridgeVisionEngine

NativeDetectorInjection = Callable[[Path], dict[str, Any]]

LegacyParserInjection = Callable[[Path], dict[str, Any]]


def _safe_frame_path(job_dir: Path, file_name: Any) -> Path:
    if not isinstance(file_name, str) or not file_name.strip():
        raise ValueError("frame file is missing")
    name = file_name.strip()
    if Path(name).name != name:
        raise ValueError("frame file must be a basename")
    frame = (job_dir / "frames" / name).resolve()
    frames_root = (job_dir / "frames").resolve()
    try:
        frame.relative_to(frames_root)
    except ValueError as exc:
        raise ValueError("frame path escapes frames directory") from exc
    if not frame.is_file():
        raise ValueError(f"frame does not exist: {name}")
    return frame


def _wrap_injected_parser(parser: LegacyParserInjection):
    """Compatibility shim for explicit callers/tests; never selected implicitly."""
    def detector(frame: Path) -> dict[str, Any]:
        raw = parser(frame)
        hands = raw.get("hands") or {}
        count = sum(len(cards) for cards in hands.values())
        status = str(raw.get("status") or "").upper()
        confidence = 1.0 if hands and status not in {"CONFLICT", "UNAVAILABLE"} else 0.0
        return {
            "hands": hands,
            "confidence": confidence,
            "evidence": {
                "adapter": "explicit-injected-parser",
                "parser_status": status,
                "recognized_card_count": raw.get("recognized_card_count", count),
                "state_fingerprint": raw.get("state_fingerprint"),
            },
        }
    return detector


def build_engine(
    *,
    allow_legacy_old_bbo: bool = False,
    profiled_challenger: NativeDetectorInjection | None = None,
) -> BridgeVisionEngine:
    engine = BridgeVisionEngine()
    # Native detector families are registered here as they graduate from their
    # gold-set gates. Until then, native analysis fails closed rather than
    # pretending the legacy BBO parser is universal.
    if allow_legacy_old_bbo:
        from bridge_vision.legacy import old_bbo_report_parser

        engine.register("old-bbo-compat", old_bbo_report_parser)
    if profiled_challenger is not None:
        engine.register("profiled-interface-challenger", profiled_challenger)
    return engine


def process_job_frames(
    job_dir: Path,
    *,
    engine: BridgeVisionEngine | None = None,
    parser: LegacyParserInjection | None = None,
    allow_legacy_old_bbo: bool = False,
    profiled_challenger: NativeDetectorInjection | None = None,
) -> dict[str, Any]:
    selected = sum(value is not None for value in (engine, parser, profiled_challenger))
    if selected > 1:
        raise ValueError("pass only one of engine, parser or profiled_challenger")
    if allow_legacy_old_bbo and selected:
        raise ValueError("legacy old BBO mode cannot be combined with an injected detector")
    compatibility_mode = parser is not None
    root = job_dir.resolve()
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    frames = manifest.get("frames")
    if not isinstance(frames, list):
        raise ValueError("manifest frames must be an array")

    if parser is not None:
        vision = BridgeVisionEngine({"explicit-injected-parser": _wrap_injected_parser(parser)})
    else:
        vision = engine or build_engine(
            allow_legacy_old_bbo=allow_legacy_old_bbo,
            profiled_challenger=profiled_challenger,
        )
    records: list[dict[str, Any]] = []
    recognized_frames = 0
    conflict_frames = 0
    derived_fourth_hand_frames = 0

    for frame_meta in frames:
        if not isinstance(frame_meta, dict):
            raise ValueError("manifest frame entry must be an object")
        frame = _safe_frame_path(root, frame_meta.get("file"))
        result = vision.analyze_frame(frame).to_dict()
        if compatibility_mode:
            candidates = result.get("candidates") or []
            evidence = candidates[0].get("evidence", {}) if candidates else {}
            result["parser_status"] = evidence.get("parser_status", result["status"])
            result["recognized_card_count"] = evidence.get(
                "recognized_card_count",
                sum(len(hand["cards"]) for hand in (result.get("deal") or {}).get("hands", {}).values()),
            )
            result["state_fingerprint"] = evidence.get("state_fingerprint")
        result["time"] = frame_meta.get("time")
        result["frame_file"] = frame.name
        result["frame_sha256"] = frame_meta.get("sha256")
        records.append(result)
        if result["deal"] is not None:
            recognized_frames += 1
            if (result["deal"].get("derivations") or []):
                derived_fourth_hand_frames += 1
        if result["status"] == "CONFLICT":
            conflict_frames += 1

    output_path = root / "bridge_positions.jsonl"
    output_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )
    if compatibility_mode:
        summary = {
            "status": "REVIEW" if conflict_frames else "COMPLETED",
            "job_id": manifest.get("job_id"),
            "source_fingerprint": manifest.get("source_fingerprint"),
            "input_frames": len(frames),
            "output_records": len(records),
            "recognized_frames": recognized_frames,
            "conflict_frames": conflict_frames,
            "derive_fourth_hand": True,
            "derived_fourth_hand_frames": derived_fourth_hand_frames,
            "output": output_path.name,
        }
    else:
        summary = {
            "status": "REVIEW" if conflict_frames else "COMPLETED",
            "vision_engine": "native",
            "detectors": list(vision.detector_names),
            "legacy_old_bbo_enabled": bool(allow_legacy_old_bbo),
            "profiled_challenger_enabled": profiled_challenger is not None,
            "job_id": manifest.get("job_id"),
            "source_fingerprint": manifest.get("source_fingerprint"),
            "input_frames": len(frames),
            "output_records": len(records),
            "recognized_frames": recognized_frames,
            "conflict_frames": conflict_frames,
            "derive_fourth_hand": True,
            "derived_fourth_hand_frames": derived_fourth_hand_frames,
            "output": output_path.name,
        }
    summary_path = root / "bridge_positions_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_dir", type=Path)
    parser.add_argument(
        "--allow-legacy-old-bbo",
        action="store_true",
        help="explicitly enable the old layout-specific BBO compatibility parser",
    )
    args = parser.parse_args()
    summary = process_job_frames(
        args.job_dir,
        allow_legacy_old_bbo=args.allow_legacy_old_bbo,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
