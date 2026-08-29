"""Post-process Universal Video keyframes through the school-owned Bridge Vision engine.

Native Bridge Vision is the default. The old BBO screenshot parser remains an
explicit opt-in legacy adapter only; it is never silently selected.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from bridge_vision import BridgeVisionEngine, fuse_card_evidence
from bridge_vision.evidence_fusion import MAX_DECLARATIONS
from bridge_vision.shadow_pdf import REPORT_NAME as SHADOW_PDF_NAME
from bridge_vision.shadow_pdf import render_shadow_pdf
from bridge_vision.shadow_pbn import render_shadow_pbn, summarize_shadow_auctions
from bridge_vision.transcript_card_observer import observe_transcript_cards

NativeDetectorInjection = Callable[[Path], dict[str, Any]]

LegacyParserInjection = Callable[[Path], dict[str, Any]]
MAX_TRANSCRIPT_BYTES = 32 * 1024 * 1024


def _bounded_speech_declarations(
    declarations: Iterable[Mapping[str, Any]] | None,
) -> list[Mapping[str, Any]]:
    if declarations is None:
        return []
    if isinstance(declarations, (str, bytes)):
        raise TypeError("speech declarations must be an iterable of objects")
    bounded: list[Mapping[str, Any]] = []
    for index, declaration in enumerate(declarations):
        if index >= MAX_DECLARATIONS:
            raise ValueError("too many speech declarations")
        if not isinstance(declaration, Mapping):
            raise TypeError("speech declaration must be an object")
        bounded.append(declaration)
    return bounded


def _speech_for_frame(
    declarations: list[Mapping[str, Any]],
    *,
    frame_file: str,
    frame_sha256: str,
    frame_time: Any,
) -> list[tuple[int, Mapping[str, Any]]]:
    selected: list[tuple[int, Mapping[str, Any]]] = []
    try:
        timestamp = float(frame_time)
    except (TypeError, ValueError):
        timestamp = math.nan
    for index, declaration in enumerate(declarations):
        declared_sha = declaration.get("frame_sha256")
        declared_file = declaration.get("frame_file")
        if declared_sha is not None:
            applies = str(declared_sha) == frame_sha256
        elif declared_file is not None:
            applies = str(declared_file) == frame_file
        else:
            try:
                start = float(declaration.get("start"))
                end = float(declaration.get("end"))
            except (TypeError, ValueError):
                applies = False
            else:
                applies = math.isfinite(timestamp) and start <= timestamp <= end
        if applies:
            selected.append((index, declaration))
    return selected


def _layout_suggestions(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    suggestions: list[Mapping[str, Any]] = []
    sources = list(result.get("candidates") or []) + list(result.get("diagnostics") or [])
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        evidence = source.get("evidence") or {}
        if not isinstance(evidence, Mapping):
            continue
        raw_suggestions = evidence.get("layout_suggestions") or []
        if not isinstance(raw_suggestions, list):
            continue
        suggestions.extend(item for item in raw_suggestions if isinstance(item, Mapping))
    return suggestions


def _observed_hands(result: Mapping[str, Any]) -> dict[str, list[str]]:
    deal = result.get("deal") or {}
    provenance = deal.get("card_provenance") if isinstance(deal, Mapping) else None
    if not isinstance(provenance, Mapping):
        return {}
    return {
        seat: list(entry.get("observed_cards") or [])
        for seat, entry in provenance.items()
        if isinstance(entry, Mapping) and entry.get("observed_cards")
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _transcript_rows(root: Path) -> list[dict[str, Any]]:
    path = root / "transcript.jsonl"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_TRANSCRIPT_BYTES:
        raise ValueError("transcript.jsonl is missing, unsafe or oversized")
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError("transcript.jsonl is not valid UTF-8") from exc
    for segment, line in enumerate(lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"transcript.jsonl segment {segment} is invalid JSON") from exc
        if not isinstance(row, dict):
            raise ValueError(f"transcript.jsonl segment {segment} must be an object")
        rows.append(row)
    return rows


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
    speech_declarations: Iterable[Mapping[str, Any]] | None = None,
    auto_transcript_card_observations: bool = False,
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
        raise TypeError("manifest frames must be an array")

    if parser is not None:
        vision = BridgeVisionEngine({"explicit-injected-parser": _wrap_injected_parser(parser)})
    else:
        vision = engine or build_engine(
            allow_legacy_old_bbo=allow_legacy_old_bbo,
            profiled_challenger=profiled_challenger,
        )
    profiled_shadow = vision.shadow_only
    speech = _bounded_speech_declarations(speech_declarations)
    if auto_transcript_card_observations and not profiled_shadow:
        raise ValueError("automatic transcript card observations are limited to profiled shadow")
    if speech and not profiled_shadow:
        raise ValueError("speech evidence fusion is limited to the profiled shadow challenger")
    records: list[dict[str, Any]] = []
    recognized_frames = 0
    conflict_frames = 0
    derived_fourth_hand_frames = 0
    speech_fusion_records = 0
    speech_review_frames = 0
    speech_conflict_frames = 0
    speech_frame_associations = 0
    matched_speech_indices: set[int] = set()

    for frame_meta in frames:
        if not isinstance(frame_meta, dict):
            raise TypeError("manifest frame entry must be an object")
        frame = _safe_frame_path(root, frame_meta.get("file"))
        actual_frame_sha = _sha256(frame) if profiled_shadow else None
        if profiled_shadow and frame_meta.get("sha256") != actual_frame_sha:
            raise ValueError("profiled shadow frame hash mismatch")
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
        result["frame_sha256"] = actual_frame_sha or frame_meta.get("sha256")
        if speech:
            selected_speech_rows = _speech_for_frame(
                speech,
                frame_file=frame.name,
                frame_sha256=str(result["frame_sha256"] or ""),
                frame_time=frame_meta.get("time"),
            )
            if selected_speech_rows:
                matched_speech_indices.update(index for index, _ in selected_speech_rows)
                speech_frame_associations += len(selected_speech_rows)
                fusion = fuse_card_evidence(
                    _observed_hands(result),
                    [declaration for _, declaration in selected_speech_rows],
                    layout_suggestions=_layout_suggestions(result),
                )
                result["speech_fusion"] = fusion
                result["fused_deal"] = fusion["deal"]
                speech_fusion_records += 1
                if fusion["status"] == "CONFLICT":
                    speech_conflict_frames += 1
                elif fusion["status"] == "REVIEW":
                    speech_review_frames += 1
        records.append(result)
        if result["deal"] is not None:
            recognized_frames += 1
            if (result["deal"].get("derivations") or []):
                derived_fourth_hand_frames += 1
        if result["status"] == "CONFLICT":
            conflict_frames += 1

    transcript_observer = None
    transcript_observations_by_frame: dict[int, list[dict[str, Any]]] = {}
    if auto_transcript_card_observations:
        transcript_observer = observe_transcript_cards(_transcript_rows(root), records)
        for observation in transcript_observer["observations"]:
            frame_index = observation.get("frame_index")
            if isinstance(frame_index, int) and 0 <= frame_index < len(records):
                transcript_observations_by_frame.setdefault(frame_index, []).append(observation)
        for frame_index, observations in transcript_observations_by_frame.items():
            record = records[frame_index]
            record["transcript_card_observations"] = observations
            accepted_hands: dict[str, list[str]] = {}
            for observation in observations:
                if observation.get("accepted_as_observation") is not True:
                    continue
                seat = str(observation.get("seat") or "").upper()
                card = str(observation.get("card") or "").upper()
                if seat in {"N", "E", "S", "W"} and card:
                    accepted_hands.setdefault(seat, []).append(card)
            if accepted_hands:
                record["multimodal_observed_hands"] = {
                    seat: sorted(set(cards)) for seat, cards in accepted_hands.items()
                }

    auction_summary = (
        summarize_shadow_auctions(records)
        if profiled_shadow
        else {
            "frame_observations_accepted": 0,
            "frame_observations_review": 0,
            "frame_observations_rejected": 0,
            "deal_statuses": {},
            "standard_pbn_auctions": 0,
        }
    )

    output_path = root / (
        "bridge_positions_profiled_shadow.jsonl" if profiled_shadow else "bridge_positions.jsonl"
    )
    output_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )
    pbn_path = None
    pdf_report = None
    if profiled_shadow:
        pbn_path = root / "bridge_positions_profiled_shadow.pbn"
        pbn_path.write_text(
            render_shadow_pbn(
                records,
                source=str(manifest.get("source_fingerprint") or manifest.get("job_id") or ""),
            ),
            encoding="utf-8",
        )
        pdf_report = render_shadow_pdf(
            records,
            frames_root=root / "frames",
            output_path=root / SHADOW_PDF_NAME,
            source=str(manifest.get("source_fingerprint") or manifest.get("job_id") or ""),
        )
    unmatched_speech_declarations = len(speech) - len(matched_speech_indices)
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
            "status": (
                "SHADOW_REVIEW"
                if profiled_shadow and (
                    conflict_frames
                    or speech_conflict_frames
                    or speech_review_frames
                    or unmatched_speech_declarations
                    or (transcript_observer and transcript_observer["status"] != "PASS")
                    or auction_summary["frame_observations_rejected"]
                    or any(
                        status != "COMPLETE_CONFIRMED"
                        for status in auction_summary["deal_statuses"]
                    )
                )
                else "SHADOW_COMPLETED"
                if profiled_shadow
                else "REVIEW"
                if conflict_frames
                else "COMPLETED"
            ),
            "vision_engine": "native",
            "detectors": list(vision.detector_names),
            "legacy_old_bbo_enabled": bool(allow_legacy_old_bbo),
            "profiled_challenger_enabled": profiled_shadow,
            "result_scope": "SHADOW_ONLY" if profiled_shadow else "CANONICAL_PIPELINE_INPUT",
            "canonical_promotion_allowed": not profiled_shadow,
            "job_id": manifest.get("job_id"),
            "source_fingerprint": manifest.get("source_fingerprint"),
            "input_frames": len(frames),
            "output_records": len(records),
            "recognized_frames": recognized_frames,
            "conflict_frames": conflict_frames,
            "derive_fourth_hand": True,
            "derived_fourth_hand_frames": derived_fourth_hand_frames,
            "speech_fusion_records": speech_fusion_records,
            "speech_review_frames": speech_review_frames,
            "speech_conflict_frames": speech_conflict_frames,
            "speech_declarations_input": len(speech),
            "speech_declarations_matched": len(matched_speech_indices),
            "speech_unmatched_declarations": unmatched_speech_declarations,
            "speech_frame_associations": speech_frame_associations,
            "auto_transcript_card_observations_enabled": auto_transcript_card_observations,
            "transcript_card_mentions": transcript_observer["mentions"] if transcript_observer else 0,
            "transcript_card_observations_accepted": transcript_observer["accepted_observations"] if transcript_observer else 0,
            "transcript_card_observations_review": transcript_observer["review_observations"] if transcript_observer else 0,
            "transcript_card_observations_conflict": transcript_observer["conflict_observations"] if transcript_observer else 0,
            "auction_frame_observations_accepted": auction_summary["frame_observations_accepted"],
            "auction_frame_observations_review": auction_summary["frame_observations_review"],
            "auction_frame_observations_rejected": auction_summary["frame_observations_rejected"],
            "auction_deal_statuses": auction_summary["deal_statuses"],
            "auction_standard_pbn_blocks": auction_summary["standard_pbn_auctions"],
            "output": output_path.name,
            "pbn_output": pbn_path.name if pbn_path else None,
            "pdf_output": pdf_report["output"] if pdf_report else None,
            "pdf_pages": pdf_report["pages"] if pdf_report else 0,
            "pdf_deals": pdf_report["deals"] if pdf_report else 0,
            "pdf_screenshots_embedded": pdf_report["screenshots_embedded"] if pdf_report else 0,
            "pdf_sha256": pdf_report["sha256"] if pdf_report else None,
        }
    summary_path = root / (
        "bridge_positions_profiled_shadow_summary.json"
        if profiled_shadow
        else "bridge_positions_summary.json"
    )
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
    parser.add_argument(
        "--auto-transcript-card-observations",
        action="store_true",
        help="opt in to exact Russian transcript + nearest-frame SHADOW card observations",
    )
    args = parser.parse_args()
    summary = process_job_frames(
        args.job_dir,
        allow_legacy_old_bbo=args.allow_legacy_old_bbo,
        auto_transcript_card_observations=args.auto_transcript_card_observations,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
