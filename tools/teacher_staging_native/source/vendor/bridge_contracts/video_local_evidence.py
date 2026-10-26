"""Read-only byte checks for the existing full-master layout and replay contract.

Local pins establish integrity, never REAL provenance, identity, role or policy.
Only embedding code may supply the existing trusted manifest resolver from an
independent evidence store. CLI/environment configuration cannot load one.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math
import os
from pathlib import Path, PurePosixPath
import re
from typing import Mapping

from bridge_contracts.video_canon_replay import (
    TrustedEvidenceUnavailable, digest, replay_result_bundle,
)
from bridge_contracts.video_replay_input import read_json

SCHEMA = "video-local-evidence-index-v1"
REPORT_SCHEMA = "video-local-evidence-inspection-v1"
_SHA = re.compile(r"^[0-9a-f]{64}$")


def _pin(value):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ValueError("LOCAL_EVIDENCE_PIN_INVALID")
    return value


def _hash_file(path):
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _object_file(path, limit):
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("LOCAL_EVIDENCE_JSON_TOO_LARGE")
    value = read_json(raw)
    if not isinstance(value, dict):
        raise ValueError("LOCAL_EVIDENCE_OBJECT_REQUIRED")
    return raw, value


def _interval(row):
    try:
        return (all(type(row.get(k)) in (int, float) and math.isfinite(row[k]) for k in ('start', 'end'))
                and 0 <= row['start'] < row['end'])
    except OverflowError:
        return False


class LocalEvidenceReader:
    """Explicit local paths only; no discovery, downloads, credentials or writes.

    The index is an inventory, not an attestation. A trusted_resolver, if supplied
    by embedding code, retains responsibility for independent source origin,
    transcript accuracy, frame-to-media relation, role, identity and policy.
    This wrapper additionally rechecks its exact transcript/frame byte bindings.
    No trusted resolver is installed by the file-only opt-in.
    """

    def __init__(self, index_path, index_sha256, *, trusted_resolver=None):
        self.index_path = Path(index_path).resolve(strict=True)
        self.root = self.index_path.parent
        raw, index = _object_file(self.index_path, 1024 * 1024)
        self.index_sha256 = _pin(index_sha256)
        if hashlib.sha256(raw).hexdigest() != self.index_sha256:
            raise ValueError("LOCAL_EVIDENCE_INDEX_DIGEST_MISMATCH")
        if set(index) != {"schema", "parent", "source", "frames"} or index["schema"] != SCHEMA:
            raise ValueError("LOCAL_EVIDENCE_INDEX_SCHEMA_INVALID")
        parent, source = index["parent"], index["source"]
        if not isinstance(parent, dict) or set(parent) != {"path", "raw_sha256", "canonical_sha256"}:
            raise ValueError("LOCAL_EVIDENCE_PARENT_INVALID")
        if not isinstance(source, dict) or set(source) != {"path", "video_file_id", "sha256"}:
            raise ValueError("LOCAL_EVIDENCE_SOURCE_INVALID")
        if not isinstance(source["video_file_id"], str) or not source["video_file_id"].strip():
            raise ValueError("LOCAL_EVIDENCE_SOURCE_ID_REQUIRED")
        _pin(parent["raw_sha256"])
        _pin(parent["canonical_sha256"])
        _pin(source["sha256"])
        frames = index["frames"]
        if not isinstance(frames, list):
            raise ValueError("LOCAL_EVIDENCE_FRAMES_INVALID")
        ids = set()
        for frame in frames:
            if not isinstance(frame, dict) or set(frame) != {"path", "evidence_id", "sha256"}:
                raise ValueError("LOCAL_EVIDENCE_FRAME_INVALID")
            fid = frame["evidence_id"]
            if not isinstance(fid, str) or not fid or fid in ids:
                raise ValueError("LOCAL_EVIDENCE_FRAME_ID_NOT_UNIQUE")
            ids.add(fid)
            _pin(frame["sha256"])
        self._index = deepcopy(index)
        self.trusted_resolver = trusted_resolver
        # Resolve paths up front, including missing paths. None explicitly means
        # unavailable media; it never authorizes a lookup outside this inventory.
        for entry in [parent, source, *frames]:
            if entry["path"] is not None:
                self._path(entry["path"])

    def _path(self, value):
        if not isinstance(value, str) or not value or "\\" in value or ":" in value:
            raise ValueError("LOCAL_EVIDENCE_PATH_INVALID")
        relative = PurePosixPath(value)
        if relative.is_absolute() or any(p in {"..", "."} for p in value.split("/")):
            raise ValueError("LOCAL_EVIDENCE_PATH_INVALID")
        path = (self.root / value).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError("LOCAL_EVIDENCE_PATH_OUTSIDE_ROOT")
        return path

    def protect_outputs(self, outputs):
        """Keep even missing evidence paths outside all output/staging targets."""
        inputs = [self.index_path] + [self._path(row["path"]) for row in (
            self._index["parent"], self._index["source"], *self._index["frames"]
        ) if row["path"] is not None]
        for output in outputs:
            output = Path(output)
            if any(output.resolve() == p or (output.exists() and p.exists() and os.path.samefile(output, p))
                   for p in inputs):
                raise ValueError("LOCAL_EVIDENCE_OUTPUT_COLLISION")

    def bind_master(self, raw):
        """Postprocessing must inspect the exact loaded master before derivation."""
        if (hashlib.sha256(raw).hexdigest() != self._index['parent']['raw_sha256']
                or digest(read_json(raw)) != self._index['parent']['canonical_sha256']):
            raise ValueError('LOCAL_EVIDENCE_LOADED_MASTER_MISMATCH')

    def _read(self):
        index = self._index
        report = {"schema": REPORT_SCHEMA, "index_raw_sha256": self.index_sha256,
                  "status": "BLOCKED", "checks": [], "gaps": [],
                  "authority_verified": False, "school_canon_write_allowed": False}

        def gap(code):
            report["gaps"].append(code)

        # Re-read on every invocation: edits between repeated runs cannot be
        # hidden by a successful earlier inspection or a cached manifest.
        try:
            raw, _ = _object_file(self.index_path, 1024 * 1024)
            if hashlib.sha256(raw).hexdigest() != self.index_sha256:
                gap("INDEX_DIGEST_MISMATCH")
        except (OSError, ValueError):
            gap("INDEX_UNREADABLE")
        parent = None
        try:
            raw, parent = _object_file(self._path(index["parent"]["path"]), 64 * 1024 * 1024)
            raw_sha, canonical_sha = hashlib.sha256(raw).hexdigest(), digest(parent)
            report["checks"].append({"kind": "parent", "raw_sha256": raw_sha,
                                     "canonical_sha256": canonical_sha})
            if raw_sha != index["parent"]["raw_sha256"]:
                gap("PARENT_RAW_DIGEST_MISMATCH")
            if canonical_sha != index["parent"]["canonical_sha256"]:
                gap("PARENT_CANONICAL_DIGEST_MISMATCH")
            source = parent.get("source")
            if not isinstance(source, dict) or source.get("driveId") != index["source"]["video_file_id"]:
                gap("PARENT_SOURCE_ID_MISMATCH")
            if not isinstance(source, dict) or source.get("sha256") != index["source"]["sha256"]:
                gap("PARENT_SOURCE_DIGEST_MISMATCH")
        except (OSError, ValueError):
            gap("PARENT_UNREADABLE")
        source_path = index["source"]["path"]
        if source_path is None:
            gap("SOURCE_MEDIA_BYTES_MISSING")
        else:
            try:
                actual = _hash_file(self._path(source_path))
                report["checks"].append({"kind": "source_media", "raw_sha256": actual})
                if actual != index["source"]["sha256"]:
                    gap("SOURCE_MEDIA_DIGEST_MISMATCH")
            except (OSError, ValueError):
                gap("SOURCE_MEDIA_UNREADABLE")
        frames = []
        if not index["frames"]:
            gap("FRAME_BYTES_MISSING")
        for frame in index["frames"]:
            try:
                actual = _hash_file(self._path(frame["path"]))
                report["checks"].append({"kind": "frame", "evidence_id": frame["evidence_id"],
                                         "raw_sha256": actual})
                if actual != frame["sha256"]:
                    gap("FRAME_DIGEST_MISMATCH")
                    continue
                rows = parent.get("screenshots") if parent else None
                matches = [r for r in rows if isinstance(r, dict) and r.get("evidence_id") == frame["evidence_id"]] if isinstance(rows, list) else []
                if len(matches) != 1 or matches[0].get("sha256") != actual:
                    gap("PARENT_FRAME_BINDING_MISMATCH")
                    continue
                frames.append(matches[0])
            except (OSError, ValueError):
                gap("FRAME_UNREADABLE")
        if self.trusted_resolver is None:
            gap("INDEPENDENT_SOURCE_IDENTITY_ROLE_POLICY_UNAVAILABLE")
        report["gaps"] = sorted(set(report["gaps"]))
        report["byte_integrity_status"] = "BLOCKED" if any(
            code != "INDEPENDENT_SOURCE_IDENTITY_ROLE_POLICY_UNAVAILABLE" for code in report["gaps"]
        ) else "VERIFIED_AGAINST_LOCAL_PINS"
        return report, parent, frames

    def _resolve(self, parent_sha, snapshot):
        report, parent, frames = snapshot
        if report["gaps"] or parent_sha != self._index["parent"]["raw_sha256"]:
            raise TrustedEvidenceUnavailable()
        manifest = self.trusted_resolver(parent_sha)
        if not isinstance(manifest, Mapping):
            raise TrustedEvidenceUnavailable()
        manifest = deepcopy(dict(manifest))
        source = manifest.get("source")
        if (not isinstance(source, dict) or source.get("video_file_id") != self._index["source"]["video_file_id"]
                or source.get("source_sha256") != self._index["source"]["sha256"]):
            raise TrustedEvidenceUnavailable()
        transcripts = manifest.get("transcripts")
        rows = parent.get("transcript")
        if not isinstance(transcripts, list) or not transcripts or not isinstance(rows, list):
            raise TrustedEvidenceUnavailable()
        for transcript in transcripts:
            if not isinstance(transcript, dict):
                raise TrustedEvidenceUnavailable()
            matches = [row for row in rows if isinstance(row, dict) and row.get("segment_id") == transcript.get("locator")]
            if len(matches) != 1:
                raise TrustedEvidenceUnavailable()
            row = matches[0]
            if (not _interval(row) or not isinstance(row.get("text"), str)
                    or hashlib.sha256(row["text"].encode("utf-8")).hexdigest() != transcript.get("text_sha256")
                    or any(row.get(k) != transcript.get(k) for k in ("start", "end"))):
                raise TrustedEvidenceUnavailable()
            binding = transcript.get("frame_evidence")
            if not isinstance(binding, dict) or not any(
                f.get("sha256") == binding.get("frame_sha256") and f.get("time") == binding.get("frame_time")
                for f in frames
            ):
                raise TrustedEvidenceUnavailable()
            t = binding.get("frame_time")
            if (type(t) not in (float, int) or not row["start"] <= t < row["end"]
                    or binding.get("source_fingerprint") != source.get("source_fingerprint")
                    or binding.get("transcript_locator") != transcript.get("locator")):
                raise TrustedEvidenceUnavailable()
        return manifest

    def __call__(self, parent_sha):
        return self._resolve(parent_sha, self._read())

    def _source_observation(self, bundle, snapshot):
        """Retain a useful exact quote without turning ASR/labels into truth."""
        report, parent, frames = snapshot
        if bundle.get('schema') != 'video-bounded-source-inspection-v1' or parent is None:
            return None
        lineage = bundle.get('input_builder_lineage', {})
        if not isinstance(lineage, Mapping):
            return None
        selection = lineage.get('selection', {})
        if not isinstance(selection, Mapping):
            return None
        selected_id = selection.get('segment_id')
        if not isinstance(selected_id, str) or not selected_id.strip():
            return None
        if (lineage.get('input_kind') != 'full_master'
                or lineage.get('source_raw_sha256') != self._index['parent']['raw_sha256']
                or any(code.startswith(('INDEX_', 'PARENT_')) for code in report['gaps'])):
            return None
        rows = parent.get('transcript')
        matches = [r for r in rows if isinstance(r, dict) and r.get('segment_id') == selected_id] if isinstance(rows, list) else []
        if len(matches) != 1 or not isinstance(matches[0].get('text'), str) or not _interval(matches[0]):
            return None
        row = matches[0]
        frame_ids = selection.get('frame_ids', [])
        if (not isinstance(frame_ids, list) or any(not isinstance(fid, str) for fid in frame_ids)
                or len(set(frame_ids)) != len(frame_ids)):
            return None
        cited_frames = []
        for fid in frame_ids:
            available = [f for f in frames if f.get('evidence_id') == fid]
            if len(available) == 1:
                frame = available[0]
                t = frame.get('time')
                inside = type(t) in (float, int) and row['start'] <= t < row['end']
                cited_frames.append({'evidence_id': fid, 'raw_sha256': frame['sha256'],
                    'timestamp_seconds': t,
                    'relation': 'INSIDE_SPEECH_INTERVAL' if inside else 'CONTEXT_ONLY_OUTSIDE_SPEECH_INTERVAL'})
        payload = {'schema': 'video-artifact-source-observation-v1',
            'stage': 'SOURCE_OBSERVATION', 'verification_status': 'ARTIFACT_BYTES_VERIFIED_MEDIA_UNVERIFIED',
            'source_video_file_id': self._index['source']['video_file_id'],
            'source_media_sha256_claim': self._index['source']['sha256'],
            'parent_raw_sha256': self._index['parent']['raw_sha256'],
            'parent_canonical_sha256': self._index['parent']['canonical_sha256'],
            'transcript': {'locator': row['segment_id'], 'start': row['start'], 'end': row['end'],
                'quote': row['text'], 'text_sha256': hashlib.sha256(row['text'].encode()).hexdigest(),
                'origin': 'EXISTING_TRANSCRIPT_UNVERIFIED_AGAINST_AUDIO'},
            'frames': cited_frames,
            'missing_frame_ids': [fid for fid in frame_ids if fid not in {f['evidence_id'] for f in cited_frames}],
            'speaker_id': 'UNKNOWN', 'speaker_identity_status': 'UNKNOWN',
            'speaker_role': 'UNKNOWN', 'media_review_status': 'UNVERIFIED',
            'allowed_use': 'REVIEW_SOURCE_ARTIFACT_WITH_UNCERTAINTY',
            'school_canon_write_allowed': False, 'canon_promotion_status': 'BLOCKED'}
        sha = digest(payload)
        return {'candidate_type': 'video_artifact_source_observation',
            'stable_key': 'video-artifact-observation:sha256:' + sha, 'payload_hash': sha,
            'payload': payload, 'quality_status': 'SOURCE_OBSERVATION', 'promotion_status': 'STAGING_ONLY',
            'evidence_refs': [row['segment_id']], 'method_version': payload['schema'],
            'authoritative_tables_modified': False}

    def replay(self, bundle):
        """Inspect even partial/bounded inputs; append a non-authoritative receipt."""
        snapshot = self._read()
        report = snapshot[0]
        result = replay_result_bundle(bundle, evidence_resolver=lambda sha: self._resolve(sha, snapshot))
        if report["gaps"]:
            # Bounded replay shares its gaps list with an already sealed draft
            # payload. Detach before adding inspection gaps to preserve hashes.
            result["gaps"] = [*result["gaps"], *(
                {"code": code, "path": "local_evidence",
                 "reason": "Local integrity or independent evidence is unavailable"}
                for code in report["gaps"])]
            result["status"] = "PARTIAL" if result["video_canon_inputs"] else "BLOCKED"
        report["bundle_sha256"] = result["bundle_sha256"]
        observation = self._source_observation(bundle, snapshot)
        if observation:
            result['candidates'].append(observation)
        sha = digest(report)
        result["candidates"].append({"candidate_type": "video_local_evidence_inspection",
            "stable_key": "video-local-evidence:sha256:" + sha, "payload_hash": sha,
            "payload": report, "quality_status": "BLOCKED", "promotion_status": "STAGING_ONLY",
            "evidence_refs": [], "method_version": REPORT_SCHEMA, "authoritative_tables_modified": False})
        result.pop("replay_sha256")
        result["replay_sha256"] = digest(result)
        return result


def reader_from_environment(env):
    path = str(env.get("BRIDGE_VIDEO_REPLAY_EVIDENCE_INDEX_PATH") or "").strip()
    pin = str(env.get("BRIDGE_VIDEO_REPLAY_EVIDENCE_INDEX_SHA256") or "").strip()
    if not path and not pin:
        return None
    if not path or not pin:
        raise ValueError("LOCAL_EVIDENCE_INDEX_PATH_AND_PIN_REQUIRED")
    return LocalEvidenceReader(path, pin)
