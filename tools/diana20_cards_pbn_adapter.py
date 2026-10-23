"""Output-only adapter for the pinned installed Diana20 cards[] JSONL.

No recognizer, geometry, solver, transport, queue or lifecycle execution. The
installed schema explicitly lacks canonical seats and hand completeness, so its
machine cards/UNKNOWN survive as review comments, never a fabricated native Deal.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from bridge_vision.pbn import export_frame_pbns, observation_record_sha256, _write_exclusive

ADAPTER_VERSION = "diana20-cards-pbn-v1"
RELEASE = "diana20-margin-installed-20261020-r1"
SOURCE = "e5a600d8d532a4f10adef191a9bfdfbb962854429849cd55821266ad71d6868f"
MAX_JSONL = 64 * 1024 * 1024
MAX_ROW = 2 * 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ROOT = "/opt/bridge-school/universal-video/code-bundles/" + RELEASE
_HELPERS = {
    "component_margin_candidate.py": "9e5e5b4a67ed42006a297175b34bb048df8b537a6228152b03b560e9393d96d9",
    "release_layer.py": "569981c5b417ea7fb6ccb8ca6abddca642561ca1cb6e86f3e3e938efc4c96c18",
    "release_scoring.py": "16fa38973e7cc97685185bd1ad40b8db6e9b9a99502a704b50739eb22456c3b2",
}


def _encode(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def installed_runtime_pins() -> dict[str, Any]:
    """Expected saved metadata, never a statement about fresh host execution."""
    return {"version": "diana20-installed-margin-worker-r1",
            "worker_path": _ROOT + "/geometry/diana20_geometry_worker.py",
            "worker_sha256": "d907fc1fbbe2b1ee3f061b81b4fd60180d58ee14b1478c94ea53bfb9a674e56a",
            "layer_version": "diana20-installed-margin-r1-20261020",
            "margin_enabled": True, "geometry_enabled": False, "factor_identity_fix_enabled": False,
            "sprite_sha256": "52eb9c97c9685758f48beb6e84013e9289bdb97f8a31b4ae8e46a0ecee3a37fb",
            "helpers": {name: {"path": _ROOT + "/geometry/" + name, "sha256": sha} for name, sha in _HELPERS.items()}}


def _need(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError("DIANA20_PBN_" + code)


def _objects(value: Any) -> bool:
    return type(value) is list and all(isinstance(v, Mapping) for v in value)


def adapt_diana20_row(row: Mapping[str, Any], *, scale_profile=None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Adapt one original persisted row; preserve every candidate and UNKNOWN."""
    _need(isinstance(row, Mapping), "ROW")
    payload, receipt = row.get("payload"), row.get("receipt")
    _need(isinstance(payload, Mapping) and isinstance(receipt, Mapping), "PAYLOAD_RECEIPT")
    sequence = row.get("sequence")
    _need(type(sequence) is int and sequence >= 0, "SEQUENCE")
    update = payload.get("recognizer_update")
    _need(isinstance(update, Mapping), "INSTALLED_UPDATE_REQUIRED")
    _need(update.get("schema") == "diana20-installed-recognizer-observation/v1" and
          update.get("version") == "diana20-installed-margin-r1-20261020" and update.get("mode") == "margin", "UPDATE_VERSION")
    empty_statuses = ("NO_HAND_CORNERS",) if scale_profile is None else ("NO_HAND_CORNERS", "SCALE_UNKNOWN", "SCALE_TIE")
    empty_legacy = (payload.get("recognizer_version") is None and payload.get("status") in empty_statuses
                    and payload.get("cards") == [] and update.get("effective_cards") == [])
    _need(payload.get("recognizer_version") == "bridgit-gambler-partial-pixels-v3" or empty_legacy, "BASE_VERSION")
    for obj, names in ((payload, ("canonical_allowed", "complete_deal")),
                       (update, ("canonical_allowed", "complete_deal", "human_verified", "geometry_enabled", "factor_identity_fix_enabled"))):
        _need(all(obj.get(name) is False for name in names), "AUTHORITY_OR_MODE")
    runtime = update.get("runtime")
    _need(isinstance(runtime, Mapping), "RUNTIME")
    if scale_profile is None:
        runtime_pins = installed_runtime_pins()
    else:
        from tools.diana20_scale_pbn_profile import VerifiedScalePBNProfile
        _need(type(scale_profile) is VerifiedScalePBNProfile, "VERIFIED_SCALE_PROFILE_REQUIRED")
        runtime_pins = scale_profile.verify_payload(payload)
    for key, expected in runtime_pins.items():
        # bool values require exact types; True must not be accepted as integer1.
        _need(runtime.get(key) == expected and (type(expected) is not bool or type(runtime.get(key)) is bool), "RUNTIME_PIN_" + key)
    _need(receipt.get("source_sha256") == payload.get("source_video_sha256") == SOURCE, "SOURCE_BINDING")
    frame = receipt.get("frame_sha256")
    _need(type(frame) is str and bool(_SHA.fullmatch(frame)) and frame == payload.get("frame_sha256"), "FRAME_BINDING")
    timestamp = receipt.get("timestamp_ms")
    _need(type(timestamp) is int and 0 <= timestamp <= 10**15 and timestamp == payload.get("source_timestamp_ms"), "TIME_BINDING")
    _need(type(receipt.get("frame_id")) is str and bool(receipt["frame_id"].strip()), "FRAME_ID")
    cards, unknown = payload.get("cards"), payload.get("unknown")
    effective, remaining = update.get("effective_cards"), update.get("effective_unknown")
    _need(all(_objects(v) for v in (cards, unknown, effective, remaining)), "CARD_ARRAYS")
    base = {k: v for k, v in payload.items() if k != "recognizer_update"}
    _need(update.get("original_payload_sha256") == _digest(_encode(base)), "BASELINE_SHA")
    for name, expected in (("original_card_count", len(cards)), ("original_unknown_count", len(unknown)),
                           ("additional_machine_candidate_count", len(effective) - len(cards))):
        _need(type(update.get(name)) is int and update[name] == expected and expected >= 0, "COUNTS")
    _need(effective[:len(cards)] == cards, "BASELINE_HISTORY")
    added_indices = []
    for item in effective[len(cards):]:
        index = item.get("source_unknown_index")
        _need(type(index) is int and 0 <= index < len(unknown) and index not in added_indices, "UNKNOWN_INDEX")
        _need(item.get("status") == "ADDITIONAL_MACHINE_CANDIDATE" and
              item.get("canonical_allowed") is False and item.get("human_verified") is False and item.get("seat") is None, "ADDITION_AUTHORITY")
        added_indices.append(index)
    _need(remaining == [v for index, v in enumerate(unknown) if index not in added_indices], "UNKNOWN_HISTORY")
    _need(_objects(update.get("decisions")), "DECISIONS")
    row_sha = _digest(_encode(row))
    record = {"status": "DIANA20_REVIEW_UNQUALIFIED_SEATS_AND_HAND_INVENTORY", "deal": None,
              "engine_version": runtime["version"], "frame_file": receipt["frame_id"],
              "frame_sha256": frame, "time": timestamp / 1000,
              "diana20_row_sha256": row_sha, "sequence": sequence}
    claims = {"adapter_version": ADAPTER_VERSION, "installed_release_claim": RELEASE,
              "original_row_sha256": row_sha, "source_original_bytes_verified": False,
              "recognizer_executed_this_export": False, "runtime_claims": runtime,
              "native_deal_gate": "UNQUALIFIED_CANONICAL_SEATS_AND_HAND_INVENTORY",
              "baseline_cards": cards, "baseline_unknown_history": unknown,
              "effective_cards": effective, "effective_unknown": remaining,
              "decisions": update["decisions"], "seat_mapping_claim": payload.get("seat_mapping"),
              "hand_inventory_completeness": payload.get("hand_inventory_completeness"),
              "receipt_claim": receipt, "inferred_solver_output": "NOT_PROVIDED"}
    if scale_profile is not None:
        claims.pop("installed_release_claim")
        claims["source_bundle_claim"] = runtime_pins["version"]
        claims["scale_source_profile"] = scale_profile.provenance()
        claims["card_scale_claim"] = payload["card_scale"]
    return record, claims


def _pairs(items):
    out = {}
    for key, value in items:
        _need(key not in out, "DUPLICATE_JSON_KEY")
        out[key] = value
    return out


def _constant(_):
    raise ValueError("DIANA20_PBN_NONFINITE_JSON")


def export_diana20_jsonl(observations: Path, output_dir: Path, *, expected_sha256: str,
                        video_title: str | None = None,
                        reconstruction_by_row_sha256: Mapping[str, Mapping[str, Any]] | None = None,
                        qualified_reconstruction_by_row_sha256: Mapping[str, Any] | None = None,
                        scale_profile=None) -> dict[str, Any]:
    """Output stage for real saved card-observations.jsonl, without inference.

    Optional reconstruction claims remain comments bound to the original row.
    Only qualified_reconstruction_by_row_sha256 admits reviewed ownership via
    separately pinned QualifiedRecovery objects; arbitrary mappings cannot do so.
    """
    path = Path(observations)
    _need(scale_profile is None or not qualified_reconstruction_by_row_sha256,
          "SCALE_QUALIFIED_RECOVERY_UNSUPPORTED")
    _need(type(expected_sha256) is str and bool(_SHA.fullmatch(expected_sha256)), "EXPECTED_SHA")
    _need(0 < path.stat().st_size <= MAX_JSONL, "JSONL_SIZE")
    raw = path.read_bytes()
    _need(len(raw) <= MAX_JSONL and _digest(raw) == expected_sha256, "JSONL_SHA")
    _need(raw.endswith(b"\n"), "TRUNCATED_JSONL")
    records, claims, row_hashes, sequences = [], {}, set(), set()
    machine = unknown = 0
    for line in raw.splitlines(keepends=True):
        _need(0 < len(line) <= MAX_ROW and bool(line.strip()), "ROW_SIZE")
        row = json.loads(line.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
        record, provenance = adapt_diana20_row(row, scale_profile=scale_profile)
        _need(record["sequence"] not in sequences and record["diana20_row_sha256"] not in row_hashes, "DUPLICATE_ROW")
        sequences.add(record["sequence"]); row_hashes.add(record["diana20_row_sha256"])
        provenance.update(input_jsonl_sha256=expected_sha256, raw_row_line_sha256=_digest(line))
        records.append(record); claims[observation_record_sha256(record)] = provenance
        machine += len(provenance["effective_cards"]); unknown += len(provenance["effective_unknown"])
    extra = dict(reconstruction_by_row_sha256 or {})
    _need(not (set(extra) - row_hashes) and all(isinstance(v, Mapping) for v in extra.values()), "RECONSTRUCTION_ROW_BINDING")
    for record in records:
        if record["diana20_row_sha256"] in extra:
            claims[observation_record_sha256(record)]["reconstruction_unvalidated_claim"] = dict(extra[record["diana20_row_sha256"]])
    qualified = dict(qualified_reconstruction_by_row_sha256 or {})
    qualified_records = {}
    _need(not (set(qualified) - row_hashes), "QUALIFIED_ROW_BINDING")
    if qualified:
        from bridge_vision.recovery_pbn import QualifiedRecovery
        _need(all(type(v) is QualifiedRecovery for v in qualified.values()), "PINNED_QUALIFIED_OBJECT_REQUIRED")
        for record in records:
            key = record["diana20_row_sha256"]
            if key in qualified:
                old_key = observation_record_sha256(record)
                record.update(input_row_sha256=key, source_video_sha256=SOURCE,
                              timestamp_ms=round(record["time"] * 1000))
                _need(qualified[key].matches(record), "QUALIFIED_TARGET_BINDING")
                new_key = observation_record_sha256(record)
                claims[new_key] = claims.pop(old_key)
                qualified_records[new_key] = qualified[key]
    # Complete JSON and binding preflight precedes all file creation.
    root = Path(output_dir).resolve(strict=True)
    copy_name = "diana20-observations-" + expected_sha256[:24] + ".jsonl"
    for value in claims.values():
        _encode(value)
    _write_exclusive(root / copy_name, raw)
    outputs = export_frame_pbns(root, records, source_metadata={"source_id": SOURCE, "video_title": video_title},
                               diagnostics_file=copy_name, provenance_by_record_sha256=claims,
                               qualified_by_record_sha256=qualified_records)
    summary = {"schema": "diana20-cards-pbn-export/v1", "adapter_version": ADAPTER_VERSION,
               "release_claim": RELEASE, "input_jsonl_sha256": expected_sha256,
               "jsonl_copy": copy_name, "frames": len(records), "machine_candidates": machine,
               "remaining_unknown": unknown,
               "native_deals_asserted": sum(v.get("ownership_card_count", 0) == 52 for v in outputs), "confirmed_cards": 0,
               "inference_run": False, "source_original_bytes_verified": False,
               "canonical_promotion_allowed": False, "geometry_changed": False,
               "pbn_outputs": [{k:v for k,v in item.items() if k != "write_status"} for item in outputs]}
    if qualified_records:
        summary["qualified_ownership_outputs"] = len(qualified_records)
    if scale_profile is not None:
        summary["release_claim"] = "diana20-pixel-scale-source-v1-installed-margin"
        summary["scale_source_profile"] = scale_profile.provenance()
    report = _encode(summary) + b"\n"
    report_name = "diana20-pbn-export-" + _digest(report)[:24] + ".json"
    _write_exclusive(root / report_name, report)
    return {**summary, "report": report_name, "pbn_outputs": outputs}
