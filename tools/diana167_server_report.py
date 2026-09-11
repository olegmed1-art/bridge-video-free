#!/usr/bin/env python3
"""Server-only Diana video scan and PDF evidence report.

The driver never uses bridge/auction logic as a recognition weight.  It builds
the rank bank only from the director-confirmed gold-v2 pixel templates, scans
the video for complete stable deal layouts, retains one lossless screenshot per
accepted layout, and reports an uncalibrated template-similarity weight for
every one of the 52 assigned cards.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

from bridge_vision import bridgit_rank_layout as rank_layout


SOURCE_FILE_ID = "1XT8eYAunywdHAiNKNHhr-dO3kx7_Gyzf"
SOURCE_PARENT_ID = "1Fr-H2NgBKEpp3q_H4FzNmQwCV6bj2x6b"
FORBIDDEN_PARENT_ID = "16TVeL_595YU05H0VaRYzxo0IDJkfPAhg"
GOLD_FILE_ID = "1MGaz14wswn2XOgi4AqqvzT2sNFLdOink"
PROFILE_ID = "bridgit.desktop.1920x1010.gold-v2"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _runtime():
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    return cv2, np


def build_gold_profile(gold_zip: Path, root: Path) -> tuple[Path, Path, dict[str, Any]]:
    """Create a self-hashed reference canvas from the already reviewed glyphs."""
    cv2, np = _runtime()
    extract = root / "gold"
    extract.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(gold_zip) as archive:
        archive.extractall(extract)
    package = extract / "bridgit_gold_v2"
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    validation = json.loads((package / "validation.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "HUMAN_VERIFIED":
        raise RuntimeError("gold template package is not human verified")
    if validation.get("deck_bijection") != "PASS" or validation.get("two_variants_per_card") != "PASS":
        raise RuntimeError("gold template validation did not pass")
    if int(validation.get("rank_separation", {}).get("correct_rank_predictions", 0)) != 104:
        raise RuntimeError("gold rank leave-one-out validation is incomplete")

    source = next(item for item in manifest["sources"] if item["source_id"] == "deal_02")
    width, height = int(source["width"]), int(source["height"])
    canvas = np.full((height, width, 3), 255, dtype=np.uint8)
    slots = []
    for item in manifest["templates"]:
        if item["source_id"] != "deal_02":
            continue
        raw = (package / item["rank_path"]).read_bytes()
        glyph = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if glyph is None:
            raise RuntimeError("gold glyph could not be decoded")
        x, y = int(item["source_glyph"]["x"]), int(item["source_glyph"]["y"])
        gh, gw = glyph.shape[:2]
        if (gw, gh) != (19, 22):
            raise RuntimeError(f"unexpected gold glyph size: {(gw, gh)}")
        canvas[y : y + gh, x : x + gw] = cv2.cvtColor(glyph, cv2.COLOR_GRAY2BGR)
        slots.append({"card": item["card"], "x": x, "y": y})
    if len(slots) != 52 or len({item["card"] for item in slots}) != 52:
        raise RuntimeError("synthetic reference does not contain a complete deck")

    reference = root / "gold_v2_reference.png"
    if not cv2.imwrite(str(reference), canvas):
        raise RuntimeError("reference image write failed")
    reference_sha = sha256_file(reference)
    profile: dict[str, Any] = {
        "schema": rank_layout.PROFILE_SCHEMA,
        "human_verified": True,
        "profile_id": PROFILE_ID,
        "reference_frame_sha256": reference_sha,
        "verification": {
            "method": "HUMAN_LABEL_REVIEW",
            "reviewer_id": str(manifest["reviewer_id"]),
            "verified_at": str(manifest["verified_at"]),
            "reference_frame_sha256": reference_sha,
        },
        "frame_size": {"width": width, "height": height},
        "ordering": {"suits": list(rank_layout.SUITS), "ranks": list(rank_layout.RANKS)},
        "template_slots": slots,
        "geometry": {
            "anchors": {
                "N": {"H": {"x": 355, "y": 28}, "C": {"x": 495, "y": 28}, "D": {"x": 709, "y": 28}, "S": {"x": 920, "y": 28}},
                "E": {suit: {"x": 1320, "y": y} for suit, y in zip("HCDS", (312, 356, 400, 444))},
                "S": {"H": {"x": 353, "y": 813}, "C": {"x": 595, "y": 813}, "D": {"x": 804, "y": 813}, "S": {"x": 953, "y": 813}},
                "W": {suit: {"x": 19, "y": y} for suit, y in zip("HCDS", (312, 356, 400, 444))},
            },
            "horizontal_search": {
                "N": {"x_min": 300, "x_max": 1050, "y": 28},
                "S": {"x_min": 300, "x_max": 1050, "y": 813},
            },
            "vertical_search": {
                "W": {"x_min": 10, "x_max": 145, "edge_x": 19},
                "E": {"x_min": 1180, "x_max": 1335, "edge_x": 1320},
            },
            "interface_anchor": None,
        },
        "gates": {
            "glyph_width": 19,
            "glyph_height": 22,
            "local_registration_px": 2,
            "binary_threshold": 200,
            "min_template_score": 0.40,
            "min_peak_score": 0.70,
            "min_peak_prominence": 0.03,
            "min_rank_ink_fraction": 0.12,
            "min_assignment_margin": 0.06,
            "min_independent_frames": 2,
        },
        "gold": {
            "drive_file_id": GOLD_FILE_ID,
            "manifest_sha256": sha256_file(package / "manifest.json"),
            "template_set_sha256": json.loads((package / "integrity.json").read_text(encoding="utf-8"))["template_set_sha256"],
            "bridge_logic_weighting": False,
        },
    }
    profile["profile_sha256"] = canonical_hash(profile)
    rank_layout.parse_profile(profile)
    profile_path = root / "profile.json"
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return reference, profile_path, profile


def _full_geometry(image: Any, bank: Any, profile: Any) -> tuple[bool, str]:
    lengths: dict[str, dict[str, int]] = {}
    for seat in ("N", "S"):
        lengths[seat], found = rank_layout._horizontal_geometry(image, seat, profile)
        if not found or sum(lengths[seat].values()) != 13:
            return False, f"{seat}_horizontal"
    side = rank_layout._side_lengths([image], bank, profile)
    lengths["W"], lengths["E"] = side["W"], side["E"]
    if any(sum(lengths[seat].values()) != 13 for seat in rank_layout.SEATS):
        return False, "seat_total"
    if any(sum(lengths[seat][suit] for seat in rank_layout.SEATS) != 13 for suit in rank_layout.SUITS):
        return False, "suit_total"
    return True, "full"


def _registered_game_frame(image: Any, bank: Any, profile: Any, preferred_y: int | None = None) -> tuple[Any | None, int | None, str]:
    """Find the 1920x1010 game viewport inside a taller recording, from pixels."""
    height, width = image.shape[:2]
    if width != profile.width or height < profile.height or height > profile.height + 120:
        return None, None, f"frame_size_{width}x{height}"
    extra = height - profile.height
    offsets = []
    for value in (preferred_y, extra, 0):
        if value is not None and value not in offsets:
            offsets.append(value)
    last_reason = "viewport_not_found"
    for y0 in offsets:
        cropped = image[y0 : y0 + profile.height, : profile.width]
        full, reason = _full_geometry(cropped, bank, profile)
        if full:
            return cropped, y0, "full"
        last_reason = reason
    return None, None, last_reason


def _frame_at(capture: Any, timestamp_ms: int) -> Any | None:
    cv2, _ = _runtime()
    capture.set(cv2.CAP_PROP_POS_MSEC, float(timestamp_ms))
    ok, frame = capture.read()
    return frame if ok else None


def _card_windows(suit: str, y_offset: int) -> dict[str, tuple[int, int, int, int]]:
    top_x = {"H": (300, 520), "C": (450, 720), "D": (660, 900), "S": (850, 1060)}[suit]
    side_y = {"H": (285, 350), "C": (330, 395), "D": (375, 440), "S": (420, 490)}[suit]
    return {
        "N": (top_x[0], 5 + y_offset, top_x[1], 100 + y_offset),
        "E": (1160, side_y[0] + y_offset, 1360, side_y[1] + y_offset),
        "S": (top_x[0], 775 + y_offset, top_x[1], 900 + y_offset),
        "W": (0, side_y[0] + y_offset, 175, side_y[1] + y_offset),
    }


def _load_card_templates(package: Path, manifest: dict[str, Any]) -> dict[str, list[Any]]:
    cv2, np = _runtime()
    result: dict[str, list[Any]] = defaultdict(list)
    for item in manifest["templates"]:
        path = package / item["path"]
        if sha256_file(path) != item["sha256"]:
            raise RuntimeError("gold card template hash mismatch")
        image = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.shape[:2] != (40, 19):
            raise RuntimeError("gold card template size mismatch")
        result[item["card"]].append(image)
    if set(result) != {rank + suit for rank in rank_layout.RANKS for suit in rank_layout.SUITS}:
        raise RuntimeError("gold card template deck is incomplete")
    if any(len(values) != 2 for values in result.values()):
        raise RuntimeError("gold card template variants are incomplete")
    return dict(result)


def _match_card_by_seat(image: Any, card: str, variants: list[Any], y_offset: int) -> dict[str, dict[str, Any]]:
    cv2, _ = _runtime()
    result: dict[str, dict[str, Any]] = {}
    for seat, (x0, y0, x1, y1) in _card_windows(card[1], y_offset).items():
        region = image[y0:y1, x0:x1]
        best = {"score": -2.0, "seat": seat, "x": -1, "y": -1}
        for template in variants:
            response = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
            _, score, _, location = cv2.minMaxLoc(response)
            if float(score) > best["score"]:
                best = {"score": float(score), "seat": seat, "x": x0 + int(location[0]), "y": y0 + int(location[1])}
        result[seat] = best
    return result


def _match_card(image: Any, card: str, variants: list[Any], y_offset: int) -> dict[str, Any]:
    return max(_match_card_by_seat(image, card, variants, y_offset).values(), key=lambda item: item["score"])


def _gate_offset(image: Any, templates: dict[str, list[Any]], offsets: list[int]) -> tuple[int | None, dict[str, Any]]:
    gate_cards = ("AH", "AC", "AD", "AS", "KH", "KC", "KD", "KS")
    candidates = []
    for offset in offsets:
        matches = {card: _match_card(image, card, templates[card], offset) for card in gate_cards}
        passed = sum(item["score"] >= 0.58 for item in matches.values())
        score = float(median(item["score"] for item in matches.values()))
        candidates.append((passed, score, -offset, offset, matches))
    passed, score, _, offset, matches = max(candidates)
    if passed < 7 or score < 0.62:
        return None, {"gate_cards_passed": passed, "gate_median": round(score, 6)}
    return offset, matches


def _full_template_layout(image: Any, templates: dict[str, list[Any]], y_offset: int) -> tuple[dict[str, Any] | None, str]:
    started = time.perf_counter()
    print(json.dumps({"full_layout_phase": "start", "y_offset": y_offset}), flush=True)
    cv2, np_runtime = _runtime()
    import numpy as np  # type: ignore
    from scipy.optimize import linear_sum_assignment  # type: ignore

    cards = sorted(templates, key=lambda card: (rank_layout.SUITS.index(card[1]), rank_layout.RANKS.index(card[0])))
    score_maps: dict[str, dict[str, Any]] = defaultdict(dict)
    windows: dict[tuple[str, str], tuple[int, int, int, int]] = {}
    for suit in rank_layout.SUITS:
        for seat, window in _card_windows(suit, y_offset).items():
            windows[(seat, suit)] = window
    for card in cards:
        for seat, (x0, y0, x1, y1) in _card_windows(card[1], y_offset).items():
            region = image[y0:y1, x0:x1]
            variant_maps = [cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED) for template in templates[card]]
            score_maps[card][seat] = np_runtime.maximum(variant_maps[0], variant_maps[1])
    print(json.dumps({"full_layout_phase": "score_maps", "seconds": round(time.perf_counter() - started, 3)}), flush=True)

    seat_slots = [seat for seat in rank_layout.SEATS for _ in range(13)]
    quick_scores = {
        card: {seat: float(cv2.minMaxLoc(score_maps[card][seat])[1]) for seat in rank_layout.SEATS}
        for card in cards
    }
    quick_costs = np.asarray([[-quick_scores[card][seat] for seat in seat_slots] for card in cards], dtype=np.float64)
    quick_rows, quick_columns = linear_sum_assignment(quick_costs)
    quick_assigned = [-float(quick_costs[row, column]) for row, column in zip(quick_rows, quick_columns)]
    print(json.dumps({"full_layout_phase": "quick_assignment", "seconds": round(time.perf_counter() - started, 3), "minimum": round(min(quick_assigned), 4), "median": round(float(median(quick_assigned)), 4)}), flush=True)
    if min(quick_assigned) < 0.55 or float(median(quick_assigned)) < 0.68:
        return None, f"template_weight_gate_min{int(min(quick_assigned) * 20):02d}_med{int(float(median(quick_assigned)) * 20):02d}"

    slots: list[dict[str, Any]] = []
    for suit in rank_layout.SUITS:
        suit_cards = [card for card in cards if card[1] == suit]
        for seat in rank_layout.SEATS:
            x0, y0, _, _ = windows[(seat, suit)]
            aggregate = np_runtime.maximum.reduce([score_maps[card][seat] for card in suit_cards])
            remaining = aggregate.copy()
            for _ in range(20):
                _, strength, _, (x, y) = cv2.minMaxLoc(remaining)
                if float(strength) < 0.40:
                    break
                slots.append({"seat": seat, "suit": suit, "x": x0 + int(x), "y": y0 + int(y), "local_x": int(x), "local_y": int(y), "strength": strength})
                xa, xb = max(0, int(x) - 11), min(remaining.shape[1], int(x) + 12)
                ya, yb = max(0, int(y) - 11), min(remaining.shape[0], int(y) + 12)
                remaining[ya:yb, xa:xb] = -2.0
    if any(sum(1 for slot in slots if slot["suit"] == suit) < 13 for suit in rank_layout.SUITS):
        return None, "candidate_slot_gate"
    print(json.dumps({"full_layout_phase": "slots", "seconds": round(time.perf_counter() - started, 3), "slots": len(slots)}), flush=True)

    costs = np.full((52, len(slots)), 4.0, dtype=np.float64)
    for row, card in enumerate(cards):
        for column, slot in enumerate(slots):
            if slot["suit"] != card[1]:
                continue
            score = float(score_maps[card][slot["seat"]][slot["local_y"], slot["local_x"]])
            costs[row, column] = -score
    rows, columns = linear_sum_assignment(costs)
    if list(rows) != list(range(52)):
        return None, "global_assignment_gate"
    matches = {}
    for row, column in zip(rows, columns):
        card, slot = cards[row], slots[column]
        matches[card] = {"score": float(-costs[row, column]), "seat": slot["seat"], "x": slot["x"], "y": slot["y"]}
    scores = [item["score"] for item in matches.values()]
    if min(scores) < 0.55 or float(median(scores)) < 0.68:
        return None, f"template_weight_gate_min{int(min(scores) * 20):02d}_med{int(float(median(scores)) * 20):02d}"
    counts = Counter(item["seat"] for item in matches.values())
    if counts != Counter({seat: 13 for seat in rank_layout.SEATS}):
        return None, "seat_count_gate"
    points = [(card, item["x"], item["y"]) for card, item in matches.items()]
    for index, (first_card, first_x, first_y) in enumerate(points):
        for second_card, second_x, second_y in points[index + 1 :]:
            if abs(first_x - second_x) < 8 and abs(first_y - second_y) < 8:
                return None, "duplicate_location_gate"
    for seat in rank_layout.SEATS:
        for suit in rank_layout.SUITS:
            cards = [(card, matches[card]["x"]) for card in matches if card[1] == suit and matches[card]["seat"] == seat]
            ordered = [rank_layout.RANKS.index(card[0]) for card, _ in sorted(cards, key=lambda value: value[1])]
            if ordered != sorted(ordered):
                return None, "screen_rank_order_gate"
    hands = {seat: {suit: [] for suit in rank_layout.SUITS} for seat in rank_layout.SEATS}
    for card, item in matches.items():
        hands[item["seat"]][card[1]].append(card[0])
    for seat in rank_layout.SEATS:
        for suit in rank_layout.SUITS:
            hands[seat][suit].sort(key=rank_layout.RANKS.index)
    return {"matches": matches, "hands": hands, "minimum": min(scores), "median": float(median(scores))}, "full"


def _fuse_template_layouts(first: dict[str, Any], second: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    rows = []
    for card in sorted(first["matches"], key=lambda value: (rank_layout.SUITS.index(value[1]), rank_layout.RANKS.index(value[0]))):
        a, b = first["matches"][card], second["matches"][card]
        if a["seat"] != b["seat"] or abs(a["x"] - b["x"]) > 5 or abs(a["y"] - b["y"]) > 5:
            return None, "temporal_position_gate"
        values = [float(a["score"]), float(b["score"])]
        rows.append({
            "seat": a["seat"],
            "card": card,
            "weight_median": round(float(median(values)), 6),
            "weight_min": round(float(min(values)), 6),
            "observations": 2,
            "confidence_kind": "TEMPLATE_SIMILARITY_UNCALIBRATED",
        })
    return {"hands": first["hands"], "weights": sorted(rows, key=lambda item: (rank_layout.SEATS.index(item["seat"]), rank_layout.SUITS.index(item["card"][1]), rank_layout.RANKS.index(item["card"][0]))), "minimum": min(item["weight_min"] for item in rows), "median": float(median(item["weight_median"] for item in rows))}, "full"


def _hands_key(hands: dict[str, Any]) -> str:
    return canonical_hash({seat: {suit: "".join(hands[seat][suit]) for suit in rank_layout.SUITS} for seat in rank_layout.SEATS})


def _card_weights(result: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for item in result.get("_visual_observations", []):
        grouped[(item["seat"], item["rank"] + item["suit"])].append(float(item["confidence"]))
    rows = []
    for (seat, card), values in sorted(grouped.items(), key=lambda item: (rank_layout.SEATS.index(item[0][0]), rank_layout.SUITS.index(item[0][1][1]), rank_layout.RANKS.index(item[0][1][0]))):
        rows.append({
            "seat": seat,
            "card": card,
            "weight_median": round(float(median(values)), 6),
            "weight_min": round(float(min(values)), 6),
            "observations": len(values),
            "confidence_kind": "TEMPLATE_SIMILARITY_UNCALIBRATED",
        })
    return rows


def scan_video(video: Path, gold_zip: Path, output: Path, scan_ms: int, max_deals: int) -> dict[str, Any]:
    cv2, _ = _runtime()
    work = output / "work"
    work.mkdir(parents=True, exist_ok=True)
    screenshots = output / "screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)
    _, _, raw_profile = build_gold_profile(gold_zip, work)
    package = work / "gold" / "bridgit_gold_v2"
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    templates = _load_card_templates(package, manifest)

    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise RuntimeError("video decoder could not open source")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration_ms = round(frame_count * 1000 / fps) if fps > 0 else 0
    source = {
        "drive_file_id": SOURCE_FILE_ID,
        "drive_parent_id": SOURCE_PARENT_ID,
        "forbidden_parent_used": False,
        "filename": video.name,
        "size_bytes": video.stat().st_size,
        "sha256": sha256_file(video),
        "fps": fps,
        "frame_count": frame_count,
        "width": width,
        "height": height,
        "duration_ms": duration_ms,
    }
    rejections: Counter[str] = Counter()
    recognized: list[dict[str, Any]] = []
    last_attempt_ms = -10**9
    registration_offsets: Counter[int] = Counter()
    try:
        profile_width = int(raw_profile["frame_size"]["width"])
        profile_height = int(raw_profile["frame_size"]["height"])
        if width != profile_width or height < profile_height or height > profile_height + 120:
            rejections[f"frame_size_{width}x{height}"] += 1
        else:
            offsets = list(dict.fromkeys((height - profile_height, 0)))
            timestamp_ms = 0
            sampled = 0
            while timestamp_ms < duration_ms and len(recognized) < max_deals * 8:
                sampled += 1
                if sampled % 10 == 0:
                    print(json.dumps({"progress_timestamp": format_timestamp(timestamp_ms), "recognized_candidates": len(recognized), "registration_offsets": dict(registration_offsets), "top_rejections": rejections.most_common(6)}, ensure_ascii=False), flush=True)
                first_original = _frame_at(capture, timestamp_ms)
                if first_original is None:
                    rejections["decode"] += 1
                    timestamp_ms += scan_ms
                    continue
                viewport_y, gate = _gate_offset(first_original, templates, offsets)
                if viewport_y is None:
                    rejections[f"gold_gate_{gate['gate_cards_passed']}"] += 1
                    timestamp_ms += scan_ms
                    continue
                registration_offsets[viewport_y] += 1
                if timestamp_ms - last_attempt_ms < 15000:
                    timestamp_ms += scan_ms
                    continue
                second_ms = min(timestamp_ms + 700, max(timestamp_ms + 1, duration_ms - 1))
                second_original = _frame_at(capture, second_ms)
                if second_original is None:
                    rejections["second_decode"] += 1
                    timestamp_ms += scan_ms
                    continue
                first_layout, first_reason = _full_template_layout(first_original, templates, viewport_y)
                if first_layout is None:
                    rejections[first_reason] += 1
                    timestamp_ms += scan_ms
                    continue
                second_viewport_y, second_gate = _gate_offset(second_original, templates, [viewport_y])
                if second_viewport_y != viewport_y:
                    rejections[f"second_gold_gate_{second_gate['gate_cards_passed']}"] += 1
                    timestamp_ms += scan_ms
                    continue
                second_layout, second_reason = _full_template_layout(second_original, templates, viewport_y)
                if second_layout is None:
                    rejections[f"second_{second_reason}"] += 1
                    timestamp_ms += scan_ms
                    continue
                fused, fused_reason = _fuse_template_layouts(first_layout, second_layout)
                if fused is None:
                    rejections[fused_reason] += 1
                    timestamp_ms += scan_ms
                    continue
                last_attempt_ms = timestamp_ms
                key = _hands_key(fused["hands"])
                screenshot = screenshots / f"deal_{len(recognized) + 1:03d}_{timestamp_ms:010d}.png"
                if not cv2.imwrite(str(screenshot), first_original):
                    raise RuntimeError("deal screenshot write failed")
                recognized.append({
                    "timestamp_ms": timestamp_ms,
                    "timestamp": format_timestamp(timestamp_ms),
                    "status": "SHADOW_FULL_LAYOUT_CANDIDATE",
                    "layout_sha256": key,
                    "screenshot": str(screenshot.relative_to(output)),
                    "screenshot_sha256": sha256_file(screenshot),
                    "game_viewport": {"x": 0, "y": viewport_y, "width": profile_width, "height": profile_height},
                    "hands": fused["hands"],
                    "integrity": {"cards": 52, "unique": 52, "seat_counts": {seat: 13 for seat in rank_layout.SEATS}},
                    "evidence": {"minimum_assigned_score": round(fused["minimum"], 6), "median_assigned_score": round(fused["median"], 6), "independent_frames": 2, "confidence_kind": "TEMPLATE_SIMILARITY_UNCALIBRATED"},
                    "weights": fused["weights"],
                    "receipt": {"method": "104_HUMAN_VERIFIED_CARD_CORNERS_PLUS_TEMPORAL_POSITION_CONSENSUS", "bridge_logic_weighting": False},
                })
                timestamp_ms += scan_ms
    finally:
        capture.release()

    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in recognized:
        by_key[item["layout_sha256"]].append(item)
    deals = []
    for values in sorted(by_key.values(), key=lambda xs: min(item["timestamp_ms"] for item in xs)):
        best = max(values, key=lambda item: (float(item.get("evidence", {}).get("minimum_assigned_score", -1)), -item["timestamp_ms"]))
        best = dict(best)
        best["server_confirmations"] = len(values)
        best["confirmation_timestamps_ms"] = [item["timestamp_ms"] for item in values]
        deals.append(best)
    deals = deals[:max_deals]
    keep = {item["screenshot"] for item in deals}
    for path in screenshots.glob("*.png"):
        if str(path.relative_to(output)) not in keep:
            path.unlink()

    return {
        "schema": "diana167-card-report/v2",
        "job_id": os.environ.get("GITHUB_RUN_ID") or hashlib.sha256((source["sha256"] + raw_profile["profile_sha256"]).encode()).hexdigest()[:16],
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "runtime_commit": os.environ.get("GITHUB_SHA", "unknown"),
        "result_scope": "SHADOW_ONLY",
        "canonical_promotion_allowed": False,
        "bridge_logic_weighting": False,
        "server_side_processing": True,
        "source": source,
        "profile": {
            "profile_id": raw_profile["profile_id"],
            "profile_sha256": raw_profile["profile_sha256"],
            "gold_drive_file_id": GOLD_FILE_ID,
            "gold_template_set_sha256": raw_profile["gold"]["template_set_sha256"],
        },
        "sampling": {"scan_interval_ms": scan_ms, "policy": "PIXEL_REGISTERED_FULL_LAYOUT_GATE_THEN_TWO_FRAME_CONSENSUS", "registration_y_offsets": dict(sorted(registration_offsets.items()))},
        "summary": {"deals": len(deals), "recognized_candidates": len(recognized), "rejections": dict(sorted(rejections.items()))},
        "deals": deals,
    }


def format_timestamp(ms: int) -> str:
    seconds = ms // 1000
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def suit_text(suit: str) -> str:
    return {"H": "♥", "C": "♣", "D": "♦", "S": "♠"}[suit]


def card_text(card: str) -> str:
    rank = "10" if card[0] == "T" else card[0]
    return rank + suit_text(card[1])


def build_pdf(data: dict[str, Any], output_root: Path, target: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    regular = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    pdfmetrics.registerFont(TTFont("DejaVu", regular))
    pdfmetrics.registerFont(TTFont("DejaVu-Bold", bold))
    styles = getSampleStyleSheet()
    title = ParagraphStyle("TitleRu", parent=styles["Title"], fontName="DejaVu-Bold", fontSize=19, leading=23, textColor=colors.HexColor("#17324D"), alignment=TA_LEFT)
    h2 = ParagraphStyle("H2Ru", parent=styles["Heading2"], fontName="DejaVu-Bold", fontSize=13, leading=16, textColor=colors.HexColor("#17324D"))
    body = ParagraphStyle("BodyRu", parent=styles["BodyText"], fontName="DejaVu", fontSize=8.5, leading=11)
    small = ParagraphStyle("SmallRu", parent=body, fontSize=7, leading=9)
    card_style = ParagraphStyle("Card", parent=small, fontName="DejaVu-Bold", alignment=TA_CENTER)
    page = landscape(A4)

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("DejaVu", 7)
        canvas.setFillColor(colors.HexColor("#667788"))
        canvas.drawString(12 * mm, 7 * mm, "Диана 167 · серверный shadow-отчёт · стратегическая бриджевая логика в весах отключена")
        canvas.drawRightString(page[0] - 12 * mm, 7 * mm, f"Страница {doc.page}")
        canvas.restoreState()

    story: list[Any] = []
    story.append(Paragraph("Диана 167: распознанные сдачи и веса всех карт", title))
    story.append(Spacer(1, 4 * mm))
    src = data["source"]
    summary = data["summary"]
    cover = [
        ["Файл", src["filename"]],
        ["Drive ID", src["drive_file_id"]],
        ["Разрешённая папка", src["drive_parent_id"]],
        ["Запрещённая папка использована", "нет"],
        ["SHA-256 видео", src["sha256"]],
        ["Длительность", format_timestamp(src["duration_ms"])],
        ["Найдено уникальных сдач", str(summary["deals"])],
        ["Режим", "серверный, shadow-only; без записи в канон"],
    ]
    table = Table([[Paragraph(f"<b>{a}</b>", body), Paragraph(str(b), body)] for a, b in cover], colWidths=[55 * mm, 190 * mm])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCD5DF")), ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EEF3F8")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story.append(table)
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph("Как читать веса", h2))
    story.append(Paragraph("Вес — это сходство пиксельного глифа с проверенным эталоном, а не вероятность правильности. Для каждой карты приведены медиана и минимум по двум независимым серверным кадрам. Торговля, известные руки и стратегическая бриджевая логика вес не повышают и не понижают.", body))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("N/E/S/W в отчёте означают экранные позиции: верх / право / низ / лево. Поворот реальных мест за столом не используется как скрытая подсказка распознавателю.", body))
    story.append(PageBreak())

    if not data["deals"]:
        story.append(Paragraph("Сдачи не подтверждены", h2))
        story.append(Paragraph("Профиль сработал fail-closed: ни один набор кадров не прошёл все геометрические и пиксельные проверки. Ни карты, ни веса не были придуманы.", body))
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph("Технические причины: " + json.dumps(summary["rejections"], ensure_ascii=False), small))

    for number, deal in enumerate(data["deals"], 1):
        story.append(Paragraph(f"Сдача {number} · {deal['timestamp']}", title))
        evidence = deal.get("evidence") or {}
        story.append(Paragraph(f"Статус: {deal['status']} · подтверждений: {deal['server_confirmations']} · минимальный вес: {evidence.get('minimum_assigned_score', '—')} · медианный вес: {evidence.get('median_assigned_score', '—')}", body))
        story.append(Spacer(1, 2 * mm))
        image_path = output_root / deal["screenshot"]
        picture = Image(str(image_path))
        max_w, max_h = 260 * mm, 142 * mm
        scale = min(max_w / picture.imageWidth, max_h / picture.imageHeight)
        picture.drawWidth = picture.imageWidth * scale
        picture.drawHeight = picture.imageHeight * scale
        story.append(picture)
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph(f"Скрин SHA-256: {deal['screenshot_sha256']}", small))
        story.append(PageBreak())

        story.append(Paragraph(f"Сдача {number}: расклад и веса", title))
        hands_rows = []
        for seat in rank_layout.SEATS:
            cells = [Paragraph(f"<b>{seat}</b>", body)]
            for suit in rank_layout.SUITS:
                ranks = "".join(deal["hands"][seat][suit]).replace("T", "10") or "—"
                cells.append(Paragraph(f"{suit_text(suit)} {ranks}", body))
            hands_rows.append(cells)
        hands_table = Table([[Paragraph("Позиция", body)] + [Paragraph(suit_text(s), card_style) for s in rank_layout.SUITS]] + hands_rows, colWidths=[24 * mm] + [52 * mm] * 4)
        hands_table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CCD5DF")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324D")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        story.append(hands_table)
        story.append(Spacer(1, 4 * mm))

        weights = deal["weights"]
        halves = [weights[:26], weights[26:]]
        blocks = []
        for half in halves:
            rows = [[Paragraph("Поз.", small), Paragraph("Карта", small), Paragraph("Медиана", small), Paragraph("Минимум", small)]]
            for item in half:
                rows.append([Paragraph(item["seat"], small), Paragraph(card_text(item["card"]), card_style), Paragraph(f"{item['weight_median']:.4f}", small), Paragraph(f"{item['weight_min']:.4f}", small)])
            block = Table(rows, colWidths=[14 * mm, 20 * mm, 25 * mm, 25 * mm], repeatRows=1)
            block.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D6DEE7")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF3F8")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("TOPPADDING", (0, 0), (-1, -1), 1.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6)]))
            blocks.append(block)
        paired = Table([[blocks[0], blocks[1]]], colWidths=[90 * mm, 90 * mm], hAlign="LEFT")
        paired.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 6)]))
        story.append(paired)
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph("Тип веса: TEMPLATE_SIMILARITY_UNCALIBRATED. Инвариант колоды и экранный порядок используются только для целостности расклада; логика торговли и розыгрыша в весах отключена.", small))
        if number != len(data["deals"]):
            story.append(PageBreak())

    tmp = target.with_suffix(".base.pdf")
    doc = SimpleDocTemplate(str(tmp), pagesize=page, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=10 * mm, bottomMargin=12 * mm, title="Диана 167 — карты и веса", author="Bridge Video server recognizer")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    analysis_json = output_root / "master_analysis.json"
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(str(tmp))
    writer = PdfWriter()
    for page_item in reader.pages:
        writer.add_page(page_item)
    writer.add_attachment("master_analysis.json", analysis_json.read_bytes())
    with target.open("wb") as handle:
        writer.write(handle)
    tmp.unlink()


def validate_pdf(path: Path, expected_deals: int, root: Path) -> dict[str, Any]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = len(reader.pages)
    expected_minimum = 2 if expected_deals == 0 else 1 + expected_deals * 2
    if pages < expected_minimum:
        raise RuntimeError(f"PDF has too few pages: {pages} < {expected_minimum}")
    if "master_analysis.json" not in list(reader.attachments):
        raise RuntimeError("PDF is missing embedded master_analysis.json")
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    for needle in ("Диана 167", "стратегическая бриджевая логика", "Drive ID"):
        if needle not in text:
            raise RuntimeError(f"PDF text validation failed: {needle}")
    render = root / "render-check"
    render.mkdir(exist_ok=True)
    subprocess.run(["pdftoppm", "-f", "1", "-l", str(min(pages, 3)), "-png", "-r", "110", str(path), str(render / "page")], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rendered = sorted(render.glob("page-*.png"))
    if not rendered or any(item.stat().st_size < 10_000 for item in rendered):
        raise RuntimeError("PDF render validation failed")
    return {"pages": pages, "bytes": path.stat().st_size, "sha256": sha256_file(path), "rendered_pages_checked": len(rendered)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--gold-zip", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--scan-ms", type=int, default=5000)
    parser.add_argument("--max-deals", type=int, default=40)
    args = parser.parse_args()
    if not 2000 <= args.scan_ms <= 30000:
        raise SystemExit("scan-ms must be in 2000..30000")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    data = scan_video(args.video, args.gold_zip, args.output_dir, args.scan_ms, args.max_deals)
    analysis = args.output_dir / "master_analysis.json"
    analysis.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if not data["deals"]:
        raise RuntimeError("no complete deal passed the fail-closed server gates")
    pdf = args.output_dir / "Диана 167 — карты и веса — server v2.pdf"
    build_pdf(data, args.output_dir, pdf)
    validation = validate_pdf(pdf, len(data["deals"]), args.output_dir)
    validation_path = args.output_dir / "validation.json"
    validation_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": "PASS",
        "deals": len(data["deals"]),
        "source_frame_size": [data["source"]["width"], data["source"]["height"]],
        "source_duration_ms": data["source"]["duration_ms"],
        "rejections": data["summary"]["rejections"],
        "pdf": str(pdf),
        "validation": validation,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
