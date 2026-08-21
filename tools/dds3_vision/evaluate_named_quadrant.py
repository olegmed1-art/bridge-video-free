#!/usr/bin/env python3
"""Real field gate for the named VuBridge hand-diagram layout.

Canonical truth is parsed from embedded source-PDF vector text. Vector geometry is used
only to choose the temporary rendered test crop; the production extractor receives PNG
pixels only. DDS3 is never used to create truth and vector card values are never used to
repair OCR output.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import tempfile
import urllib.request
from pathlib import Path

import fitz
from PIL import Image

from bridge_school_api.dds3.image_ingress import ImageIngressError, _extract_local_observation

URL = "https://www.vubridge.com/Handouts/Handout_2284.pdf"
RANKS = set("AKQJT98765432")
SUITS = {"♠": "S", "♥": "H", "♦": "D", "♣": "C"}
DEALER = {"NORTH": "N", "EAST": "E", "SOUTH": "S", "WEST": "W"}
VUL = {"NONE": "None", "N / S": "NS", "N/S": "NS", "E / W": "EW", "E/W": "EW", "BOTH": "Both", "ALL": "Both"}


def _download(path: Path) -> None:
    request = urllib.request.Request(URL, headers={"User-Agent": "bridge-school-dds3-field-gate/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response, path.open("wb") as output:
        output.write(response.read())


def _clean_ranks(value: str) -> str:
    value = re.sub(r"\s+", "", value.upper()).replace("10", "T")
    if not value or any(char not in RANKS for char in value):
        raise ValueError(f"invalid source rank text: {value!r}")
    return value


def _metadata_truth(text: str) -> tuple[int, str, str]:
    board = re.search(r"Board\s*#\s*:\s*(\d+)", text, re.I)
    dealer = re.search(r"Dealer\s*:\s*(North|East|South|West)", text, re.I)
    vulnerable = re.search(r"Vulnerable\s*:\s*([^\n\r]+)", text, re.I)
    if not board or not dealer or not vulnerable:
        raise ValueError("source metadata missing")
    vul_raw = re.sub(r"\s+", " ", vulnerable.group(1)).strip().upper()
    if vul_raw not in VUL:
        raise ValueError(f"unsupported source vulnerability: {vul_raw!r}")
    return int(board.group(1)), DEALER[dealer.group(1).upper()], VUL[vul_raw]


def _hand_truth(lines: list[str], seat: str) -> str:
    # Source text can also contain auction headings with the same seat words. Select the
    # occurrence whose following source lines actually contain the four printed suit rows.
    starts = [i for i, line in enumerate(lines) if line.strip().lower() == seat.lower()]
    for start in starts:
        rows: dict[str, str] = {}
        for raw in lines[start + 1 : start + 14]:
            line = raw.strip()
            if not line:
                continue
            match = re.match(r"^([♠♥♦♣])\s*(.+)$", line)
            if match:
                rows[SUITS[match.group(1)]] = _clean_ranks(match.group(2))
                if len(rows) == 4:
                    holding = ".".join(rows[suit] for suit in "SHDC")
                    if sum(len(part) for part in holding.split(".")) == 13:
                        return holding
                    break
            elif line.lower() in {"north", "west", "east", "south"} and rows:
                break
    raise ValueError(f"source hand incomplete for {seat}")


def _truth(page: fitz.Page) -> tuple[int, str, str, dict[str, str]]:
    text = page.get_text("text")
    board, dealer, vulnerability = _metadata_truth(text)
    lines = text.splitlines()
    hands = {seat[0]: _hand_truth(lines, seat) for seat in ("North", "East", "South", "West")}
    cards = [suit + rank for seat in "NESW" for suit, ranks in zip("SHDC", hands[seat].split("."), strict=True) for rank in ranks]
    expected = {suit + rank for suit in "SHDC" for rank in "AKQJT98765432"}
    if len(cards) != 52 or len(set(cards)) != 52 or set(cards) != expected:
        raise ValueError(f"source deck invalid: {len(cards)}/{len(set(cards))}")
    return board, dealer, vulnerability, hands


def _looks_like_rank_word(text: str) -> bool:
    cleaned = re.sub(r"\s+", "", text.upper()).replace("10", "T")
    return bool(cleaned) and all(char in RANKS for char in cleaned)


def _near_hand_words(words, heading):
    cx0 = (heading[0] + heading[2]) / 2
    return [
        word for word in words
        if heading[1] - 5 <= (word[1] + word[3]) / 2 <= heading[3] + 190
        and abs((word[0] + word[2]) / 2 - cx0) <= 105
    ]


def _select_hand_heading(words, seat: str):
    candidates = [word for word in words if word[4].strip().lower() == seat]
    if not candidates:
        raise ValueError(f"source seat heading missing for crop: {seat}")
    scored = []
    for heading in candidates:
        nearby = _near_hand_words(words, heading)
        suit_words = [word for word in nearby if word[4].strip() and word[4].strip()[0] in SUITS]
        suit_kinds = {word[4].strip()[0] for word in suit_words}
        rank_words = [word for word in nearby if _looks_like_rank_word(word[4].strip())]
        # Auction column labels have no four-suit hand beneath them. The actual hand
        # heading must be locally supported by at least three distinct printed suit glyphs
        # and several rank strings; the exact 52-card truth gate remains independent.
        if len(suit_kinds) >= 3 and len(rank_words) >= 3:
            score = 100 * len(suit_kinds) + 10 * len(rank_words) - heading[1] * 0.001
            scored.append((score, heading))
    if not scored:
        raise ValueError(f"source hand heading lacks suit-row evidence: {seat}")
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _deal_clip(page: fitz.Page) -> fitz.Rect:
    words = page.get_text("words")
    chosen = {seat: _select_hand_heading(words, seat) for seat in ("north", "west", "east", "south")}

    relevant = list(chosen.values())
    for heading in chosen.values():
        for word in _near_hand_words(words, heading):
            text = word[4].strip()
            if text and (text[0] in SUITS or _looks_like_rank_word(text)):
                relevant.append(word)

    # Include explicit metadata labels and the complete vector words on their lines so
    # the rendered image contains Board/Dealer/Vulnerable exactly as a user screenshot can.
    metadata_labels = [
        word for word in words
        if word[4].strip().lower().rstrip(":#") in {"board", "dealer", "vulnerable"}
    ]
    for label in metadata_labels:
        cy = (label[1] + label[3]) / 2
        relevant.extend(
            word for word in words
            if abs((word[1] + word[3]) / 2 - cy) <= 8
            and word[0] <= label[2] + 180
            and word[2] >= label[0] - 20
        )

    x0 = min(word[0] for word in relevant); y0 = min(word[1] for word in relevant)
    x1 = max(word[2] for word in relevant); y1 = max(word[3] for word in relevant)
    return fitz.Rect(max(0, x0 - 18), max(0, y0 - 16), min(page.rect.width, x1 + 18), min(page.rect.height, y1 + 22))


def _obs_hands(observation) -> dict[str, str]:
    return {seat: ".".join(observation.hands[seat][suit] for suit in "SHDC") for seat in "NESW"}


def _evaluate_page(page: fitz.Page, page_index: int, source_sha: str, dpi: int) -> dict:
    board, dealer, vulnerability, truth_hands = _truth(page)
    clip = _deal_clip(page)
    pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), clip=clip, alpha=False)
    image_bytes = pix.tobytes("png")
    image_sha = hashlib.sha256(image_bytes).hexdigest()
    try:
        observation = _extract_local_observation(image_bytes, media_type="image/png", filename=f"vubridge-board-{board}.png")
    except ImageIngressError as exc:
        return {"board": board, "page": page_index + 1, "status": "rejected", "reason": str(exc), "source_sha256": source_sha, "image_sha256": image_sha}

    observed_hands = _obs_hands(observation)
    meta_ok = int(observation.board_number.value) == board and str(observation.dealer.value) == dealer and str(observation.vulnerability.value) == vulnerability
    hands_ok = observed_hands == truth_hands
    extractor_field = observation.extra_metadata.get("vision_extractor")
    extractor = str(extractor_field.value) if extractor_field is not None else "missing"

    negative = "not_run"
    try:
        pil = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        severe = pil.crop((0, 0, pil.width, max(1, int(pil.height * 0.68))))
        buffer = io.BytesIO(); severe.save(buffer, format="PNG")
        _extract_local_observation(buffer.getvalue(), media_type="image/png", filename=f"vubridge-board-{board}-severe-crop.png")
        negative = "wrong_accept"
    except ImageIngressError:
        negative = "rejected"

    return {
        "board": board, "page": page_index + 1,
        "status": "exact" if meta_ok and hands_ok and extractor == "local_tesseract_named_quadrant_v1" else "wrong_accept",
        "metadata_exact": meta_ok, "hands_exact": hands_ok, "extractor": extractor,
        "negative_crop": negative, "source_sha256": source_sha, "image_sha256": image_sha,
    }


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path); parser.add_argument("--dpi", type=int, default=300); args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="dds3-vubridge-field-") as temp:
        pdf = Path(temp) / "vubridge.pdf"; _download(pdf); source_sha = hashlib.sha256(pdf.read_bytes()).hexdigest(); document = fitz.open(pdf)
        results = []
        for index, page in enumerate(document):
            if not re.search(r"Board\s*#\s*:\s*\d+", page.get_text("text"), re.I):
                continue
            try:
                results.append(_evaluate_page(page, index, source_sha, args.dpi))
            except Exception as exc:
                results.append({"page": index + 1, "status": "field_error", "reason": f"{type(exc).__name__}:{exc}", "source_sha256": source_sha})
    exact = sum(item.get("status") == "exact" for item in results); wrong = sum(item.get("status") == "wrong_accept" for item in results); negative = sum(item.get("negative_crop") == "rejected" for item in results)
    report = {"layout_family": "named_vubridge", "extractor": "local_tesseract_named_quadrant_v1", "real_public_sources": 1, "real_board_pages": len(results), "exact": exact, "wrong_accepts": wrong, "negative_crop_rejected": negative, "truth_source": "embedded source PDF vector text", "dds3_used_for_truth": False, "paid_or_cloud_vision": False, "bridge_inference_repair": False, "render_dpi": args.dpi, "results": results}
    text = json.dumps(report, indent=2, sort_keys=True); print(text)
    if args.output: args.output.write_text(text + "\n", encoding="utf-8")
    return 0 if exact >= 1 and wrong == 0 and negative >= exact else 3


if __name__ == "__main__":
    raise SystemExit(main())
