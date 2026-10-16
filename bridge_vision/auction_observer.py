"""Profile-bound visual auction extraction; no speech or legality reconstruction.

Uses the existing rank recognizer's pixel runtime and normalized correlation.
Profiles are review inputs, not evidence that a layout has passed a real eval.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

from bridge_contracts.video_auction import (
    SEATS, VideoAuctionContractError, normalize_call, validate_auction_prefix,
)
from bridge_vision import bridgit_rank_layout as rank

SCHEMA = "bridge-visual-auction/v3"
PROFILE_SCHEMA = "bridge-auction-cell-profile/v1"
MAX_SNAPSHOTS = 128
MAX_EVIDENCE_BYTES = 128 * 1024 * 1024


def pixel_sha(image):
    return hashlib.sha256(str(image.shape).encode() + image.tobytes()).hexdigest()


def _box(image, box):
    if (not isinstance(box, list) or len(box) != 4
            or any(type(n) is not int for n in box)):
        raise ValueError("auction box must contain four integers")
    x, y, w, h = box
    if min(x, y) < 0 or min(w, h) < 1 or x+w > image.shape[1] or y+h > image.shape[0]:
        raise ValueError("auction box outside frame")
    return image[y:y+h, x:x+w]


def _gray(image):
    cv2, _ = rank._pixel_runtime()
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image


def _same(a, b):
    _, np = rank._pixel_runtime()
    return a.shape == b.shape and float(np.abs(a.astype(float)-b.astype(float)).mean()) <= 2.0


def _same_mark(a, b):
    """A blank/occluded header must not pass an average-error-only comparison."""
    _, np = rank._pixel_runtime()
    a, b = _gray(a), _gray(b)
    if not _same(a, b) or min(float(a.std()), float(b.std())) < 2:
        return False
    vector = b.astype(np.float32).ravel()
    vector -= vector.mean()
    vector /= max(float(np.linalg.norm(vector)), 1e-6)
    return rank._similarity(a, vector.reshape(1, -1)) >= .99


class AuctionObserver:
    """One observer per source/job. Missing context closes the current occurrence."""

    def __init__(self, config, reference, *, source_id, output_dir):
        if not isinstance(config, dict) or config.get("schema") != PROFILE_SCHEMA:
            raise ValueError("unsupported auction profile")
        if config.get("frame_size") != [reference.shape[1], reference.shape[0]]:
            raise ValueError("auction reference dimensions disagree")
        if config.get("reference_pixel_sha256") != pixel_sha(reference):
            raise ValueError("auction calibration reference pixels disagree")
        config = copy.deepcopy(config)
        reference = reference.copy()
        self.config = config
        self.source_id = source_id
        self.output_dir = Path(output_dir) / "auction-evidence"
        self.reference = reference
        self.cells = config.get("cells", [])
        self.headers = config.get("headers", [])
        if not 4 <= len(self.cells) <= 80 or len(self.cells) % 4 or len(self.headers) != 4:
            raise ValueError("auction requires four columns and at most 20 rows")
        # Explicit row-major geometry; seats require visible matching headers.
        for i, box in enumerate(self.cells):
            _box(reference, box)
            if i % 4 and (box[0] < self.cells[i-1][0]+self.cells[i-1][2] or box[1] != self.cells[i-1][1]):
                raise ValueError("auction columns must run left to right")
            if i >= 4 and (box[1] < self.cells[i-4][1]+self.cells[i-4][3] or box[0] != self.cells[i-4][0]):
                raise ValueError("auction rows must run top to bottom")
        self.start_sample = _box(reference, config["start_marker"]).copy()
        self.anchor_box = config["board_anchor"]
        _box(reference, self.anchor_box)
        self.blank = _gray(_box(reference, config["blank"])).copy()
        if float(self.blank.std()) > 2:
            raise ValueError("blank calibration must be uniform")
        for h in self.headers:
            if h is not None:
                if h.get("seat") not in SEATS:
                    raise ValueError("unknown header seat")
                if float(_gray(_box(reference, h["box"])).std()) < 2:
                    raise ValueError("header must contain visible marks")
        if float(_gray(self.start_sample).std()) < 2:
            raise ValueError("start marker must contain visible marks")
        samples = config.get("templates", [])
        if not 2 <= len(samples) <= 40:
            raise ValueError("auction requires 2..40 call exemplars")
        _, np = rank._pixel_runtime()
        self.bank = {}
        self.samples = {}
        for item in samples:
            call = normalize_call(item["call"])
            if call in self.bank:
                raise ValueError("duplicate call exemplar")
            sample = _gray(_box(reference, item["box"])).copy()
            if sample.shape != self.blank.shape or float(sample.std()) < 2:
                raise ValueError("call exemplar is blank or wrong size")
            vector = sample.astype(np.float32).ravel()
            vector -= vector.mean()
            vector /= max(float(np.linalg.norm(vector)), 1e-6)
            self.bank[call] = vector.reshape(1, -1)
            self.samples[call] = sample
        for box in self.cells:
            if (box[3], box[2]) != self.blank.shape:
                raise ValueError("all auction cells must match exemplar size")
        self.profile_sha256 = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        self.reference_pixel_sha256 = pixel_sha(reference)
        self.occurrences = []
        self.current = None
        self.previous_anchor = None
        self.snapshot_count = 0
        self.evidence_bytes = 0
        self.truncated = False
        self.last_timestamp = -1
        self.coverage_issues = set()

    def _read_cell(self, image, box):
        sample = _gray(_box(image, box))
        _, np = rank._pixel_runtime()
        # Mean error can hide a sparse/faded call. Only uniform pixelwise agreement
        # proves a blank within this fixed-layout calibration.
        delta = np.abs(sample.astype(float)-self.blank.astype(float))
        if float(delta.max()) <= 2 and float(sample.max())-float(sample.min()) <= 2:
            return {"call": None, "status": "BLANK", "score": None, "margin": None}
        scores = sorted(((rank._similarity(sample, bank), call)
                         for call, bank in self.bank.items()), reverse=True)
        score, call = scores[0]
        margin = score - scores[1][0]
        # Correlation alone is invariant to brightness and cannot reject all OOD glyphs.
        _, np = rank._pixel_runtime()
        error = float(np.abs(sample.astype(float)-self.samples[call].astype(float)).mean())
        accepted = score >= .97 and margin >= .05 and error <= 8
        return {"call": call if accepted else None,
                "status": "READ" if accepted else "UNREADABLE",
                "score": round(score, 6), "margin": round(margin, 6),
                "mean_pixel_error": round(error, 6)}

    def observe(self, image, timestamp_ms):
        if type(timestamp_ms) is not int or timestamp_ms <= self.last_timestamp:
            raise ValueError("auction timestamps must strictly increase")
        self.last_timestamp = timestamp_ms
        if self.truncated:
            return
        if image is None or image.shape[:2] != self.reference.shape[:2]:
            self.coverage_issues.add("DECODE_GAP" if image is None else "UNSUPPORTED_AUCTION_DIMENSIONS")
            self.current = self.previous_anchor = None
            return
        anchor = _gray(_box(image, self.anchor_box))
        if float(anchor.std()) < 2:
            self.current = self.previous_anchor = None
            return
        anchor_sha = pixel_sha(anchor)
        if anchor_sha != self.previous_anchor:
            occurrence_id = hashlib.sha256(
                f"{self.source_id}|{len(self.occurrences)}|{timestamp_ms}|{anchor_sha}".encode()
            ).hexdigest()
            self.current = {
                "board_occurrence_id": occurrence_id, "source_id": self.source_id,
                "start_ms": timestamp_ms, "end_ms": timestamp_ms,
                "board_number": None, "anchor_pixel_sha256": anchor_sha, "observations": [],
                "latest_observation_index": None, "progression_broken": False,
            }
            self.occurrences.append(self.current)
            self.previous_anchor = anchor_sha
        self.current["end_ms"] = timestamp_ms
        frame_sha = pixel_sha(image)
        for index, prior in enumerate(self.current["observations"]):
            if prior["frame_pixel_sha256"] == frame_sha:
                self._set_latest(index, timestamp_ms)
                return
        seats = []
        for h in self.headers:
            seats.append(h["seat"] if h and _same_mark(_box(image, h["box"]), _box(self.reference, h["box"])) else None)
        orientation_known = (None not in seats and len(set(seats)) == 4
                             and all(seats[(i+1) % 4] == SEATS[(SEATS.index(seats[i])+1) % 4] for i in range(4)))
        start_visible = _same_mark(_box(image, self.config["start_marker"]), self.start_sample)
        cells = []
        for index, box in enumerate(self.cells):
            item = self._read_cell(image, box)
            item.update({"row": index // 4, "column": index % 4, "seat": seats[index % 4],
                         "box": box, "timestamp_ms": timestamp_ms, "source": "VISUAL_TEMPLATE"})
            cells.append(item)
        cv2, _ = rank._pixel_runtime()
        ok, encoded = cv2.imencode(".png", image)
        if not ok:
            raise ValueError("auction evidence cannot be encoded")
        payload = encoded.tobytes()
        if self.snapshot_count >= MAX_SNAPSHOTS or self.evidence_bytes + len(payload) > MAX_EVIDENCE_BYTES:
            self.truncated = True
            return
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"auction-{timestamp_ms:010d}-{frame_sha[:12]}.png"
        path.write_bytes(payload)
        obs = {"timestamp_ms": timestamp_ms, "frame_pixel_sha256": frame_sha,
               "frame_sha256": hashlib.sha256(payload).hexdigest(), "frame_path": str(path),
               "start_visible": start_visible, "orientation_known": orientation_known,
               "cells": cells}
        self.current["observations"].append(obs)
        self._set_latest(len(self.current["observations"])-1, timestamp_ms)
        self.snapshot_count += 1
        self.evidence_bytes += len(payload)

    def _set_latest(self, index, timestamp_ms):
        observations = self.current["observations"]
        previous = self.current["latest_observation_index"]
        new = self._prefix(observations[index])
        old = self._prefix(observations[previous]) if previous is not None else None
        if old is not None:
            old_calls = [c["call"] for c in old[0]]
            if (new is None or new[1] != old[1]
                    or [c["call"] for c in new[0]][:len(old_calls)] != old_calls):
                self.current["progression_broken"] = True
        self.current["latest_observation_index"] = index
        self.current["latest_observation_timestamp_ms"] = timestamp_ms

    @staticmethod
    def _prefix(obs):
        cells = obs["cells"]
        used = [i for i, c in enumerate(cells) if c["status"] != "BLANK"]
        if not used or not obs["start_visible"] or not obs["orientation_known"]:
            return None
        first, last = min(used), max(used)
        if first >= 4 or any(c["status"] != "READ" for c in cells[first:last+1]):
            return None
        # Cells after the observed prefix remain absent, never padded with PASS.
        selected = cells[first:last+1]
        return selected, selected[0]["seat"]

    def result(self):
        auctions = []
        for occurrence in self.occurrences:
            out = dict(occurrence)
            observations = out["observations"]
            prefixes = [(obs, self._prefix(obs)) for obs in observations]
            prefixes = [(obs, p) for obs, p in prefixes if p is not None]
            out["visible_fragments"] = [
                {"timestamp_ms": obs["timestamp_ms"], "frame_sha256": obs["frame_sha256"],
                 "frame_path": obs["frame_path"], "start_visible": obs["start_visible"],
                 "order_basis": "SCREEN_ROW_MAJOR_ONLY", "calls": [
                     dict(c, call=c["call"], sequence_index=None)
                     for c in obs["cells"] if c["status"] != "BLANK"
                 ]} for obs in observations
            ]
            out.update({"status": "PARTIAL", "complete": False, "ordered_calls": [],
                        "contract": None, "declarer": None, "dealer": None,
                        "order_basis": "VISIBLE_CELL_POSITION", "uncertainties": [],
                        "canonical_promotion_allowed": False})
            if not prefixes:
                out["uncertainties"].append("START_SEATS_OR_CONTIGUOUS_PREFIX_NOT_PROVEN")
            else:
                longest_obs, (longest, dealer) = max(prefixes, key=lambda p: len(p[1][0]))
                calls = [c["call"] for c in longest]
                conflict = any(p[1] != dealer or [c["call"] for c in p[0]] != calls[:len(p[0])]
                               for _, p in prefixes)
                out["dealer"] = dealer
                out["ordered_calls"] = [
                    dict(cell, index=i, evidence=[
                        {"timestamp_ms": obs["timestamp_ms"], "frame_sha256": obs["frame_sha256"],
                         "frame_path": obs["frame_path"]}
                        for obs, p in prefixes if p[1] == dealer and len(p[0]) > i
                        and [c["call"] for c in p[0]][:i+1] == calls[:i+1]
                    ]) for i, cell in enumerate(longest)
                ]
                if conflict or occurrence["progression_broken"]:
                    out["status"] = "CONFLICT" if conflict else "REVIEW"
                    out["uncertainties"].append("INCOMPATIBLE_VISIBLE_PREFIXES" if conflict else "AUCTION_PROGRESS_REVERSED_OR_CONTEXT_LOST")
                else:
                    try:
                        legal = validate_auction_prefix(calls, dealer=dealer)
                        support = [obs for obs, p in prefixes if len(p[0]) == len(calls)]
                        confirmed = (len(support) >= 2 and
                                     support[-1]["timestamp_ms"] - support[0]["timestamp_ms"] >= 500)
                        out["legality"] = "VALID_COMPLETE" if legal["terminated"] else "VALID_PREFIX"
                        latest_index = occurrence["latest_observation_index"]
                        latest = self._prefix(observations[latest_index]) if latest_index is not None else None
                        latest_matches = (latest is not None and latest[1] == dealer
                                          and [c["call"] for c in latest[0]] == calls)
                        if legal["terminated"] and confirmed and not self.truncated and latest_matches:
                            out.update({"status": "COMPLETE_CONFIRMED", "complete": True,
                                        "contract": legal["contract"], "declarer": legal["declarer"]})
                        elif legal["terminated"]:
                            out["status"] = "COMPLETE_NEEDS_CONFIRMATION"
                            out["uncertainties"].append("DISTINCT_FRAMES_OR_COVERAGE_REQUIRED")
                    except VideoAuctionContractError as exc:
                        out["status"] = "REVIEW"
                        out["legality"] = str(exc)
            # All snapshots, including scrolling suffixes and unreadable cells, survive.
            if self.truncated:
                out["uncertainties"].append("OBSERVATION_BUDGET_EXHAUSTED")
            auctions.append(out)
        return {"schema": SCHEMA, "status": "TRUNCATED" if self.truncated else "PARTIAL_COVERAGE" if self.coverage_issues else "OBSERVED",
                "coverage_issues": sorted(self.coverage_issues),
                "calibration_profile": copy.deepcopy(self.config),
                "source_id": self.source_id, "profile_sha256": self.profile_sha256,
                "reference_pixel_sha256": self.reference_pixel_sha256,
                "confidence_kind": "UNCALIBRATED_TEMPLATE_SIMILARITY",
                "supported_layout": "CALIBRATED_FIXED_FOUR_COLUMN_TABLE_ONLY",
                "auctions": auctions, "canonical_promotion_allowed": False}


def speech_mentions(segments):
    """Preserve speech mentions separately. Even a positive sentence is not visual fact."""
    result = []
    for index, segment in enumerate(segments or []):
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "")
        mentions = re.findall(r"(?<!\w)(?:[1-7]\s*(?:NT|[CDHSN])|PASS|XX|X|ПАС|КОНТРА|РЕКОНТРА)(?!\w)", text.upper())
        if not mentions:
            continue
        hypothetical = bool(re.search(r"\b(if|suppose|would|если|допустим|например|могли)\b|\?", text, re.I))
        negated = bool(re.search(r"\b(not|never|не|нет|нельзя)\b", text, re.I))
        result.append({"segment_index": index, "start": segment.get("start"), "end": segment.get("end"),
                       "source": "SPEECH", "text": text, "mentions": mentions,
                       "statement_type": "NEGATED" if negated else "HYPOTHESIS" if hypothetical else "UNVERIFIED_MENTION",
                       "seat": None, "board_occurrence_id": None, "actual_auction_evidence": False})
    return result


def observer_from_profile(profile_path, reference, *, video_path, output_dir, auction_profile_path=None):
    """Optional extension of the existing rank profile; absence never changes card recognition."""
    config = (json.loads(Path(auction_profile_path).read_text(encoding="utf-8"))
              if auction_profile_path is not None else
              json.loads(Path(profile_path).read_text(encoding="utf-8")).get("auction"))
    if config is None:
        return None
    return AuctionObserver(config, reference, source_id=rank.sha256_file(Path(video_path)),
                           output_dir=output_dir)
