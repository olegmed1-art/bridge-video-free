#!/usr/bin/env python3
"""Field-evaluate the publication-cross pixel extractor on public real PDFs.

The PDFs are downloaded only at test time. Canonical truth is read independently from
embedded PDF vector text; OCR output and DDS3 never create or repair truth. Rendered
board crops are temporary and are not committed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import fitz

from bridge_school_api.dds3.vision_publication import (
    PublicationVisionError,
    extract_publication_cross_observation,
)

SUIT_GLYPHS = {
    "[": "S", "]": "H", "{": "D", "}": "C",
    "♠": "S", "♥": "H", "♦": "D", "♣": "C",
    "ª": "S", "©": "H", "¨": "D", "§": "C",
}
RANKS = set("AKQJT98765432")
VUL_MAP = {"NONE": "None", "LOVE": "None", "NS": "NS", "N/S": "NS", "N-S": "NS", "EW": "EW", "E/W": "EW", "E-W": "EW", "BOTH": "Both", "ALL": "Both"}
DEALER_MAP = {"N": "N", "NORTH": "N", "E": "E", "EAST": "E", "S": "S", "SOUTH": "S", "W": "W", "WEST": "W"}


@dataclass(frozen=True)
class Sample:
    source_id: str
    url: str
    title: str


SAMPLES = [
    Sample(
        "wbf2006-b12",
        "https://www.bridgehands.com/Tournaments/WBF/2006_World_Bridge_Championship/bul_07.pdf",
        "Board 12. Dealer West. N/S Vul.",
    ),
    Sample(
        "wbf2005-b1",
        "https://www.bridgehands.com/Tournaments/WBF/2005_World_Team_Championship/bul_12.pdf",
        "Board 1. Dealer North. None Vul.",
    ),
    Sample(
        "ebl2022-b1",
        "https://championships.eurobridge.org/ENTC2022/bulletins/Bul_06.pdf",
        "Board 1. Dealer West. NS Vul.",
    ),
    Sample(
        "bamsa2020-b23",
        "https://bridgemindsport.org/wp-content/uploads/2021/01/BAMSA-Bulletin-Spring-2020.pdf",
        "Board 23. Dealer South. All Vul.",
    ),
    Sample(
        "irish2025-b1",
        "https://www.cbai.ie/wp-content/uploads/2025/12/CBAI-Bridge-Journal-Issue-12-Winter-2025.pdf",
        "Board 1 Dealer North. Vul All.",
    ),
]


def _download(url: str, path: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "bridge-school-dds3-field-gate/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response, path.open("wb") as output:
        output.write(response.read())


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("–", "-").replace("—", "-")).strip()


def _clean_rank_token(text: str) -> str:
    value = re.sub(r"\s+", "", text.upper()).replace("10", "T")
    if not value or any(ch not in RANKS for ch in value):
        return ""
    return value


def _find_clip(page: fitz.Page, title: str) -> fitz.Rect | None:
    wanted = _norm(title).lower()
    blocks = page.get_text("blocks")
    matches = [block for block in blocks if wanted in _norm(block[4]).lower()]
    if not matches:
        prefix = _norm(title).split(" Vul", 1)[0].lower()
        matches = [block for block in blocks if prefix in _norm(block[4]).lower()]
    if not matches:
        return None
    block = min(matches, key=lambda item: item[1])
    x0, y0, x1, y1 = block[:4]
    block_text = block[4]
    suit_count = sum(block_text.count(symbol) for symbol in SUIT_GLYPHS)
    if suit_count >= 12 and y1 - y0 > 80:
        rect = fitz.Rect(x0 - 8, y0 - 5, x1 + 8, y1 + 5)
    else:
        half = page.rect.width / 2
        if x0 < half and x1 <= half + 30:
            left, right = 0, half + 12
        elif x0 >= half - 30:
            left, right = half - 12, page.rect.width
        else:
            left, right = 0, page.rect.width
        rect = fitz.Rect(left, max(0, y0 - 5), right, min(page.rect.height, y0 + 230))
    return rect & page.rect


def _metadata_truth(title: str) -> tuple[int, str, str]:
    text = _norm(title)
    board_match = re.search(r"Board\s*(\d+)", text, re.IGNORECASE)
    dealer_match = re.search(r"Dealer\s*(North|East|South|West|[NESW])", text, re.IGNORECASE)
    vul_match = re.search(r"Vul\s*(None|Love|N\s*[-/]?\s*S|E\s*[-/]?\s*W|Both|All)", text, re.IGNORECASE)
    if vul_match is None:
        vul_match = re.search(r"(None|Love|N\s*[-/]?\s*S|E\s*[-/]?\s*W|Both|All)\s*Vul", text, re.IGNORECASE)
    if not board_match or not dealer_match or not vul_match:
        raise ValueError(f"title metadata unsupported: {title!r}")
    dealer = DEALER_MAP[dealer_match.group(1).upper()]
    vul_key = re.sub(r"\s+", "", vul_match.group(1).upper())
    return int(board_match.group(1)), dealer, VUL_MAP[vul_key]


def _cluster_rows(rows: list[dict]) -> dict[str, list[dict]]:
    clusters: list[list[dict]] = []
    for row in sorted(rows, key=lambda item: item["x"]):
        for cluster in clusters:
            center = sum(item["x"] for item in cluster) / len(cluster)
            if abs(row["x"] - center) < 18:
                cluster.append(row)
                break
        else:
            clusters.append([row])
    clusters.sort(key=lambda cluster: sum(item["x"] for item in cluster) / len(cluster))
    if len(clusters) != 3 or sorted(len(cluster) for cluster in clusters) != [4, 4, 8]:
        summary = [(round(sum(item["x"] for item in cluster) / len(cluster), 1), len(cluster)) for cluster in clusters]
        raise ValueError(f"source hand clusters invalid: {summary}")
    west, middle, east = clusters
    middle.sort(key=lambda item: item["y"])
    west.sort(key=lambda item: item["y"])
    east.sort(key=lambda item: item["y"])
    return {"N": middle[:4], "E": east, "S": middle[4:], "W": west}


def _truth_hands(page: fitz.Page, clip: fitz.Rect) -> dict[str, str]:
    words = [word for word in page.get_text("words") if clip.intersects(fitz.Rect(word[:4]))]
    suit_words = []
    rank_words = []
    for word in words:
        text = word[4].strip()
        cx = (word[0] + word[2]) / 2; cy = (word[1] + word[3]) / 2
        if text in SUIT_GLYPHS:
            suit_words.append((cx, cy, SUIT_GLYPHS[text], word))
        else:
            cleaned = _clean_rank_token(text)
            if cleaned:
                rank_words.append((cx, cy, cleaned, word))
    if len(suit_words) != 16:
        raw_symbols = sorted({word[4].strip() for word in words if len(word[4].strip()) == 1 and not word[4].strip().isalnum()})
        raise ValueError(f"source has {len(suit_words)} suit rows, expected 16; symbols={raw_symbols[:20]}")

    rows = []
    for suit_x, suit_y, suit, suit_word in suit_words:
        same_row_suits = [
            other for other in suit_words
            if other[3][0] > suit_word[0] and abs(other[1] - suit_y) <= 5.5
        ]
        right_boundary = suit_word[2] + 115
        if same_row_suits:
            nearest = min(same_row_suits, key=lambda item: item[3][0])
            right_boundary = min(right_boundary, (suit_word[2] + nearest[3][0]) / 2)
        pieces = []
        for _, rank_y, text, rank_word in rank_words:
            if (
                rank_word[0] >= suit_word[2] - 0.5
                and rank_word[0] < right_boundary
                and abs(rank_y - suit_y) <= 5.5
            ):
                pieces.append((rank_word[0], text))
        pieces.sort()
        holding = "".join(piece[1] for piece in pieces)
        rows.append({"x": suit_x, "y": suit_y, "suit": suit, "holding": holding})

    grouped = _cluster_rows(rows)
    hands: dict[str, str] = {}
    cards: list[str] = []
    for hand, hand_rows in grouped.items():
        suits = {row["suit"]: row["holding"] for row in hand_rows}
        if set(suits) != set("SHDC"):
            raise ValueError(f"source missing suit row for {hand}")
        holding = ".".join(suits[suit] for suit in "SHDC")
        if sum(len(part) for part in holding.split(".")) != 13:
            raise ValueError(f"source hand {hand} is not 13 cards: {holding}")
        hands[hand] = holding
        for suit, ranks in zip("SHDC", holding.split("."), strict=True):
            cards.extend(suit + rank for rank in ranks)
    if len(cards) != 52 or len(set(cards)) != 52:
        raise ValueError(f"source deck invalid: {len(cards)}/{len(set(cards))}")
    return hands


def _observation_hands(observation) -> dict[str, str]:
    return {seat: ".".join(observation.hands[seat][suit] for suit in "SHDC") for seat in "NESW"}


def _evaluate_sample(sample: Sample, root: Path, dpi: int) -> dict:
    pdf_path = root / f"{sample.source_id}.pdf"
    _download(sample.url, pdf_path)
    source_sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    document = fitz.open(pdf_path)
    board, dealer, vulnerability = _metadata_truth(sample.title)
    for page_index, page in enumerate(document):
        clip = _find_clip(page, sample.title)
        if clip is None:
            continue
        truth_hands = _truth_hands(page, clip)
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), clip=clip, alpha=False)
        image_bytes = pix.tobytes("png")
        image_sha = hashlib.sha256(image_bytes).hexdigest()
        try:
            observed = extract_publication_cross_observation(image_bytes, media_type="image/png", filename=f"{sample.source_id}.png")
        except PublicationVisionError as exc:
            return {"id": sample.source_id, "status": "rejected", "reason": str(exc), "source_sha256": source_sha, "image_sha256": image_sha}
        observed_hands = _observation_hands(observed)
        meta_ok = int(observed.board_number.value) == board and str(observed.dealer.value) == dealer and str(observed.vulnerability.value) == vulnerability
        hands_ok = observed_hands == truth_hands

        # Severe negative: remove enough of the rendered deal to cut through the
        # South-hand rows. The previous 82% crop still contained all 52 cards in this
        # publication layout, so accepting it was not a meaningful crop failure.
        negative_status = "not_run"
        try:
            from PIL import Image
            import io
            pil = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            cropped = pil.crop((0, 0, pil.width, max(1, int(pil.height * 0.72))))
            buffer = io.BytesIO(); cropped.save(buffer, format="PNG")
            extract_publication_cross_observation(buffer.getvalue(), media_type="image/png", filename=f"{sample.source_id}-cropped.png")
            negative_status = "wrong_accept"
        except PublicationVisionError:
            negative_status = "rejected"

        return {
            "id": sample.source_id,
            "status": "exact" if meta_ok and hands_ok else "wrong_accept",
            "metadata_exact": meta_ok,
            "hands_exact": hands_ok,
            "negative_crop": negative_status,
            "source_sha256": source_sha,
            "image_sha256": image_sha,
            "page": page_index + 1,
            "board": board,
        }
    return {"id": sample.source_id, "status": "source_title_not_found", "source_sha256": source_sha}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dpi", type=int, default=220)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="dds3-publication-field-") as temp:
        root = Path(temp)
        results = []
        for sample in SAMPLES:
            try:
                results.append(_evaluate_sample(sample, root, args.dpi))
            except Exception as exc:
                results.append({"id": sample.source_id, "status": "field_error", "reason": f"{type(exc).__name__}:{exc}"})
    exact = sum(result.get("status") == "exact" for result in results)
    wrong = sum(result.get("status") == "wrong_accept" for result in results)
    negative_pass = sum(result.get("negative_crop") == "rejected" for result in results)
    report = {
        "extractor": "local_tesseract_publication_cross_v1",
        "real_public_sources": len(SAMPLES),
        "exact": exact,
        "wrong_accepts": wrong,
        "negative_crop_rejected": negative_pass,
        "truth_source": "embedded source PDF vector text",
        "dds3_used_for_truth": False,
        "bridge_inference_repair": False,
        "paid_or_cloud_vision": False,
        "results": results,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(text)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0 if wrong == 0 and exact >= 1 and negative_pass >= exact else 3


if __name__ == "__main__":
    raise SystemExit(main())
