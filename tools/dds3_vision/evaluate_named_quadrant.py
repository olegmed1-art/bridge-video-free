#!/usr/bin/env python3
"""Field gate for a distinct named-seat VuBridge publication layout.

The public PDF is downloaded only during CI. Canonical truth and crop geometry come from
embedded PDF vector text, independently of OCR/DDS3. The tested runtime input is the
rendered PNG crop, and extraction must route through the production local-image ingress.
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
    request = urllib.request.Request(
        URL, headers={"User-Agent": "bridge-school-dds3-field-gate/1.0"}
    )
    with urllib.request.urlopen(request, timeout=45) as response, path.open("wb") as output:
        output.write(response.read())


def _clean_ranks(value: str) -> str:
    value = re.sub(r"\s+", "", value.upper()).replace("10", "T")
    if not value or any(char not in RANKS for char in value):
        raise ValueError(f"invalid source rank text: {value!r}")
    return value


def _metadata_truth(text: str) -> tuple[int, str, str]:
    board = re.search(r"Board\s*#\s*:\s*(\d+)", text, re.IGNORECASE)
    dealer = re.search(r"Dealer\s*:\s*(North|East|South|West)", text, re.IGNORECASE)
    vulnerable = re.search(r"Vulnerable\s*:\s*([^\n\r]+)", text, re.IGNORECASE)
    if not board or not dealer or not vulnerable:
        raise ValueError("source metadata missing")
    vul_raw = re.sub(r"\s+", " ", vulnerable.group(1)).strip().upper()
    vul = VUL.get(vul_raw)
    if vul is None:
        raise ValueError(f"unsupported source vulnerability: {vul_raw!r}")
    return int(board.group(1)), DEALER[dealer.group(1).upper()], vul


def _hand_truth(lines: list[str], seat: str) -> str:
    try:
        start = next(index for index, line in enumerate(lines) if line.strip().lower() == seat.lower())
    except StopIteration as exc:
        raise ValueError(f"source seat label missing: {seat}") from exc
    rows: dict[str, str] = {}
    for raw in lines[start + 1 : start + 12]:
        line = raw.strip()
        if not line:
            continue
        match = re.match(r"^([♠♥♦♣])\s*(.+)$", line)
        if match:
            rows[SUITS[match.group(1)]] = _clean_ranks(match.group(2))
            if len(rows) == 4:
                break
        elif line.lower() in {"north", "west", "east", "south"}:
            break
    if set(rows) != set("SHDC"):
        raise ValueError(f"source hand incomplete for {seat}: {rows}")
    holding = ".".join(rows[suit] for suit in "SHDC")
    if sum(len(part) for part in holding.split(".")) != 13:
        raise ValueError(f"source hand is not 13 cards for {seat}: {holding}")
    return holding


def _truth(page: fitz.Page) -> tuple[int, str, str, dict[str, str]]:
    text = page.get_text("text")
    board, dealer, vulnerability = _metadata_truth(text)
    lines = text.splitlines()
    hands = {seat[0]: _hand_truth(lines, seat) for seat in ("North", "East", "South", "West")}
    cards: list[str] = []
    for seat in "NESW":
        for suit, ranks in zip("SHDC", hands[seat].split("."), strict=True):
            cards.extend(suit + rank for rank in ranks)
    if len(cards) != 52 or len(set(cards)) != 52:
        raise ValueError(f"source deck invalid: {len(cards)}/{len(set(cards))}")
    expected = {suit + rank for suit in "SHDC" for rank in "AKQJT98765432"}
    if set(cards) != expected:
        raise ValueError("source deck is not the standard 52-card deck")
    return board, dealer, vulnerability, hands


def _deal_clip(page: fitz.Page) -> fitz.Rect:
    words = page.get_text("words")
    seat_words = [word for word in words if word[4].strip().lower() in {"north", "west", "east", "south"}]
    if len(seat_words) < 4:
        raise ValueError("source seat headings not found for crop")
    chosen = {}
    for seat in ("north", "west", "east", "south"):
        matches = [word for word in seat_words if word[4].strip().lower() == seat]
        if not matches:
            raise ValueError(f"source seat heading missing for crop: {seat}")
        chosen[seat] = min(matches, key=lambda word: word[1])

    relevant = list(chosen.values())
    for seat, heading in chosen.items():
        center_x = (heading[0] + heading[2]) / 2
        for word in words:
            text = word[4].strip()
            if not text:
                continue
            cy = (word[1] + word[3]) / 2
            cx = (word[0] + word[2]) / 2
            if heading[1] - 3 <= cy <= heading[3] + 105 and abs(cx - center_x) <= 95:
                if text[0] in SUITS or _looks_like_rank_word(text):
                    relevant.append(word)

    for word in words:
        text = word[4].strip().lower().rstrip(":#")
        if text in {"board", "dealer", "vulnerable", "none", "north", "east", "south", "west"}:
            if word[1] < chosen["north"][1] + 10:
                relevant.append(word)

    x0 = min(word[0] for word in relevant); y0 = min(word[1] for word in relevant)
    x1 = max(word[2] for word in relevant); y1 = max(word[3] for word in relevant)
    return fitz.Rect(max(0, x0 - 18), max(0, y0 - 16), min(page.rect.width, x1 + 18), min(page.rect.height, y1 + 18))


def _looks_like_rank_word(text: str) -> bool:
    cleaned = re.sub(r"\s+", "", text.upper()).replace("10", "T")
    return bool(cleaned) and all(char in RANKS for char in cleaned)


def _obs_hands(observation) -> dict[str, str]:
    return {
        seat: ".".join(observation.hands[seat][suit] for suit in "SHDC")
        for seat in "NESW"
    }


def _evaluate_page(page: fitz.Page, page_index: int, source_sha: str, dpi: int) -> dict:
    board, dealer, vulnerability, truth_hands = _truth(page)
    clip = _deal_clip(page)
    pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), clip=clip, alpha=False)
    image_bytes = pix.tobytes("png")
    image_sha = hashlib.sha256(image_bytes).hexdigest()
    try:
        observation = _extract_local_observation(
            image_bytes, media_type="image/png", filename=f"vubridge-board-{board}.png"
        )
    except ImageIngressError as exc:
        return {
            "board": board,
            "page": page_index + 1,
            "status": "rejected",
            "reason": str(exc),
            "source_sha256": source_sha,
            "image_sha256": image_sha,
        }

    observed_hands = _obs_hands(observation)
    meta_ok = (
        int(observation.board_number.value) == board
        and str(observation.dealer.value) == dealer
        and str(observation.vulnerability.value) == vulnerability
    )
    hands_ok = observed_hands == truth_hands
    extractor_field = observation.extra_metadata.get("vision_extractor")
    extractor = str(extractor_field.value) if extractor_field is not None else "missing"

    negative = "not_run"
    try:
        pil = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        severe = pil.crop((0, 0, pil.width, max(1, int(pil.height * 0.70))))
        buffer = io.BytesIO(); severe.save(buffer, format="PNG")
        _extract_local_observation(
            buffer.getvalue(),
            media_type="image/png",
            filename=f"vubridge-board-{board}-severe-crop.png",
        )
        negative = "wrong_accept"
    except ImageIngressError:
        negative = "rejected"

    return {
        "board": board,
        "page": page_index + 1,
        "status": "exact" if meta_ok and hands_ok and extractor == "local_tesseract_named_quadrant_v1" else "wrong_accept",
        "metadata_exact": meta_ok,
        "hands_exact": hands_ok,
        "extractor": extractor,
        "negative_crop": negative,
        "source_sha256": source_sha,
        "image_sha256": image_sha,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dpi", type=int, default=220)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="dds3-vubridge-field-") as temp:
        root = Path(temp)
        pdf = root / "vubridge.pdf"
        _download(pdf)
        source_sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
        document = fitz.open(pdf)
        results = []
        for index, page in enumerate(document):
            text = page.get_text("text")
            if not re.search(r"Board\s*#\s*:\s*\d+", text, re.IGNORECASE):
                continue
            try:
                results.append(_evaluate_page(page, index, source_sha, args.dpi))
            except Exception as exc:
                results.append({"page": index + 1, "status": "field_error", "reason": f"{type(exc).__name__}:{exc}", "source_sha256": source_sha})

    exact = sum(item.get("status") == "exact" for item in results)
    wrong = sum(item.get("status") == "wrong_accept" for item in results)
    negative = sum(item.get("negative_crop") == "rejected" for item in results)
    report = {
        "layout_family": "named_quadrant_vubridge",
        "extractor": "local_tesseract_named_quadrant_v1",
        "real_public_sources": 1,
        "real_board_pages": len(results),
        "exact": exact,
        "wrong_accepts": wrong,
        "negative_crop_rejected": negative,
        "truth_source": "embedded source PDF vector text",
        "dds3_used_for_truth": False,
        "paid_or_cloud_vision": False,
        "bridge_inference_repair": False,
        "results": results,
    }
    text = json.dumps(report, indent=2, sort_keys=True)
    print(text)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0 if exact >= 1 and wrong == 0 and negative >= exact else 3


if __name__ == "__main__":
    raise SystemExit(main())
