#!/usr/bin/env python3
"""Build a real-image DDS3 vision corpus from source federation PDFs.

The source PDF provides two independent views of the same board:
* pixels rendered from the original page become the vision input image;
* embedded vector text becomes canonical truth (never a DDS3 result).

Only board diagrams whose source truth independently validates as a full
52-card deal are emitted. The builder does not infer a missing card, hand,
dealer or vulnerability. Source PDFs and rendered crops are intentionally
not committed by this script; callers choose a private/local output path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import cv2
import fitz
import numpy as np
from PIL import Image

SUIT_GLYPHS = {"\uf0aa": "S", "\uf0a9": "H", "\uf0a8": "D", "\uf0a7": "C"}
RANK_CHARS = set("AKQJT98765432")
VULNERABILITY_ALIASES = {"NS": "N-S", "EW": "E-W", "All": "Both"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _merge_adjacent_components(components: list[tuple[int, int, int, int, int]]) -> list[tuple[int, int, int, int, int]]:
    """Merge the two yellow halves separated by the black compass square."""
    current = sorted(components, key=lambda item: item[0])
    changed = True
    while changed:
        changed = False
        output: list[tuple[int, int, int, int, int]] = []
        used = [False] * len(current)
        for index, component in enumerate(current):
            if used[index]:
                continue
            x, y, width, height, area = component
            x2, y2 = x + width, y + height
            for other_index in range(index + 1, len(current)):
                if used[other_index]:
                    continue
                bx, by, bwidth, bheight, barea = current[other_index]
                bx2, by2 = bx + bwidth, by + bheight
                vertical_overlap = max(0, min(y2, by2) - max(y, by)) / max(1, min(height, bheight))
                horizontal_gap = max(0, max(x, bx) - min(x2, bx2))
                if vertical_overlap > 0.85 and horizontal_gap <= 8:
                    nx, ny = min(x, bx), min(y, by)
                    nx2, ny2 = max(x2, bx2), max(y2, by2)
                    x, y, width, height, area = nx, ny, nx2 - nx, ny2 - ny, area + barea
                    x2, y2 = x + width, y + height
                    used[other_index] = True
                    changed = True
            used[index] = True
            output.append((x, y, width, height, area))
        current = output
    return current


def detect_yellow_board_panels(rgb: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """Locate original yellow federation board panels by pixels, not PDF text."""
    yellow = (
        (rgb[:, :, 0] > 225)
        & (rgb[:, :, 1] > 210)
        & (rgb[:, :, 2] < 130)
    ).astype("uint8")
    component_count, _, stats, _ = cv2.connectedComponentsWithStats(yellow, 8)
    components: list[tuple[int, int, int, int, int]] = []
    for component_id in range(1, component_count):
        x, y, width, height, area = map(int, stats[component_id])
        if area > 5000 and width > 50 and height > 100:
            components.append((x, y, width, height, area))
    merged = _merge_adjacent_components(components)
    return sorted(
        [item for item in merged if item[2] > 180 and item[3] > 200],
        key=lambda item: (item[1], item[0]),
    )


def _cluster_suit_rows(rows: list[dict]) -> dict[str, list[dict]]:
    clusters: list[list[dict]] = []
    for row in sorted(rows, key=lambda item: item["x"]):
        for cluster in clusters:
            center = sum(item["x"] for item in cluster) / len(cluster)
            if abs(row["x"] - center) < 15:
                cluster.append(row)
                break
        else:
            clusters.append([row])
    clusters.sort(key=lambda cluster: sum(item["x"] for item in cluster) / len(cluster))
    if len(clusters) != 3 or sorted(len(cluster) for cluster in clusters) != [4, 4, 8]:
        summary = [(round(sum(item["x"] for item in cluster) / len(cluster), 1), len(cluster)) for cluster in clusters]
        raise ValueError(f"unexpected hand x-clusters: {summary}")
    west, middle, east = clusters
    middle.sort(key=lambda item: item["y"])
    return {"N": middle[:4], "E": east, "S": middle[4:], "W": west}


def extract_canonical_hands(page: fitz.Page, panel_rect: fitz.Rect) -> dict[str, str]:
    """Read canonical deal truth from PDF vector text inside one rendered panel."""
    words = page.get_text("words")
    suit_words: list[tuple[float, float, str, tuple]] = []
    rank_words: list[tuple[float, float, str, tuple]] = []
    for word in words:
        word_rect = fitz.Rect(word[:4])
        if not panel_rect.intersects(word_rect):
            continue
        text = word[4].strip()
        center_x = (word[0] + word[2]) / 2
        center_y = (word[1] + word[3]) / 2
        if text in SUIT_GLYPHS:
            suit_words.append((center_x, center_y, text, word))
        elif text and all(character in RANK_CHARS for character in text):
            rank_words.append((center_x, center_y, text, word))
    if len(suit_words) != 16:
        raise ValueError(f"canonical source has {len(suit_words)} suit rows, expected 16")

    rows: list[dict] = []
    used_rank_words: set[int] = set()
    for suit_x, suit_y, glyph, suit_word in suit_words:
        candidates = []
        for rank_index, (_, rank_y, text, rank_word) in enumerate(rank_words):
            if rank_index in used_rank_words:
                continue
            if rank_word[0] >= suit_word[2] - 1 and 0 <= rank_word[0] - suit_word[2] <= 80 and abs(rank_y - suit_y) <= 4:
                candidates.append((abs(rank_y - suit_y) + 0.02 * (rank_word[0] - suit_word[2]), rank_index, text))
        holding = ""
        if candidates:
            _, selected_index, holding = min(candidates)
            used_rank_words.add(selected_index)
        elif SUIT_GLYPHS[glyph] == "C":
            # Some source PDFs visually wrap a long final club holding below the club symbol.
            # This is accepted only from source vector geometry; no card is guessed.
            wrapped = []
            for rank_index, (_, rank_y, text, rank_word) in enumerate(rank_words):
                if rank_index in used_rank_words:
                    continue
                if -3 <= rank_word[0] - suit_word[0] <= 80 and 5 <= rank_y - suit_y <= 16:
                    wrapped.append((abs(rank_y - suit_y), rank_index, text))
            if wrapped:
                _, selected_index, holding = min(wrapped)
                used_rank_words.add(selected_index)
        rows.append({"x": suit_x, "y": suit_y, "suit": SUIT_GLYPHS[glyph], "holding": holding})

    grouped = _cluster_suit_rows(rows)
    hands: dict[str, str] = {}
    all_cards: list[str] = []
    for hand_name, hand_rows in grouped.items():
        suits = {row["suit"]: row["holding"] for row in hand_rows}
        if set(suits) != set("SHDC"):
            raise ValueError(f"canonical source missing suit row for {hand_name}")
        hands[hand_name] = ".".join(suits[suit] for suit in "SHDC")
        card_count = 0
        for suit, holding in zip("SHDC", hands[hand_name].split(".")):
            card_count += len(holding)
            all_cards.extend(f"{suit}{rank}" for rank in holding)
        if card_count != 13:
            raise ValueError(f"canonical source hand {hand_name} has {card_count} cards")
    if len(all_cards) != 52 or len(set(all_cards)) != 52:
        raise ValueError(f"canonical source is not 52 unique cards: {len(all_cards)}/{len(set(all_cards))}")
    return hands


def extract_canonical_metadata(page: fitz.Page, panel_rect: fitz.Rect) -> tuple[int, str, str]:
    """Read Board/Dealer/Vulnerability directly from the source PDF header."""
    region = fitz.Rect(
        max(0, panel_rect.x0 - 5),
        max(0, panel_rect.y0 - 45),
        min(page.rect.x1, panel_rect.x1 + 5),
        panel_rect.y0 + 3,
    )
    words = [word for word in page.get_text("words") if region.intersects(fitz.Rect(word[:4]))]

    def value_right_of(label: str) -> str | None:
        labels = [word for word in words if word[4].strip() == label]
        if not labels:
            return None
        anchor = labels[0]
        anchor_y = (anchor[1] + anchor[3]) / 2
        values = [
            word for word in words
            if word[0] >= anchor[2] - 1
            and abs(((word[1] + word[3]) / 2) - anchor_y) < 4
            and word[4].strip() != label
        ]
        return min(values, key=lambda word: word[0])[4].strip() if values else None

    board_text = value_right_of("Board:")
    dealer = value_right_of("Dealer:")
    vulnerability = value_right_of("Vul:")
    if not board_text or not board_text.isdigit() or dealer not in "NESW":
        raise ValueError("canonical source metadata incomplete")
    vulnerability = VULNERABILITY_ALIASES.get(vulnerability or "", vulnerability or "")
    if vulnerability not in {"None", "N-S", "E-W", "Both"}:
        raise ValueError(f"canonical source vulnerability invalid: {vulnerability!r}")
    return int(board_text), dealer, vulnerability


def build_pdf(pdf_path: Path, output_dir: Path, dpi: int) -> tuple[list[dict], list[dict]]:
    pdf_hash = sha256_file(pdf_path)
    document = fitz.open(pdf_path)
    scale = dpi / 72
    accepted: list[dict] = []
    rejected: list[dict] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    for page_index, page in enumerate(document):
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        rgb = np.frombuffer(pixmap.samples, np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)[:, :, :3]
        for x, y, width, height, _ in detect_yellow_board_panels(rgb):
            panel_rect = fitz.Rect(x / scale, y / scale, (x + width) / scale, (y + height) / scale)
            try:
                hands = extract_canonical_hands(page, panel_rect)
                board, dealer, vulnerability = extract_canonical_metadata(page, panel_rect)
            except ValueError as exc:
                rejected.append({"source_file": pdf_path.name, "page": page_index + 1, "reason": str(exc)})
                continue
            top = max(0, y - int(45 * scale))
            left = max(0, x - 5)
            right = min(pixmap.width, x + width + 5)
            bottom = min(pixmap.height, y + height + 5)
            crop = Image.fromarray(rgb[top:bottom, left:right])
            crop_name = f"{pdf_path.stem}-b{board:02d}-p{page_index + 1:02d}.png"
            crop_path = output_dir / crop_name
            crop.save(crop_path)
            accepted.append({
                "id": f"{pdf_path.stem}-b{board:02d}",
                "source_file": pdf_path.name,
                "source_pdf_sha256": pdf_hash,
                "page": page_index + 1,
                "board": board,
                "dealer": dealer,
                "vulnerability": vulnerability,
                "hands": hands,
                "crop_sha256": sha256_file(crop_path),
                "crop_file": crop_name,
                "crop_px": [left, top, right, bottom],
                "dpi": dpi,
            })
    return accepted, rejected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdfs", nargs="+", type=Path, help="Original real federation PDFs")
    parser.add_argument("--output-dir", type=Path, required=True, help="Private/local corpus output directory")
    parser.add_argument("--dpi", type=int, default=180)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    accepted_all: list[dict] = []
    rejected_all: list[dict] = []
    for pdf_path in args.pdfs:
        accepted, rejected = build_pdf(pdf_path, args.output_dir / pdf_path.stem, args.dpi)
        accepted_all.extend(accepted)
        rejected_all.extend(rejected)
        print(f"{pdf_path.name}: accepted={len(accepted)} rejected={len(rejected)}")

    manifest_path = args.output_dir / "canonical_truth.jsonl"
    with manifest_path.open("w", encoding="utf-8") as handle:
        for record in accepted_all:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    report = {
        "real_images": len(accepted_all),
        "rejected_source_panels": len(rejected_all),
        "source_pdf_count": len(args.pdfs),
        "sources": [
            {"file": pdf.name, "sha256": sha256_file(pdf)} for pdf in args.pdfs
        ],
        "rejections": rejected_all,
        "truth_source": "embedded source PDF vector text",
        "dds3_used_for_truth": False,
        "bridge_inference_repair": False,
    }
    (args.output_dir / "build_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("real_images", "rejected_source_panels", "source_pdf_count")}, sort_keys=True))
    return 0 if len(accepted_all) >= 50 else 2


if __name__ == "__main__":
    raise SystemExit(main())
