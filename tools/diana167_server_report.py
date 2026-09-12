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
import math
import os
import re
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
CHALLENGER_PROFILE_ID = "bridgit.desktop.1920x1010.gold-v2-cross-deal-challenger-v3"
LOW_WEIGHT_THRESHOLD = 0.80
CLUSTER_OWNER_DISTANCE = 6
MENTION_DEAL_MAX_DISTANCE_MS = 10 * 60 * 1000


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
    from scipy.optimize import Bounds, LinearConstraint, linear_sum_assignment, milp  # type: ignore
    from scipy.sparse import lil_matrix  # type: ignore

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
    unique_slots: list[dict[str, Any]] = []
    for slot in sorted(slots, key=lambda item: item["strength"], reverse=True):
        if any(
            existing["seat"] == slot["seat"]
            and abs(existing["x"] - slot["x"]) < 8
            and abs(existing["y"] - slot["y"]) < 8
            for existing in unique_slots
        ):
            continue
        unique_slots.append(slot)
    slots = unique_slots
    def eligible(card: str, slot: dict[str, Any]) -> tuple[int, int] | None:
        x0, y0, _, _ = windows[(slot["seat"], card[1])]
        local_x, local_y = int(slot["x"] - x0), int(slot["y"] - y0)
        shape = score_maps[card][slot["seat"]].shape
        if 0 <= local_x < shape[1] and 0 <= local_y < shape[0]:
            return local_x, local_y
        return None

    if any(sum(1 for slot in slots if eligible("A" + suit, slot) is not None) < 13 for suit in rank_layout.SUITS):
        return None, "candidate_slot_gate"
    print(json.dumps({"full_layout_phase": "slots", "seconds": round(time.perf_counter() - started, 3), "slots": len(slots)}), flush=True)

    edges: list[tuple[int, int, float]] = []
    for row, card in enumerate(cards):
        for column, slot in enumerate(slots):
            local = eligible(card, slot)
            if local is None:
                continue
            local_x, local_y = local
            score = float(score_maps[card][slot["seat"]][local_y, local_x])
            edges.append((row, column, score))
    constraint_rows = 52 + len(slots) + len(rank_layout.SEATS)
    matrix = lil_matrix((constraint_rows, len(edges)), dtype=np.float64)
    lower = np.full(constraint_rows, -np.inf, dtype=np.float64)
    upper = np.full(constraint_rows, np.inf, dtype=np.float64)
    for edge_index, (row, column, _) in enumerate(edges):
        matrix[row, edge_index] = 1.0
        matrix[52 + column, edge_index] = 1.0
        seat_index = rank_layout.SEATS.index(slots[column]["seat"])
        matrix[52 + len(slots) + seat_index, edge_index] = 1.0
    lower[:52] = upper[:52] = 1.0
    upper[52 : 52 + len(slots)] = 1.0
    lower[52 + len(slots) :] = upper[52 + len(slots) :] = 13.0
    solution = milp(
        c=np.asarray([-score for _, _, score in edges], dtype=np.float64),
        integrality=np.ones(len(edges), dtype=np.int32),
        bounds=Bounds(np.zeros(len(edges)), np.ones(len(edges))),
        constraints=LinearConstraint(matrix.tocsr(), lower, upper),
        options={"time_limit": 30.0},
    )
    if not solution.success or solution.x is None:
        return None, "global_assignment_gate"
    matches = {}
    for edge_index, selected in enumerate(solution.x):
        if selected < 0.5:
            continue
        row, column, score = edges[edge_index]
        card, slot = cards[row], slots[column]
        matches[card] = {"score": float(score), "seat": slot["seat"], "x": slot["x"], "y": slot["y"], "slot_column": column}
    if len(matches) != 52:
        return None, "global_assignment_card_count_gate"
    for seat in rank_layout.SEATS:
        for suit in rank_layout.SUITS:
            group_cards = sorted(
                [card for card in cards if card[1] == suit and matches[card]["seat"] == seat],
                key=lambda card: rank_layout.RANKS.index(card[0]),
            )
            group_slots = sorted(
                [slots[matches[card]["slot_column"]] for card in group_cards],
                key=lambda slot: slot["x"],
            )
            for card, slot in zip(group_cards, group_slots):
                local = eligible(card, slot)
                if local is None:
                    return None, "ordered_slot_eligibility_gate"
                local_x, local_y = local
                matches[card] = {
                    "score": float(score_maps[card][seat][local_y, local_x]),
                    "seat": seat,
                    "x": slot["x"],
                    "y": slot["y"],
                }
    scores = [item["score"] for item in matches.values()]
    print(json.dumps({"full_layout_phase": "constrained_assignment", "seconds": round(time.perf_counter() - started, 3), "minimum": round(min(scores), 4), "median": round(float(median(scores)), 4)}), flush=True)
    if min(scores) < 0.12 or float(median(scores)) < 0.95:
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
                print(json.dumps({"full_layout_phase": "rank_order_reject", "seat": seat, "suit": suit, "observed": ordered}), flush=True)
                return None, f"screen_rank_order_gate_{seat}_{suit}"
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
        if a["seat"] != b["seat"]:
            return None, "temporal_seat_gate"
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


def _single_template_layout(layout: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for card, item in layout["matches"].items():
        score = round(float(item["score"]), 6)
        rows.append({
            "seat": item["seat"],
            "card": card,
            "weight_median": score,
            "weight_min": score,
            "observations": 1,
            "confidence_kind": "TEMPLATE_SIMILARITY_UNCALIBRATED",
        })
    rows.sort(key=lambda item: (rank_layout.SEATS.index(item["seat"]), rank_layout.SUITS.index(item["card"][1]), rank_layout.RANKS.index(item["card"][0])))
    return {
        "hands": layout["hands"],
        "weights": rows,
        "minimum": min(item["weight_min"] for item in rows),
        "median": float(median(item["weight_median"] for item in rows)),
        "matches": layout["matches"],
    }


def _hands_key(hands: dict[str, Any]) -> str:
    return canonical_hash({seat: {suit: "".join(hands[seat][suit]) for suit in rank_layout.SUITS} for seat in rank_layout.SEATS})


def _owners(hands: dict[str, Any]) -> dict[str, str]:
    return {
        rank + suit: seat
        for seat in rank_layout.SEATS
        for suit in rank_layout.SUITS
        for rank in hands[seat][suit]
    }


def _owner_distance(first: dict[str, Any], second: dict[str, Any]) -> int:
    left, right = _owners(first), _owners(second)
    return sum(left.get(card) != right.get(card) for card in left)


def _cluster_recognized(recognized: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Merge small assignment jitter without merging genuinely different deals."""
    clusters: list[list[dict[str, Any]]] = []
    for item in sorted(recognized, key=lambda value: value["timestamp_ms"]):
        eligible = [
            cluster
            for cluster in clusters
            if min(_owner_distance(item["hands"], member["hands"]) for member in cluster)
            <= CLUSTER_OWNER_DISTANCE
        ]
        if not eligible:
            clusters.append([item])
            continue
        best = min(
            eligible,
            key=lambda cluster: min(
                _owner_distance(item["hands"], member["hands"]) for member in cluster
            ),
        )
        best.append(item)
    return clusters


def _card_crop(image: Any, match: dict[str, Any]) -> Any | None:
    x, y = int(match["x"]), int(match["y"])
    crop = image[y : y + 40, x : x + 19]
    return crop.copy() if crop.shape[:2] == (40, 19) else None


def _same_size_score(first: Any, second: Any) -> float:
    cv2, _ = _runtime()
    if first is None or second is None or first.shape != second.shape:
        return -2.0
    return float(cv2.matchTemplate(first, second, cv2.TM_CCOEFF_NORMED)[0, 0])


def _apply_cross_deal_challenger(
    clusters: list[list[dict[str, Any]]],
    deals: list[dict[str, Any]],
    output: Path,
) -> dict[str, Any]:
    """Add low-card variants only when a different deal validates the label.

    The source crop is never scored against its own cluster.  This prevents the
    trivial 1.0 score produced by comparing a frame with itself.
    """
    cv2, _ = _runtime()
    representatives = []
    for cluster_index, (cluster, deal) in enumerate(zip(clusters, deals)):
        image = cv2.imread(str(output / deal["screenshot"]), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError("challenger screenshot decode failed")
        representatives.append({"cluster": cluster_index, "deal": deal, "image": image})

    low_cards = sorted(
        {
            row["card"]
            for deal in deals
            for row in deal["weights"]
            if float(row["weight_median"]) < LOW_WEIGHT_THRESHOLD
        },
        key=lambda card: (rank_layout.SUITS.index(card[1]), rank_layout.RANKS.index(card[0])),
    )
    variants: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for card in low_cards:
        for source in representatives:
            source_crop = _card_crop(source["image"], source["deal"]["_matches"][card])
            if source_crop is None:
                continue
            validations = []
            for target in representatives:
                if target["cluster"] == source["cluster"]:
                    continue
                target_crop = _card_crop(target["image"], target["deal"]["_matches"][card])
                same = _same_size_score(source_crop, target_crop)
                competing = []
                for other in rank_layout.RANKS:
                    other_card = other + card[1]
                    if other_card == card:
                        continue
                    competing_crop = _card_crop(target["image"], target["deal"]["_matches"][other_card])
                    competing.append(_same_size_score(source_crop, competing_crop))
                margin = same - max(competing, default=-2.0)
                validations.append((same, margin, target["cluster"]))
            passing = [value for value in validations if value[0] >= 0.55 and value[1] >= 0.03]
            if not passing:
                continue
            variants[card].append({
                "image": source_crop,
                "source_cluster": source["cluster"],
                "source_timestamp_ms": source["deal"]["timestamp_ms"],
                "source_screenshot_sha256": source["deal"]["screenshot_sha256"],
                "validation_score": round(max(value[0] for value in passing), 6),
                "validation_margin": round(max(value[1] for value in passing), 6),
                "validated_on_clusters": sorted({value[2] for value in passing}),
                "sha256": hashlib.sha256(source_crop.tobytes()).hexdigest(),
            })

    used = 0
    improvements = []
    for target in representatives:
        cluster_index = target["cluster"]
        rows = {row["card"]: row for row in target["deal"]["weights"]}
        for card, candidates in variants.items():
            target_crop = _card_crop(target["image"], target["deal"]["_matches"][card])
            eligible = [item for item in candidates if item["source_cluster"] != cluster_index]
            if not eligible or target_crop is None:
                continue
            challenger_score = max(_same_size_score(target_crop, item["image"]) for item in eligible)
            row = rows[card]
            gold_score = float(row["weight_median"])
            row["weight_gold_v2"] = round(gold_score, 6)
            row["weight_challenger_v3"] = round(challenger_score, 6)
            row["weight_median"] = row["weight_min"] = round(max(gold_score, challenger_score), 6)
            row["confidence_kind"] = "MAX_GOLD_V2_AND_CROSS_DEAL_CHALLENGER_SIMILARITY_UNCALIBRATED"
            row["challenger_variant_count"] = len(eligible)
            if challenger_score > gold_score:
                improvements.append({
                    "deal": cluster_index + 1,
                    "card": card,
                    "before": round(gold_score, 6),
                    "after": round(challenger_score, 6),
                })
                used += 1
        values = [float(row["weight_median"]) for row in target["deal"]["weights"]]
        target["deal"]["evidence"]["minimum_assigned_score"] = round(min(values), 6)
        target["deal"]["evidence"]["median_assigned_score"] = round(float(median(values)), 6)

    serializable = {
        card: [
            {key: value for key, value in item.items() if key != "image"}
            for item in values
        ]
        for card, values in variants.items()
    }
    return {
        "profile_id": CHALLENGER_PROFILE_ID,
        "promotion_status": "SHADOW_NOT_CANONICAL",
        "leakage_control": "SOURCE_CLUSTER_EXCLUDED_FROM_SCORING",
        "low_weight_threshold": LOW_WEIGHT_THRESHOLD,
        "target_cards": low_cards,
        "validated_variant_cards": sorted(serializable),
        "validated_variant_count": sum(len(values) for values in serializable.values()),
        "applied_improvement_count": used,
        "variants": serializable,
        "improvements": improvements,
    }


_MENTION_WORD_RE = re.compile(r"[a-zа-я0-9]+", re.IGNORECASE)
_MENTION_RANKS = {
    "туз": "A", "туза": "A", "тузом": "A", "тузу": "A",
    "король": "K", "короля": "K", "королем": "K", "королю": "K",
    "дама": "Q", "дамы": "Q", "дамой": "Q", "даму": "Q",
    "валет": "J", "валета": "J", "валетом": "J", "валету": "J",
    "десятка": "T", "десятку": "T", "десяткой": "T",
    "девятка": "9", "девятку": "9", "девяткой": "9",
    "восьмерка": "8", "восьмерку": "8", "восьмеркой": "8",
    "семерка": "7", "семерку": "7", "семеркой": "7",
    "шестерка": "6", "шестерку": "6", "шестеркой": "6",
    "пятерка": "5", "пятерку": "5", "пятеркой": "5",
    "четверка": "4", "четверку": "4", "четверкой": "4",
    "тройка": "3", "тройку": "3", "тройкой": "3",
    "двойка": "2", "двойку": "2", "двойкой": "2",
    "ace": "A", "king": "K", "queen": "Q", "jack": "J", "ten": "T",
}
_MENTION_SUITS = {
    "черва": "H", "червы": "H", "червей": "H", "червовая": "H", "червовый": "H",
    "червовую": "H", "червовой": "H", "червовая": "H", "червового": "H",
    "треф": "C", "трефа": "C", "трефы": "C", "трефовая": "C", "трефовый": "C",
    "трефовую": "C", "трефовой": "C", "крест": "C", "крести": "C", "крестовая": "C",
    "бубен": "D", "бубны": "D", "бубна": "D", "бубновая": "D", "бубновый": "D",
    "бубновую": "D", "бубновой": "D", "бубнового": "D",
    "пик": "S", "пики": "S", "пиковая": "S", "пиковый": "S", "пиковую": "S",
    "пиковой": "S", "пикового": "S", "spade": "S", "spades": "S",
    "heart": "H", "hearts": "H", "club": "C", "clubs": "C", "diamond": "D", "diamonds": "D",
}


def _segment_card_mentions(text: str) -> list[str]:
    tokens = _MENTION_WORD_RE.findall(str(text).lower().replace("ё", "е"))
    result = []
    suit_positions = [(index, _MENTION_SUITS[token]) for index, token in enumerate(tokens) if token in _MENTION_SUITS]
    for index, token in enumerate(tokens):
        rank = _MENTION_RANKS.get(token)
        if rank is None:
            continue
        nearby = sorted(
            ((abs(index - suit_index), suit_index, suit) for suit_index, suit in suit_positions if abs(index - suit_index) <= 4),
            key=lambda item: (item[0], item[1]),
        )
        if not nearby:
            continue
        nearest_distance = nearby[0][0]
        nearest_suits = {item[2] for item in nearby if item[0] == nearest_distance}
        if len(nearest_suits) == 1:
            result.append(rank + next(iter(nearest_suits)))
    return result


def _seat_for_mention(card: str, timestamp_ms: int, deals: list[dict[str, Any]]) -> tuple[str, str, list[int]]:
    candidates = []
    for deal_number, deal in enumerate(deals, 1):
        anchors = deal.get("confirmation_timestamps_ms") or [deal["timestamp_ms"]]
        distance = min(abs(timestamp_ms - int(anchor)) for anchor in anchors)
        if distance <= MENTION_DEAL_MAX_DISTANCE_MS:
            candidates.append((distance, deal_number, deal))
    if not candidates:
        return "UNKNOWN", "NO_VISUAL_DEAL_WITHIN_10_MIN", []
    nearest = min(item[0] for item in candidates)
    plausible = [item for item in candidates if item[0] <= nearest + 60_000]
    owners = []
    deal_numbers = []
    for _, deal_number, deal in plausible:
        owner = _owners(deal["hands"]).get(card)
        if owner is not None:
            owners.append(owner)
            deal_numbers.append(deal_number)
    if owners and len(set(owners)) == 1:
        return owners[0], "NEAREST_VISUAL_DEAL_CARD_OWNER", deal_numbers
    return "UNKNOWN", "AMBIGUOUS_VISUAL_DEAL_CONTEXT", deal_numbers


def analyze_teacher_mentions(video: Path, data: dict[str, Any], output: Path) -> dict[str, Any]:
    """Transcribe, diarize and count explicit card names in teacher speech."""
    from universal_video.runner import transcribe
    from universal_video.speaker_structure import run_speaker_structure

    asr_work = output / "work" / "asr"
    asr_work.mkdir(parents=True, exist_ok=True)
    duration = float(data["source"]["duration_ms"]) / 1000.0
    transcript, qc, language, deduplicated = transcribe(
        video,
        asr_work,
        duration,
        chunk_seconds=300,
        initial_prompt=(
            "Урок спортивного бриджа. Карты: туз, король, дама, валет, десятка, "
            "девятка, восьмерка, семерка, шестерка, пятерка, четверка, тройка, двойка; "
            "масти: пики, червы, бубны, трефы; Север, Восток, Юг, Запад."
        ),
    )
    diarized, speaker_report = run_speaker_structure(
        video,
        transcript,
        asr_work,
        min_label_coverage=0.80,
    )
    (output / "transcript.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in diarized),
        encoding="utf-8",
    )
    (output / "transcript_qc.json").write_text(json.dumps(qc, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "speaker_diarization.json").write_text(json.dumps(speaker_report, ensure_ascii=False, indent=2), encoding="utf-8")

    role_supported = bool(speaker_report.get("role_mapping_supported")) and speaker_report.get("role_mapping_proof_status") == "PASS"
    events = []
    unverified_events = []
    for index, segment in enumerate(diarized):
        if segment.get("unreliable"):
            continue
        cards = _segment_card_mentions(str(segment.get("text") or ""))
        if not cards:
            continue
        is_teacher = (
            role_supported
            and str(segment.get("speaker_role_candidate") or "").lower() == "teacher"
            and float(segment.get("speaker_role_confidence") or 0.0) >= 0.60
        )
        target = events if is_teacher else unverified_events
        for occurrence, card in enumerate(cards, 1):
            timestamp_ms = round(float(segment["start"]) * 1000)
            seat, binding, deal_numbers = _seat_for_mention(card, timestamp_ms, data["deals"])
            target.append({
                "event_id": f"asr-{index:05d}-{occurrence}",
                "timestamp_ms": timestamp_ms,
                "timestamp": format_timestamp(timestamp_ms),
                "card": card,
                "seat": seat,
                "seat_binding": binding,
                "candidate_deals": deal_numbers,
                "text": str(segment.get("text") or "").strip(),
                "speaker": segment.get("speaker"),
                "speaker_role_confidence": segment.get("speaker_role_confidence"),
            })

    counts = Counter((item["card"], item["seat"]) for item in events)
    aggregates = [
        {"card": card, "seat": seat, "count": count}
        for (card, seat), count in sorted(
            counts.items(),
            key=lambda item: (-item[1], rank_layout.SUITS.index(item[0][0][1]), rank_layout.RANKS.index(item[0][0][0]), item[0][1]),
        )
    ]
    return {
        "status": "TEACHER_ROLE_VERIFIED" if role_supported else "TEACHER_ROLE_UNVERIFIED",
        "language": language,
        "asr_model": os.getenv("UNIVERSAL_VIDEO_WHISPER_MODEL", "small"),
        "transcript_segments": len(diarized),
        "deduplicated_segments": deduplicated,
        "speaker_report": speaker_report,
        "teacher_mention_count": len(events),
        "teacher_mentions": events,
        "teacher_counts_by_card_and_hand": aggregates,
        "unverified_speaker_mention_count": len(unverified_events),
        "unverified_speaker_mentions": unverified_events,
        "binding_policy": "WITHIN_10_MIN_OF_NEAREST_VISUAL_DEAL;_AMBIGUOUS_IS_UNKNOWN",
        "auction_or_bridge_logic_used_for_card_weights": False,
    }


def _positive_only_fused_weight(visual_weight: float, bonuses: list[float]) -> float:
    """Combine corroborating evidence without ever lowering visual evidence."""
    residual = 1.0 - max(0.0, min(1.0, visual_weight))
    for bonus in bonuses:
        residual *= 1.0 - max(0.0, min(0.95, bonus))
    return round(1.0 - residual, 6)


def _deal_evidence_events(data: dict[str, Any], key: str, deal_number: int) -> list[dict[str, Any]]:
    events = data.get(key) or []
    return [
        item
        for item in events
        if isinstance(item, dict)
        and (
            item.get("deal") == deal_number
            or deal_number in (item.get("candidate_deals") or [])
        )
    ]


def complete_unrecognized_cards(data: dict[str, Any]) -> dict[str, Any]:
    """Classify the already solved 52-card assignment by evidence origin.

    The pixel stage first locates physical card slots and maximizes template
    similarity.  This post-visual stage is explicitly allowed to complete the
    deck, but it must never relabel a constrained completion as a direct visual
    read.  Raw pixel weights remain unchanged and visible to reviewers.
    """
    from bridge_vision.multiframe import validate_full_deal

    total_visual = 0
    total_completed = 0
    per_deal = []
    all_conflicts: list[dict[str, Any]] = []
    full_deck = {rank + suit for rank in rank_layout.RANKS for suit in rank_layout.SUITS}
    for deal_number, deal in enumerate(data.get("deals") or [], 1):
        rows = deal.get("weights") or []
        cards = {row["card"] for row in rows}
        counts = Counter(row["seat"] for row in rows)
        if cards != full_deck or counts != Counter({seat: 13 for seat in rank_layout.SEATS}):
            raise RuntimeError("post-visual completion received an invalid deck assignment")
        independent_validation = validate_full_deal({
            "hands": {
                seat: {
                    "cards": sorted(
                        (row["card"] for row in rows if row["seat"] == seat),
                        key=lambda card: (rank_layout.SUITS.index(card[1]), rank_layout.RANKS.index(card[0])),
                    )
                }
                for seat in rank_layout.SEATS
            },
            "derivations": [{"kind": "POST_VISUAL_52_BY_13_COMPLETION"}],
        })
        if independent_validation.get("status") != "PASS":
            raise RuntimeError("post-visual completion failed independent full-deal validation")
        visual = 0
        completed = 0
        pointer_events = _deal_evidence_events(data, "teacher_pointer_events", deal_number)
        play_events = _deal_evidence_events(data, "played_card_memory", deal_number)
        for row in rows:
            score = float(row["weight_median"])
            row["visual_weight"] = round(score, 6)
            row["visual_recognized"] = score >= LOW_WEIGHT_THRESHOLD
            if row["visual_recognized"]:
                row["visual_template_source"] = (
                    "CROSS_DEAL_CHALLENGER_VISUAL"
                    if float(row.get("weight_challenger_v3", -2.0)) > float(row.get("weight_gold_v2", score))
                    else "GOLD_V2_VISUAL"
                )
                row["identification_source"] = "VISUAL"
                row["ownership_source"] = "GEOMETRY"
                row["provenance"] = ["VISUAL", "GEOMETRY"]
                visual += 1
            else:
                row["visual_template_source"] = "BELOW_VISUAL_THRESHOLD"
                row["identification_source"] = "DECK_COMPLETION"
                row["ownership_source"] = "GEOMETRY_PLUS_DECK_COMPLETION"
                row["provenance"] = ["GEOMETRY", "DECK_COMPLETION"]
                completed += 1
            row["completed_assignment"] = True
            bonuses: list[float] = []
            corroboration: list[str] = []
            row_conflicts: list[dict[str, Any]] = []
            for event in pointer_events:
                claimed_card = str(event.get("claimed_card") or event.get("card") or "").upper().replace("10", "T")
                claimed_seat = str(event.get("claimed_seat") or event.get("seat") or "").upper()
                if claimed_card != row["card"]:
                    continue
                if event.get("spatial_overlap") is not True or event.get("teacher_role_verified") is not True:
                    continue
                if claimed_seat == row["seat"]:
                    bonus = min(0.90, max(0.0, float(event.get("confidence", 0.0))))
                    if bonus:
                        bonuses.append(bonus)
                        corroboration.append("TEACHER_POINTER")
                elif claimed_seat and claimed_seat != row["seat"]:
                    conflict = {
                        "kind": "CURSOR_CONFLICT",
                        "deal": deal_number,
                        "card": row["card"],
                        "visual_or_completed_seat": row["seat"],
                        "claimed_seat": claimed_seat,
                    }
                    row_conflicts.append(conflict)
                    all_conflicts.append(conflict)
            for event in play_events:
                remembered_card = str(event.get("card") or "").upper().replace("10", "T")
                remembered_seat = str(event.get("seat") or "").upper()
                if remembered_card != row["card"]:
                    continue
                if not (
                    event.get("evidence_verified") is True
                    or event.get("recognition_status") == "VISUAL_CARD_CANDIDATE"
                ):
                    continue
                if remembered_seat == row["seat"]:
                    bonus = min(0.80, max(0.0, float(event.get("confidence", event.get("minimum_visual_confidence", 0.0)))))
                    if bonus:
                        bonuses.append(bonus)
                        corroboration.append("PLAY_MEMORY")
                elif remembered_seat:
                    conflict = {
                        "kind": "PLAY_MEMORY_CONFLICT",
                        "deal": deal_number,
                        "card": row["card"],
                        "visual_or_completed_seat": row["seat"],
                        "remembered_seat": remembered_seat,
                    }
                    row_conflicts.append(conflict)
                    all_conflicts.append(conflict)
            row["cursor_bonus"] = round(max((bonuses[index] for index, source in enumerate(corroboration) if source == "TEACHER_POINTER"), default=0.0), 6)
            row["positive_evidence_sources"] = sorted(set(corroboration))
            row["provenance"] = sorted(set(row["provenance"] + corroboration))
            row["fused_weight"] = _positive_only_fused_weight(score, bonuses)
            row["conflicts"] = row_conflicts
        suit_states = {
            seat: {
                suit: {
                    "status": "VOID_CONFIRMED" if not deal["hands"][seat][suit] else "PRESENT",
                    "card_count": len(deal["hands"][seat][suit]),
                    "source": "POST_COMPLETION_52_BY_13",
                }
                for suit in rank_layout.SUITS
            }
            for seat in rank_layout.SEATS
        }
        total_visual += visual
        total_completed += completed
        completion = {
            "deal": deal_number,
            "visual_recognized_cards": visual,
            "deck_constrained_completed_cards": completed,
            "final_cards": 52,
            "hand_counts": {seat: counts[seat] for seat in rank_layout.SEATS},
            "status": "COMPLETE_52_BY_13",
            "independent_validation": independent_validation,
        }
        deal["completion"] = completion
        deal["suit_states"] = suit_states
        deal["status"] = "COMPLETE_52_BY_13_WITH_PROVENANCE"
        per_deal.append(completion)
    return {
        "stage": "POST_VISUAL_UNRECOGNIZED_CARD_COMPLETION_V1",
        "visual_threshold": LOW_WEIGHT_THRESHOLD,
        "objective": "MAXIMIZE_TEMPLATE_SIMILARITY_SUBJECT_TO_GEOMETRIC_SUIT_ORDER_AND_DECK_BIJECTION",
        "constraints": [
            "52_UNIQUE_CARDS",
            "13_CARDS_PER_HAND",
            "13_CARDS_PER_SUIT_ACROSS_HANDS",
            "FIXED_H_C_D_S_INTERFACE_ORDER",
            "DESCENDING_RANK_ORDER_INSIDE_SUIT_GROUP",
            "EMPTY_SUIT_GROUP_ALLOWED",
        ],
        "bridge_strategy_or_auction_logic_used": False,
        "raw_visual_weights_preserved": True,
        "cursor_bonus_policy": "POSITIVE_ONLY;_ZERO_UNLESS_VERIFIED_TEACHER_CARD_NAME_AND_SPATIAL_OVERLAP",
        "positive_fusion_formula": "1-(1-visual_weight)*PRODUCT(1-positive_bonus)",
        "teacher_speech_without_verified_pointer_changes_owner": False,
        "conflicts_do_not_reduce_weight": True,
        "conflicts": all_conflicts,
        "total_visual_recognized_cards": total_visual,
        "total_deck_constrained_completed_cards": total_completed,
        "deals": per_deal,
    }


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
                first_layout, first_reason = _full_template_layout(first_original, templates, viewport_y)
                if first_layout is None:
                    rejections[first_reason] += 1
                    timestamp_ms += scan_ms
                    continue
                fused = _single_template_layout(first_layout)
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
                    "evidence": {"minimum_assigned_score": round(fused["minimum"], 6), "median_assigned_score": round(fused["median"], 6), "independent_frames": 1, "confidence_kind": "TEMPLATE_SIMILARITY_UNCALIBRATED"},
                    "weights": fused["weights"],
                    "_matches": fused["matches"],
                    "receipt": {"method": "104_HUMAN_VERIFIED_CARD_CORNERS_SINGLE_FULL_SERVER_FRAME", "bridge_logic_weighting": False},
                })
                timestamp_ms += scan_ms
    finally:
        capture.release()

    clusters = _cluster_recognized(recognized)
    deals = []
    retained_clusters = []
    for values in sorted(clusters, key=lambda xs: min(item["timestamp_ms"] for item in xs)):
        best = max(values, key=lambda item: (float(item.get("evidence", {}).get("minimum_assigned_score", -1)), -item["timestamp_ms"]))
        best = dict(best)
        best["server_confirmations"] = len(values)
        best["confirmation_timestamps_ms"] = [item["timestamp_ms"] for item in values]
        best["cluster_max_owner_distance"] = max(
            (_owner_distance(best["hands"], item["hands"]) for item in values),
            default=0,
        )
        deals.append(best)
        retained_clusters.append(values)
    deals = deals[:max_deals]
    retained_clusters = retained_clusters[:max_deals]
    challenger = _apply_cross_deal_challenger(retained_clusters, deals, output)
    keep = {item["screenshot"] for item in deals}
    for path in screenshots.glob("*.png"):
        if str(path.relative_to(output)) not in keep:
            path.unlink()
    for deal in deals:
        deal.pop("_matches", None)

    return {
        "schema": "diana167-card-report/v3",
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
            "challenger": challenger,
        },
        "sampling": {"scan_interval_ms": scan_ms, "policy": "PIXEL_REGISTERED_FULL_LAYOUT_WITH_OWNER_DISTANCE_CLUSTERING", "registration_y_offsets": dict(sorted(registration_offsets.items()))},
        "summary": {
            "deals": len(deals),
            "recognized_candidates": len(recognized),
            "exact_layout_hashes": len({item["layout_sha256"] for item in recognized}),
            "owner_distance_clusters": len(clusters),
            "rejections": dict(sorted(rejections.items())),
        },
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
    story.append(Paragraph("Диана 167: сдачи, веса карт и упоминания преподавателя", title))
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
        ["Интервал сканирования", f"{data['sampling']['scan_interval_ms'] / 1000:g} сек."],
        ["Новых проверенных challenger-вариантов", str(data["profile"]["challenger"]["validated_variant_count"])],
        ["Визуально прочитано карт", str(data.get("reconstruction", {}).get("total_visual_recognized_cards", 0))],
        ["Достроено ограничениями", str(data.get("reconstruction", {}).get("total_deck_constrained_completed_cards", 0))],
        ["Режим", "серверный, shadow-only; без записи в канон"],
    ]
    table = Table([[Paragraph(f"<b>{a}</b>", body), Paragraph(str(b), body)] for a, b in cover], colWidths=[55 * mm, 190 * mm])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCD5DF")), ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EEF3F8")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story.append(table)
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph("Как читать веса", h2))
    story.append(Paragraph("Вес — это сходство пиксельного глифа с проверенным эталоном, а не вероятность правильности. Карты с весом не ниже порога отмечены как визуально прочитанные. Остальные назначены отдельным поствизуальным этапом по геометрическому месту и ограничениям: 52 уникальные карты, по 13 в каждой руке. Исходный визуальный вес при этом не изменяется. Торговля и стратегическая бриджевая логика отключены.", body))
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
        completion = deal.get("completion") or {}
        story.append(Paragraph(
            f"Визуально прочитано: {completion.get('visual_recognized_cards', 0)} · достроено по геометрии и ограничениям: {completion.get('deck_constrained_completed_cards', 0)} · итог: {completion.get('final_cards', 0)} карт, по 13 в каждой руке.",
            body,
        ))
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
            rows = [[Paragraph("Поз.", small), Paragraph("Карта", small), Paragraph("Gold v2", small), Paragraph("После v3", small), Paragraph("Источник", small)]]
            for item in half:
                before = float(item.get("weight_gold_v2", item["weight_median"]))
                source = "визуально" if item.get("visual_recognized") else "достроено"
                rows.append([Paragraph(item["seat"], small), Paragraph(card_text(item["card"]), card_style), Paragraph(f"{before:.4f}", small), Paragraph(f"{item['weight_median']:.4f}", small), Paragraph(source, small)])
            block = Table(rows, colWidths=[12 * mm, 18 * mm, 20 * mm, 20 * mm, 24 * mm], repeatRows=1)
            style_commands = [("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D6DEE7")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF3F8")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("TOPPADDING", (0, 0), (-1, -1), 1.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6)]
            for row_number, item in enumerate(half, 1):
                if not item.get("visual_recognized"):
                    style_commands.append(("BACKGROUND", (0, row_number), (-1, row_number), colors.HexColor("#FFF1D6")))
            block.setStyle(TableStyle(style_commands))
            blocks.append(block)
        paired = Table([[blocks[0], blocks[1]]], colWidths=[90 * mm, 90 * mm], hAlign="LEFT")
        paired.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 6)]))
        story.append(paired)
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph("Оранжевые строки — карты ниже визуального порога: их принадлежность руке достроена по экранной геометрии и ограничениям полной колоды. Пустая масть допустима и после завершения помечается VOID_CONFIRMED. Тип веса: сходство с Gold v2 либо с challenger-вариантом; это не вероятность. Логика торговли и розыгрыша отключена.", small))
        if number != len(data["deals"]):
            story.append(PageBreak())

    speech = data.get("speech") or {}
    story.append(PageBreak())
    story.append(Paragraph("Упоминания карт преподавателем", title))
    story.append(Spacer(1, 2 * mm))
    if speech.get("status") != "TEACHER_ROLE_VERIFIED":
        story.append(Paragraph(
            "Автоматическая идентификация роли преподавателя не прошла независимую проверку. Поэтому неподтверждённые реплики не включены в итоговый счёт преподавателя и сохранены отдельно как UNKNOWN speaker.",
            body,
        ))
    story.append(Paragraph(
        f"Статус роли: {speech.get('status', 'UNKNOWN')} · подтверждённых упоминаний: {speech.get('teacher_mention_count', 0)} · неподтверждённых упоминаний других/неразделённых голосов: {speech.get('unverified_speaker_mention_count', 0)}",
        body,
    ))
    story.append(Spacer(1, 3 * mm))
    aggregates = speech.get("teacher_counts_by_card_and_hand") or []
    aggregate_rows = [[Paragraph("Карта", small), Paragraph("Рука", small), Paragraph("Количество", small)]]
    for item in aggregates:
        aggregate_rows.append([
            Paragraph(card_text(item["card"]), card_style),
            Paragraph(str(item["seat"]), small),
            Paragraph(str(item["count"]), small),
        ])
    if len(aggregate_rows) == 1:
        aggregate_rows.append([Paragraph("—", small), Paragraph("UNKNOWN", small), Paragraph("0", small)])
    aggregate_table = Table(aggregate_rows, colWidths=[35 * mm, 35 * mm, 35 * mm], repeatRows=1)
    aggregate_table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D6DEE7")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF3F8")), ("ALIGN", (0, 0), (-1, -1), "CENTER")]))
    story.append(aggregate_table)

    events = speech.get("teacher_mentions") or []
    if events:
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph("Подтверждённые фрагменты речи", h2))
        event_rows = [[Paragraph("Время", small), Paragraph("Карта", small), Paragraph("Рука", small), Paragraph("Текст ASR", small)]]
        for item in events:
            event_rows.append([
                Paragraph(item["timestamp"], small),
                Paragraph(card_text(item["card"]), card_style),
                Paragraph(item["seat"], small),
                Paragraph(item["text"], small),
            ])
        event_table = Table(event_rows, colWidths=[23 * mm, 20 * mm, 24 * mm, 190 * mm], repeatRows=1)
        event_table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.2, colors.HexColor("#D6DEE7")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF3F8")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(event_table)

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
    parser.add_argument("--skip-asr", action="store_true")
    args = parser.parse_args()
    if not 2000 <= args.scan_ms <= 30000:
        raise SystemExit("scan-ms must be in 2000..30000")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    data = scan_video(args.video, args.gold_zip, args.output_dir, args.scan_ms, args.max_deals)
    if not args.skip_asr:
        data["speech"] = analyze_teacher_mentions(args.video, data, args.output_dir)
    else:
        data["speech"] = {"status": "SKIPPED", "teacher_mention_count": 0, "teacher_mentions": [], "teacher_counts_by_card_and_hand": []}
    data["reconstruction"] = complete_unrecognized_cards(data)
    analysis = args.output_dir / "master_analysis.json"
    analysis.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if not data["deals"]:
        raise RuntimeError("no complete deal passed the fail-closed server gates")
    pdf = args.output_dir / "Диана 167 — карты, веса и упоминания — server v3.pdf"
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


