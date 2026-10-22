"""Automatic source-specific persisted-row/FrameEvidence evidence provider.

Byte and decoder receipt integrity do not qualify board, seat or card semantics.
The adapter never starts a recognizer, transcriber or geometry pass.
"""
from collections.abc import Mapping
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import json
from bridge_contracts.video_deal import FULL_DECK
from bridge_vision.bridgit_component_evidence import factor_component_view
from bridge_vision.teacher_card_reconstruction_r265 import classify_speech_modality, extract_teacher_card_declarations
from bridge_vision.runtime_recovery_evidence import (
    RuntimeRecoveryEvidence, canonical_bytes, _hash, _sha, _need, _number,
    _pixels, _roi_check, MAX_ROW_BYTES, MAX_FRAME_BYTES, MAX_RECORDS,
)
RUNTIME_VERSION = "bridge-diana20-runtime-evidence-v1"


class Diana20RuntimeProvider:
    """Per-run deterministic provider for saved or already-produced observations.

    It never invokes the recognizer or transcriber. observe_frame_evidence is the
    post-observe seam for Diana20Client: call it on the returned FrameEvidence,
    while the unchanged decoder receipt and encoded frame are still available.
    observe_saved_row is the byte-equivalent persisted-row replay seam.
    """
    def __init__(self, *, transcript_checkpoint=None, transcript_artifact_sha256=None, semantic_adapter=None):
        from video_adapters.diana20.runtime_semantics import Diana20RuntimeSemantics
        _need(semantic_adapter is None or type(semantic_adapter) is Diana20RuntimeSemantics, 'SEMANTIC_ADAPTER_TYPE')
        self._semantic_adapter = semantic_adapter
        self._records, self._frames, self._rows = [], [], set()
        self._input_bytes = 0
        self._checkpoint = None
        if transcript_checkpoint is not None:
            _need(isinstance(transcript_checkpoint, Mapping) and _sha(transcript_artifact_sha256),
                  "TRANSCRIPT_PIN_REQUIRED")
            encoded = canonical_bytes(transcript_checkpoint)
            _need(len(encoded) <= 16 * 1024 * 1024, "TRANSCRIPT_BYTE_LIMIT")
            checkpoint = json.loads(encoded)
            _need(checkpoint.get("schema") == "bridge-video-transcript-stage-checkpoint-v1"
                  and checkpoint.get("status") == "TRANSCRIPT_STAGE_COMPLETE"
                  and _sha(checkpoint.get("sourceSha256"))
                  and isinstance(checkpoint.get("segments"), list)
                  and len(checkpoint["segments"]) <= 20000, "TRANSCRIPT_CONTRACT")
            self._checkpoint = checkpoint
        self._transcript_sha = transcript_artifact_sha256

    def _speech(self, source, timestamp):
        if self._checkpoint is None:
            return {"status": "UNKNOWN_TRANSCRIPT_UNAVAILABLE", "segments": [], "declarations": []}
        if source != self._checkpoint["sourceSha256"]:
            return {"status": "UNKNOWN_TRANSCRIPT_SOURCE_MISMATCH", "segments": [], "declarations": []}
        overlapping, invalid = [], []
        for index, segment in enumerate(self._checkpoint["segments"]):
            if (not isinstance(segment, Mapping) or not _number(segment.get("start"))
                    or not _number(segment.get("end")) or not 0 <= segment["start"] < segment["end"]
                    or not isinstance(segment.get("text"), str) or len(segment["text"]) > 16000):
                invalid.append(index)
                continue
            if Decimal(str(segment["start"])) * 1000 <= timestamp < Decimal(str(segment["end"])) * 1000:
                overlapping.append((index, segment))
        _need(len(overlapping) <= 52, "TRANSCRIPT_OVERLAP_LIMIT")
        extraction = extract_teacher_card_declarations([s for _, s in overlapping])
        return {"status": "SOURCE_CLAIM_MATCH_TIME_OVERLAP_ONLY", "artifact_sha256": self._transcript_sha,
                "timestamp_units": "SECONDS_TO_MS_EXACT_DECIMAL", "same_board": "UNKNOWN",
                "source_original_bytes_verified": False, "hard_fact_count": 0,
                "invalid_segment_indices": invalid, "declarations": extraction["declarations"],
                "rejected": extraction["rejected"],
                "segments": [{"checkpoint_index": i, "segment_id": s.get("segment_id"),
                    "ordinal": s.get("ordinal"), "segment_sha256": _hash(s),
                    "start_seconds": s["start"], "end_seconds": s["end"],
                    "text": s["text"], "modality": classify_speech_modality(s["text"]),
                    "speaker_role_candidate": s.get("speaker_role_candidate"),
                    "speaker_role_confidence": s.get("speaker_role_confidence"),
                    "unreliable": s.get("unreliable"), "hard_evidence": False} for i, s in overlapping]}

    def observe_frame_evidence(self, observation, *, receipt, sequence, frame_bytes=None):
        """Use FrameEvidence.observation, never FrameEvidence.detector_result()."""
        value = getattr(observation, "observation", None)
        _need(isinstance(value, Mapping), "FRAME_EVIDENCE_OBSERVATION_REQUIRED")
        return self.observe_saved_row({"sequence": sequence, "receipt": receipt, "payload": value},
                                      frame_bytes=frame_bytes)

    def observe_saved_row(self, row, *, frame_bytes=None):
        _need(isinstance(row, Mapping), "ROW_REQUIRED")
        if frame_bytes is not None:
            _need(type(frame_bytes) is bytes and 0 < len(frame_bytes) <= MAX_FRAME_BYTES, "FRAME_BYTE_LIMIT")
        raw = canonical_bytes(row)
        _need(len(raw) <= MAX_ROW_BYTES and len(self._records) < MAX_RECORDS, "ROW_LIMIT")
        row = json.loads(raw)
        key = sha256(raw).hexdigest()
        duplicate = key in self._rows
        if duplicate:
            existing = next(r for r in self._records if r["input_row_sha256"] == key)
            if frame_bytes is not None and (type(frame_bytes) is not bytes or sha256(frame_bytes).hexdigest() != existing["frame_sha256"]):
                existing["integrity_conflicts"] = sorted(set(existing["integrity_conflicts"] + ["DUPLICATE_DELIVERY_FRAME_HASH_MISMATCH"]))
                return {"input_row_sha256": key, "duplicate_delivery": True, "status": "CONFLICT"}
            if frame_bytes is None or existing["frame_byte_verification"] == "LOCAL_BYTES_HASHED":
                return {"input_row_sha256": key, "duplicate_delivery": True}
        _need(self._input_bytes + (0 if duplicate else len(raw)) <= 64 * 1024 * 1024, "TOTAL_INPUT_LIMIT")
        sequence, payload, receipt = row.get("sequence"), row.get("payload"), row.get("receipt")
        _need(type(sequence) is int and sequence >= 0 and isinstance(payload, dict)
              and isinstance(receipt, dict), "ROW_SHAPE")
        source, frame_sha, timestamp = (receipt.get(k) for k in ("source_sha256", "frame_sha256", "timestamp_ms"))
        _need(_sha(source) and _sha(frame_sha) and type(timestamp) is int and 0 <= timestamp < 10**15,
              "ROW_BINDING_SHAPE")
        cards, unknown = payload.get("cards"), payload.get("unknown")
        _need(isinstance(cards, list) and isinstance(unknown, list)
              and len(cards) + len(unknown) <= 104
              and all(isinstance(c, dict) for c in cards + unknown), "CARD_ARRAYS")
        update = payload.get("recognizer_update") or {}
        _need(isinstance(update, dict), "UPDATE_SHAPE")
        effective = update.get("effective_cards", cards)
        remaining = update.get("effective_unknown", unknown)
        _need(isinstance(effective, list) and isinstance(remaining, list)
              and len(effective) + len(remaining) <= 104
              and all(isinstance(c, dict) for c in effective + remaining), "EFFECTIVE_ARRAYS")
        conflicts = []
        if isinstance(update.get("runtime"), Mapping) and update["runtime"].get("geometry_enabled") is not False:
            conflicts.append("RUNTIME_GEOMETRY_NOT_OFF")
        for expected, actual, name in ((source, payload.get("source_video_sha256"), "SOURCE"),
                (frame_sha, payload.get("frame_sha256"), "FRAME"),
                (timestamp, payload.get("source_timestamp_ms"), "TIME")):
            if actual is not None and (type(actual) is not type(expected) or actual != expected):
                conflicts.append(name + "_BINDING_MISMATCH")
        if update:
            base = {k: v for k, v in payload.items() if k != "recognizer_update"}
            if update.get("original_payload_sha256") != _hash(base) or effective[:len(cards)] != cards:
                conflicts.append("BASELINE_HISTORY_MISMATCH")
        decode = receipt.get("decode_receipt")
        pts_status = "UNKNOWN_DECODE_RECEIPT_UNAVAILABLE"
        if isinstance(decode, dict):
            pts_status = "SAVED_DECODER_PTS_CLAIM_CONSISTENT_NO_NEW_DECODE"
            if (decode.get("source_sha256") != source or decode.get("frame_sha256") != frame_sha
                    or type(decode.get("actual_pts_ms")) is not int or decode["actual_pts_ms"] != timestamp):
                conflicts.append("DECODER_BINDING_MISMATCH")
                pts_status = "CONFLICT"
            if decode.get("timestamp_basis") != "DECODER_ACTUAL_PTS":
                pts_status = "UNKNOWN_DECODER_TIME_BASIS"
            seconds = decode.get("actual_pts_seconds")
            if seconds is not None and (not _number(seconds) or Decimal(str(seconds)) * 1000 != timestamp):
                conflicts.append("DECODER_TIME_UNITS_MISMATCH")
                pts_status = "CONFLICT"
        pixels = None
        if frame_bytes is not None:
            if type(frame_bytes) is not bytes or sha256(frame_bytes).hexdigest() != frame_sha:
                conflicts.append("ENCODED_FRAME_HASH_MISMATCH")
            else:
                pixels = _pixels(frame_bytes)
        image_status = "UNKNOWN_FRAME_BYTES_UNAVAILABLE" if pixels is None else "LOCAL_BYTES_HASHED"
        pixel_sha = sha256(pixels.tobytes()).hexdigest() if pixels is not None else None
        if pixels is not None:
            if payload.get("frame_pixel_sha256_bgr") not in (None, pixel_sha):
                conflicts.append("FRAME_PIXEL_HASH_MISMATCH")
            if payload.get("frame_size") not in (None, [pixels.shape[1], pixels.shape[0]]):
                conflicts.append("FRAME_SIZE_MISMATCH")
        candidates = []
        for index, item in enumerate(effective + remaining):
            _need(item.get("receipt") is None or isinstance(item["receipt"], Mapping), "CANDIDATE_RECEIPT_SHAPE")
            checks = _roi_check(item, pixels)
            if any(c in ("MALFORMED_ROI_RECEIPT", "ROI_OUT_OF_FRAME", "ROI_HASH_MISMATCH") for c in checks):
                conflicts.append("CANDIDATE_ROI_CONFLICT:" + str(index))
            components = factor_component_view(item)
            card = item.get("card")
            if isinstance(card, str) and card in FULL_DECK:
                components["rank"]["candidate_domain"] = [card[0]]
                components["suit"]["candidate_domain"] = [card[1]]
            components["owner"]["candidate_domain"] = [item["seat"]] if item.get("seat") in ("N", "E", "S", "W") else list("NESW")
            candidates.append({"origin": "effective_cards" if index < len(effective) else "effective_unknown",
                "origin_index": index if index < len(effective) else index - len(effective),
                "observation_sha256": _hash(item), "raw_observation": item,
                "components": components, "roi_checks": checks, "hard_evidence": False})
        speech = self._speech(source, timestamp)
        gates = {"board_occurrence": "UNKNOWN", "settled_frame": "UNKNOWN", "orientation": "UNKNOWN",
                 "canonical_seat": "UNKNOWN", "physical_slot_inventory": "UNKNOWN", "played_zone_mapping": "UNKNOWN",
                 "semantic_card_qualification": "UNKNOWN"}
        record = {"version": RUNTIME_VERSION, "input_row_sha256": key, "sequence": sequence,
            "payload_sha256": _hash(payload), "receipt_sha256": _hash(receipt), "source_receipt": receipt,
            "source_video_sha256": source, "source_original_bytes_verified": False,
            "frame_sha256": frame_sha, "frame_byte_verification": image_status,
            "frame_pixel_sha256_bgr": pixel_sha, "timestamp_ms": timestamp, "timestamp_evidence": pts_status,
            "semantic_gates": gates, "integrity_conflicts": sorted(set(conflicts)),
            "producer_claims": {k: deepcopy(payload.get(k)) for k in ("recognizer_version", "layout_version",
                "layout_profile_sha256", "seat_mapping", "board_number", "board_identity", "attempt_identity",
                "hand_inventory_completeness", "source_binding_status", "discovery", "center_and_played_card_coverage")},
            "runtime_claims": deepcopy(update.get("runtime")), "candidate_observations": candidates,
            "teacher_evidence": speech, "hard_fact_count": 0, "independent_vote_count": 0,
            "retained_original_row": row, "canonical_promotion_allowed": False}
        if self._semantic_adapter is not None and pixels is not None:
            record['semantic_measurements'] = self._semantic_adapter.measure(pixels, source_video_sha256=source)
        frame = {"source_video_sha256": source, "frame_sha256": frame_sha, "timestamp_ms": timestamp,
            "episode_id": None, "board_epoch": None, "board_number": None,
            "observation_index": sequence, "raw_frame_size": {"width": pixels.shape[1], "height": pixels.shape[0]} if pixels is not None else None,
            "recognizer_version": payload.get("recognizer_version"), "reference_profile_id": payload.get("layout_profile_sha256"),
            "data_class": "REAL_SHADOW", "pixel_observer_version": payload.get("recognizer_version"),
            "observations": [], "runtime_row_sha256": key, "rejected_observations": [],
            "teacher_declarations": speech["declarations"], "runtime_semantic_gates": gates}
        self._rows.add(key)
        if duplicate:
            index = next(i for i, r in enumerate(self._records) if r["input_row_sha256"] == key)
            record["integrity_conflicts"] = sorted(set(record["integrity_conflicts"] + self._records[index]["integrity_conflicts"]))
            self._records[index], self._frames[index] = record, frame
        else:
            self._input_bytes += len(raw)
            self._records.append(record)
            self._frames.append(frame)
        return deepcopy(record)

    def finish(self):
        from bridge_vision.temporal_deal_recovery import accumulate_recovery_frames
        from video_adapters.diana20.runtime_semantics import annotate_observed_pairs
        records = sorted(deepcopy(self._records), key=lambda r: (r["source_video_sha256"], r["sequence"], r["input_row_sha256"]))
        annotate_observed_pairs(records)
        temporal = accumulate_recovery_frames(self._frames, evidence=RuntimeRecoveryEvidence(records))
        return {"version": RUNTIME_VERSION, "status": temporal["status"],
                "records": deepcopy(records), "temporal_recovery": temporal,
                "hard_fact_count": 0, "independent_vote_count": 0,
                "unique_frame_byte_hash_count": len({r["frame_sha256"] for r in records}),
                "geometry_enabled": False, "canonical_promotion_allowed": False,
                "geometry_field_scope": "RECOVERY_ALGORITHM_DOES_NOT_RUN_GEOMETRY",
                "semantic_admission_supported": False}
