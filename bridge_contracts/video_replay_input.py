"""Pure, pinned-file selection into an untrusted source draft. No I/O or resolver."""
from copy import deepcopy
import hashlib
import json
import math

from bridge_contracts.video_source_draft import PACKET_SCHEMA


class ReplayInputError(ValueError):
    pass


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def read_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ReplayInputError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    def invalid(_):
        raise ReplayInputError("NONFINITE_JSON")
    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs, parse_constant=invalid)
        # Also catch exponents overflowing the finite float range.
        json.dumps(value, allow_nan=False)
        return value
    except (UnicodeError, ValueError, TypeError) as exc:
        if isinstance(exc, ReplayInputError):
            raise
        raise ReplayInputError("INVALID_JSON") from None


def _pinned(raw, expected, label):
    if not isinstance(raw, bytes) or sha256(raw) != expected:
        raise ReplayInputError(label + "_DIGEST_MISMATCH")
    value = read_json(raw)
    if not isinstance(value, dict):
        raise ReplayInputError(label + "_OBJECT_REQUIRED")
    return value


def _select(rows, field, wanted, label):
    if not isinstance(wanted, str) or not wanted or not isinstance(rows, list):
        raise ReplayInputError(label + "_SELECTION_INVALID")
    matches = [(index, row) for index, row in enumerate(rows)
               if isinstance(row, dict) and row.get(field) == wanted]
    if len(matches) != 1:
        raise ReplayInputError(label + "_NOT_UNIQUE")
    return matches[0]


def _interval(row):
    try:
        return (all(type(row.get(k)) in (float, int) and math.isfinite(row[k]) for k in ("start", "end"))
                and 0 <= row["start"] < row["end"])
    except OverflowError:
        return False


def build_replay_input(source_bytes, selection, *, constraints_bytes=None, speaker_map_bytes=None):
    """Pinned bytes establish file integrity only, never teacher/source authority.

    Manifest paths are interpreted by the CLI, not this function. All selectors
    are exact IDs. Input records and externally supplied constraints are copied.
    """
    if not isinstance(selection, dict) or selection.get("schema") != "video-replay-selection-v1":
        raise ReplayInputError("SELECTION_SCHEMA_INVALID")
    source = _pinned(source_bytes, selection.get("source_raw_sha256"), "SOURCE")
    constraints = None
    for raw, field, label in ((constraints_bytes, "constraints_raw_sha256", "CONSTRAINTS"),
                               (speaker_map_bytes, "speaker_map_raw_sha256", "SPEAKER_MAP")):
        if (raw is None) != (field not in selection):
            raise ReplayInputError(label + "_PIN_AND_FILE_REQUIRED")
    if constraints_bytes is not None:
        constraints = _pinned(constraints_bytes, selection["constraints_raw_sha256"], "CONSTRAINTS")
    speaker_map = None
    if speaker_map_bytes is not None:
        speaker_map = _pinned(speaker_map_bytes, selection["speaker_map_raw_sha256"], "SPEAKER_MAP")
    kind = selection.get("input_kind")
    conflicts = []
    if kind == "bounded_packet":
        if speaker_map is not None:
            raise ReplayInputError("BOUNDED_PACKET_MAP_OVERRIDE_FORBIDDEN")
        # Explicit migration of a caller-named private schema, not an implicit
        # alias or authorization. Preserve the original schema in lineage.
        if not isinstance(selection.get("source_schema"), str) or source.get("schema") != selection["source_schema"]:
            raise ReplayInputError("BOUNDED_PACKET_SCHEMA_MISMATCH")
        for key in ("source_document", "video_source_claim", "selected_transcript_segment", "episode_context", "speaker_identity_claim"):
            if not isinstance(source.get(key), dict):
                raise ReplayInputError("BOUNDED_PACKET_FIELD_INVALID:" + key)
        packet = deepcopy(source)
        packet["schema"] = PACKET_SCHEMA
    elif kind == "full_master":
        si, segment = _select(source.get("transcript"), "segment_id", selection.get("segment_id"), "SEGMENT")
        ei, episode = _select(source.get("episodes"), "episode_id", selection.get("episode_id"), "EPISODE")
        if not _interval(segment) or not _interval(episode) or not (
                episode["start"] <= segment["start"] < segment["end"] <= episode["end"]):
            raise ReplayInputError("SELECTED_INTERVAL_INVALID")
        if not isinstance(segment.get("text"), str) or not segment["text"]:
            raise ReplayInputError("SELECTED_TEXT_MISSING")
        if any("source_constraints" in row or "overlap_status" in row or row.get("conflicts")
               for row in (segment, episode)):
            conflicts.append("SELECTED_RECORD_CONSTRAINT_REVIEW_REQUIRED")
        for key in ("text_sha256", "exact_text_utf8_sha256"):
            if key in segment and segment[key] != sha256(segment["text"].encode()):
                conflicts.append("DECLARED_TEXT_DIGEST_MISMATCH")
        if not isinstance(episode.get("segment_ids"), list) or segment["segment_id"] not in episode["segment_ids"]:
            raise ReplayInputError("SEGMENT_NOT_IN_EPISODE")
        for ref in episode["segment_ids"]:
            if not isinstance(ref, str) or sum(isinstance(r, dict) and r.get("segment_id") == ref for r in source["transcript"]) != 1:
                conflicts.append("EPISODE_REFERENCE_UNRESOLVED")
        if len(episode["segment_ids"]) != len({r for r in episode["segment_ids"] if isinstance(r, str)}):
            conflicts.append("EPISODE_REFERENCE_UNRESOLVED")
        canonical = sha256(json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
        original_source = source.get("source")
        if not isinstance(original_source, dict):
            raise ReplayInputError("MASTER_SOURCE_MISSING")
        identity = {"cluster_id": segment.get("speaker_cluster"), "role": "UNKNOWN", "evidence_refs": []}
        if speaker_map is not None:
            if speaker_map.get("sourceMasterJsonSha256") != canonical:
                raise ReplayInputError("SPEAKER_MAP_PARENT_MISMATCH")
            if not source.get("job_id") or speaker_map.get("job_id") != source["job_id"]:
                raise ReplayInputError("SPEAKER_MAP_JOB_MISMATCH")
            if not selection.get("source_document_drive_id") or speaker_map.get("sourceMasterPdfDriveId") != selection["source_document_drive_id"]:
                raise ReplayInputError("SPEAKER_MAP_DOCUMENT_MISMATCH")
            mi, mapped = _select(speaker_map.get("intervals"), "interval_ref", segment["segment_id"], "MAPPED_INTERVAL")
            if not _interval(mapped) or any(mapped.get(key) != segment[key] for key in ("start", "end")):
                raise ReplayInputError("MAPPED_INTERVAL_TIME_MISMATCH")
            identity = {**deepcopy(mapped), "field_path": f"$.intervals[{mi}]"}
            if "source_constraints" in speaker_map or "overlap_status" in speaker_map or speaker_map.get("conflicts"):
                conflicts.append("MAP_CONSTRAINT_REVIEW_REQUIRED")
            if mapped.get("cluster_id") != segment.get("speaker_cluster"):
                conflicts.append("LEGACY_MAP_CLUSTER_CONFLICT")
            if mapped.get("conflicts") or mapped.get("participant_status") != "PERSON_CONFIRMED":
                conflicts.append("MAPPED_IDENTITY_UNRESOLVED")
            if "overlap_status" in mapped or "source_constraints" in mapped:
                conflicts.append("MAPPED_INTERVAL_CONSTRAINT_REVIEW_REQUIRED")
            for other in speaker_map["intervals"]:
                if other is mapped:
                    continue
                if not isinstance(other, dict) or not _interval(other):
                    conflicts.append("MAP_INTERVAL_UNCHECKABLE")
                elif other["start"] < segment["end"] and other["end"] > segment["start"]:
                    conflicts.append("OVERLAPPING_MAP_INTERVAL")
        frame_ids = selection.get("frame_ids", [])
        if not isinstance(frame_ids, list) or any(not isinstance(x, str) for x in frame_ids) or len(set(frame_ids)) != len(frame_ids):
            raise ReplayInputError("FRAME_SELECTION_INVALID")
        frames = []
        for fid in frame_ids:
            fi, frame = _select(source.get("screenshots"), "evidence_id", fid, "FRAME")
            if "source_constraints" in frame or "overlap_status" in frame or frame.get("conflicts"):
                conflicts.append("SELECTED_FRAME_CONSTRAINT_REVIEW_REQUIRED")
            frames.append({**deepcopy(frame), "original_record": deepcopy(frame), "field_path": f"$.screenshots[{fi}]",
                           "declared_frame_sha256": frame.get("sha256"), "timestamp_seconds": frame.get("time"),
                           "frame_bytes_verified": False})
            t = frame.get("time")
            try:
                inside = type(t) in (float, int) and math.isfinite(t) and segment["start"] <= t < segment["end"]
            except OverflowError:
                inside = False
            if not inside:
                conflicts.append("FRAME_OUTSIDE_HALF_OPEN_SPEECH_INTERVAL")
        for other in source["transcript"]:
            if not isinstance(other, dict) or not _interval(other):
                conflicts.append("TRANSCRIPT_INTERVAL_UNCHECKABLE")
                continue
            if isinstance(other, dict) and other is not segment and _interval(other):
                if other["start"] < segment["end"] and other["end"] > segment["start"]:
                    conflicts.append("OVERLAPPING_TRANSCRIPT_INTERVAL")
                if (other["start"] < episode["end"] and other["end"] > episode["start"]
                        and other.get("segment_id") not in episode["segment_ids"]):
                    conflicts.append("EPISODE_SEGMENT_OMITTED")
        packet = {
            "schema": PACKET_SCHEMA,
            "source_document": {"drive_file_id": selection.get("source_document_drive_id"),
                "embedded_raw_sha256": sha256(source_bytes),
                "embedded_canonical_sort_compact_utf8_sha256": canonical,
                "source_map_declared_master_sha256": speaker_map.get("sourceMasterJsonSha256") if speaker_map else None},
            "video_source_claim": {"field_path": "$.source", "drive_file_id": original_source.get("driveId"),
                "source_sha256": original_source.get("sha256"), "original_record": deepcopy(original_source),
                "source_media_bytes_rehashed": False},
            "selected_transcript_segment": {**deepcopy(segment), "field_path": f"$.transcript[{si}]",
                "original_record": deepcopy(segment),
                "exact_text": segment["text"], "exact_text_utf8_sha256": sha256(segment["text"].encode()),
                "master_speaker_cluster": segment.get("speaker_cluster"),
                "master_role_candidate": segment.get("speaker_role_candidate", "UNKNOWN"),
                "asr_text_independently_verified": False},
            "episode_context": {**deepcopy(episode), "field_path": f"$.episodes[{ei}]",
                "episode_completion_or_learning_outcome_verified": False},
            "speaker_identity_claim": identity, "frame_inventory_candidates": frames,
            "speaker_map_context_claim": {key: deepcopy(speaker_map[key]) for key in (
                "schema", "job_id", "sourceMasterPdfDriveId", "sourceMasterJsonSha256", "source_constraints",
                "overlap_status", "conflicts", "identityEvidenceDocumentRef", "participantRegistryDocumentRef"
            ) if speaker_map is not None and key in speaker_map},
            "frame_binding_status": "UNATTESTED_INVENTORY_RELATION_ONLY",
        }
        if "source_constraints" in source:
            packet["source_constraints"] = deepcopy(source["source_constraints"])
    else:
        raise ReplayInputError("INPUT_KIND_INVALID")
    if constraints is not None:
        if "source_constraints" in packet and json.dumps(packet["source_constraints"], sort_keys=True, allow_nan=False) != json.dumps(constraints, sort_keys=True, allow_nan=False):
            raise ReplayInputError("CONSTRAINTS_CONFLICT")
        packet["source_constraints"] = deepcopy(constraints)
    if "input_builder_lineage" in packet or "input_builder_conflicts" in packet:
        raise ReplayInputError("BUILDER_RESERVED_FIELD_CONFLICT")
    packet["input_builder_conflicts"] = sorted(set(conflicts))
    packet["input_builder_lineage"] = {
        "schema": "video-replay-input-builder-v1", "input_kind": kind,
        "original_schema": source.get("schema"), "source_raw_sha256": sha256(source_bytes),
        "selection": deepcopy(selection), "trusted_evidence": False,
    }
    return packet
