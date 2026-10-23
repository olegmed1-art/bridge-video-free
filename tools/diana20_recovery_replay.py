"""Local saved Diana20 producer -> runtime evidence -> temporal recovery replay.

Reads pinned existing observations, frame JPEGs and existing ASR checkpoint.
No worker, subprocess, network, new ASR, ownership labels or production wiring.
The optional frozen candidate validator executes only its pure response check.
"""
from __future__ import annotations
import argparse
from collections import Counter
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import zipfile

from bridge_vision.runtime_recovery_evidence import (
    MAX_RECORDS, MAX_FRAME_BYTES, canonical_bytes,
)
from video_adapters.diana20.runtime_recovery import Diana20RuntimeProvider
from bridge_vision.original_deal_solver import RecoveryInputError

VALIDATOR_SHA = "fcd94731e914598cb85e35627a92cdded017da92f8002e4c3adfb65dd1d3fdc7"
CONTRACT_SHA = "26843f04d258834d497affd0a340cef1ef1f657c2d2852c0d26ffea2ea11e626"


def _need(ok, reason):
    if not ok:
        raise RecoveryInputError("REPLAY_" + reason)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _need(key not in result, "DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def parse(raw):
    def invalid(value):
        raise RecoveryInputError("REPLAY_NONFINITE_JSON")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=invalid)
        canonical_bytes(value)
        return value
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise RecoveryInputError("REPLAY_JSON_INVALID") from exc


def read_pinned(path, expected, limit):
    path = Path(path)
    before = path.stat()
    _need(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= limit, "FILE_LIMIT")
    def identity(info):
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        _need(identity(before) == identity(opened), "FILE_CHANGED")
        raw = stream.read(limit + 1)
        _need(identity(os.fstat(stream.fileno())) == identity(opened), "FILE_CHANGED")
    after = path.stat()
    _need(identity(before) == identity(after) and before.st_size == len(raw) and len(raw) <= limit
          and sha256(raw).hexdigest() == expected, "INPUT_PIN_MISMATCH")
    return raw


def frozen_candidate_validator(candidate_root):
    """Use the exact existing pure validator; never instantiate Diana20Client."""
    root = Path(candidate_root)
    path = root / "universal_video/diana20_recognizer.py"
    source = read_pinned(path, VALIDATOR_SHA, 256 * 1024)
    contract = parse(read_pinned(root / "ops/diana20-recognizer-component.json", CONTRACT_SHA, 256 * 1024))
    name = "_pinned_diana20_recovery_validator"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    # Execute exactly the bytes hashed above (do not reread mutable source).
    exec(compile(source, str(path), "exec"), module.__dict__)
    def validate(row):
        receipt = row["receipt"]
        request = {"id": receipt["frame_id"], "frame_path": "/unused/no-worker-started.jpg",
                   "source_sha256": receipt["source_sha256"], "frame_sha256": receipt["frame_sha256"],
                   "timestamp_ms": receipt["timestamp_ms"]}
        result = module.validate_response({"id": request["id"], "status": "OK", "result": row["payload"]}, request, contract)
        _need(canonical_bytes(result) == canonical_bytes(row["payload"]), "VALIDATOR_REWROTE_PAYLOAD")
        return module.FrameEvidence(result)
    return validate


def replay(*, observations, observations_sha256, output, frame_archive=None, frame_archive_sha256=None,
           frame_manifest=None, frame_manifest_sha256=None, transcript=None, transcript_sha256=None,
           candidate_root=None, semantic_calibration_root=None, semantic_reference_root=None):
    rows_raw = read_pinned(observations, observations_sha256, 64 * 1024 * 1024)
    lines = rows_raw.splitlines()
    _need(0 < len(lines) <= MAX_RECORDS, "ROW_COUNT")
    rows = [parse(raw) for raw in lines]
    checkpoint = parse(read_pinned(transcript, transcript_sha256, 16 * 1024 * 1024)) if transcript else None
    semantic_adapter = None
    if semantic_calibration_root is not None or semantic_reference_root is not None:
        _need(semantic_calibration_root is not None and semantic_reference_root is not None, 'SEMANTIC_INPUT_PAIR')
        from video_adapters.diana20.runtime_semantics import Diana20RuntimeSemantics, PROFILE_SHA, UI_PINS_SHA
        calibration, references = Path(semantic_calibration_root), Path(semantic_reference_root)
        profile = read_pinned(calibration / 'diana20_layout_profile.json', PROFILE_SHA, 65536)
        ui_pins = read_pinned(calibration / 'diana20_layout_ui_pins.json', UI_PINS_SHA, 65536)
        groups = parse(ui_pins)['reference_groups']
        reference_bytes = [read_pinned(references / ('PDF_page_022_image_' + str(i + 1) + '.png'),
                                      group['source_sha256'], 16 * 1024 * 1024) for i, group in enumerate(groups)]
        semantic_adapter = Diana20RuntimeSemantics(profile, ui_pins, reference_bytes)
    provider = Diana20RuntimeProvider(transcript_checkpoint=checkpoint, transcript_artifact_sha256=transcript_sha256,
                                     semantic_adapter=semantic_adapter)
    validate = frozen_candidate_validator(candidate_root) if candidate_root else None
    archive = None
    mapping, manifest = {}, None
    pins = {"observations_raw_sha256": observations_sha256, "transcript_raw_sha256": transcript_sha256}
    if frame_archive is not None or frame_manifest is not None:
        _need(frame_archive is not None and frame_manifest is not None, "FRAME_INPUT_PAIR_REQUIRED")
        archive_raw = read_pinned(frame_archive, frame_archive_sha256, 256 * 1024 * 1024)
        manifest = parse(read_pinned(frame_manifest, frame_manifest_sha256, 2 * 1024 * 1024))
        _need(isinstance(manifest, dict) and manifest.get("schema") == "diana20-card-evidence-export-v1"
              and isinstance(manifest.get("source"), dict) and isinstance(manifest.get("frames"), list)
              and len(manifest["frames"]) <= MAX_RECORDS, "FRAME_MANIFEST")
        for frame in manifest["frames"]:
            _need(isinstance(frame, dict) and isinstance(frame.get("evidence_id"), str)
                  and frame["evidence_id"] not in mapping, "FRAME_MANIFEST_DUPLICATE_OR_INVALID")
            mapping[frame["evidence_id"]] = frame
        import io
        archive = zipfile.ZipFile(io.BytesIO(archive_raw))
        names = archive.namelist()
        _need(len(names) <= MAX_RECORDS + 16 and len(set(names)) == len(names), "ARCHIVE_ENTRIES")
        pins.update(frame_archive_raw_sha256=frame_archive_sha256, frame_manifest_raw_sha256=frame_manifest_sha256)
    candidate_checks = []
    try:
        for row in rows:
            _need(isinstance(row, dict) and isinstance(row.get("receipt"), dict), "ROW_SHAPE")
            receipt = row["receipt"]
            _need(type(row.get("sequence")) is int and row["sequence"] >= 0
                  and isinstance(row.get("payload"), dict)
                  and type(receipt.get("frame_id")) is str and 0 < len(receipt["frame_id"]) <= 256
                  and all(type(receipt.get(k)) is str and len(receipt[k]) == 64
                          and all(c in "0123456789abcdef" for c in receipt[k])
                          for k in ("source_sha256", "frame_sha256"))
                  and type(receipt.get("timestamp_ms")) is int and receipt["timestamp_ms"] >= 0
                  and (receipt.get("decode_receipt") is None or isinstance(receipt["decode_receipt"], dict)),
                  "ROW_BINDING_SHAPE")
            image = None
            export = mapping.get(receipt.get("frame_id"))
            if export is not None:
                _need(export.get("sha256") == export.get("export_sha256") == receipt.get("frame_sha256")
                      and manifest["source"].get("sha256") == receipt.get("source_sha256"), "EXPORT_BINDING")
                name = export.get("export_path")
                _need(isinstance(name, str) and not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts
                      and "\\" not in name and ":" not in name, "ARCHIVE_MEMBER_PATH")
                _need(name in archive.namelist(), "FRAME_MEMBER_MISSING")
                info = archive.getinfo(name)
                _need(0 < info.file_size <= MAX_FRAME_BYTES, "FRAME_MEMBER_SIZE")
                image = archive.read(info)
                decode = receipt.get("decode_receipt") or {}
                _need(type(export.get("time")) in (int, float) and type(decode.get("requested_time_seconds")) in (int, float)
                      and export["time"] == decode["requested_time_seconds"], "REQUESTED_TIME_BINDING")
            if validate:
                frame_evidence = validate(row)
                # Exact original persisted row is retained, not rebuilt from the
                # object, so frame_evidence metadata and all original row keys survive.
                _need(canonical_bytes(frame_evidence.observation) == canonical_bytes(row["payload"]), "FRAME_EVIDENCE_CHANGED")
                candidate_checks.append({"sequence": row["sequence"], "result": "UNCHANGED_CANDIDATE_VALIDATE_RESPONSE_PASS"})
            provider.observe_saved_row(row, frame_bytes=image)
        result = provider.finish()
    finally:
        if archive is not None:
            archive.close()
    records = result.pop("records")
    counts = Counter()
    for record in records:
        counts.update({"frames": 1, "local_frame_bytes_verified": int(record["frame_byte_verification"] == "LOCAL_BYTES_HASHED"),
                       "machine_candidates": sum(c["origin"] == "effective_cards" for c in record["candidate_observations"]),
                       "machine_unknown": sum(c["origin"] == "effective_unknown" for c in record["candidate_observations"]),
                       "integrity_conflicts": len(record["integrity_conflicts"]),
                       "overlapping_speech_segments": len(record["teacher_evidence"]["segments"]),
                       "speech_declaration_candidates": len(record["teacher_evidence"]["declarations"])})
    report = {"version": "bridge-diana20-linked-replay-v1", "status": result["status"], "input_pins": pins,
              "counts": dict(counts), "candidate_validation_count": len(candidate_checks),
              "candidate_validator_source_sha256": VALIDATOR_SHA if validate else None,
              "candidate_contract_sha256": CONTRACT_SHA if validate else None,
              "existing_transcript_segments": len(checkpoint["segments"]) if checkpoint else 0,
              "hard_fact_count": 0, "new_recognizer_or_asr_run": False, "new_source_decode": False,
              "source_original_bytes_verified": False, "production_deployed": False, "geometry_enabled": False,
              "semantic_gates": records[0]["semantic_gates"] if records else {},
              "temporal_recovery_status": result["temporal_recovery"]["status"]}
    if semantic_adapter is not None:
        report['semantic_measurements'] = {
            'frames_measured': sum('semantic_measurements' in r for r in records),
            'current_pixel_conditional_compass_matches': sum(r.get('semantic_measurements', {}).get('status') == 'CONDITIONAL_CURRENT_PIXEL_MATCH' for r in records),
            'board_occurrences_qualified': 0, 'settled_intervals_qualified': 0,
            'method': 'EXISTING_AUCTION_MARK_PREDICATE_SEPARATE_FROM_EXACT_PIN_GATE',
            'calibration_source_pts': 'UNKNOWN_PDF_REFERENCE'}
    output = Path(output)
    output.mkdir(parents=False, exist_ok=False)
    with (output / "runtime-evidence.jsonl").open("xb") as stream:
        for record in records:
            stream.write(canonical_bytes(record) + b"\n")
    for name, value in (("recovery.json", result), ("REPLAY_REPORT.json", report), ("candidate-validation.json", candidate_checks)):
        with (output / name).open("xb") as stream:
            stream.write(canonical_bytes(value) + b"\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("observations", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--observations-sha256", required=True)
    for name in ("frame-archive", "frame-manifest", "transcript"):
        parser.add_argument("--" + name, type=Path)
        parser.add_argument("--" + name + "-sha256")
    parser.add_argument("--candidate-root", type=Path, help="Exact frozen candidate source and component JSON; pure validation only")
    parser.add_argument('--semantic-calibration-root', type=Path, help='Pinned existing Diana20 layout/UI JSON calibration')
    parser.add_argument('--semantic-reference-root', type=Path, help='Pinned existing PDF raster references, method calibration only')
    args = vars(parser.parse_args(argv))
    print(json.dumps(replay(**args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
