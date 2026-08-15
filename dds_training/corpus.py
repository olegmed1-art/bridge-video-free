from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path

from config import PROJECT_SEED, SEATS, SPLIT_RATIOS, VUL_CYCLE

RANKS = "AKQJT98765432"
SUITS = "SHDC"
DECK = tuple(f"{s}{r}" for s in SUITS for r in RANKS)
DEAL_RE = re.compile(r'^\[Deal\s+"([^"]+)"\]$', re.MULTILINE)
DEAL_ID_RE = re.compile(r'^\[DealID\s+"([^"]+)"\]$', re.MULTILINE)
BOARD_RE = re.compile(r'^\[Board\s+"([^"]+)"\]$', re.MULTILINE)


def _hand_to_pbn(cards: list[str]) -> str:
    by_suit = {s: [] for s in SUITS}
    for card in cards:
        by_suit[card[0]].append(card[1])
    order = {r: i for i, r in enumerate(RANKS)}
    return ".".join("".join(sorted(by_suit[s], key=order.__getitem__)) for s in SUITS)


def _validate_hands(hands: list[list[str]]) -> None:
    assert len(hands) == 4
    assert all(len(h) == 13 for h in hands)
    flat = [c for h in hands for c in h]
    assert len(flat) == 52
    assert len(set(flat)) == 52
    assert set(flat) == set(DECK)


def split_assignment(n: int, seed: int) -> dict[int, str]:
    """Stable 70/15/15 assignment in blocks of 10k.

    Expanding pilot 10k to main 30k never changes the split of the original
    10k deals. Each full 10k block is exactly 7000/1500/1500.
    """
    result: dict[int, str] = {}
    block_size = 10_000
    for block_idx, start in enumerate(range(1, n + 1, block_size)):
        stop = min(n, start + block_size - 1)
        ids = list(range(start, stop + 1))
        random.Random(seed ^ 0xD05D05 ^ (block_idx * 0x9E3779B1)).shuffle(ids)
        m = len(ids)
        n_train = round(m * SPLIT_RATIOS["train"])
        n_val = round(m * SPLIT_RATIOS["validation"])
        for i in ids[:n_train]:
            result[i] = "train"
        for i in ids[n_train:n_train + n_val]:
            result[i] = "validation"
        for i in ids[n_train + n_val:]:
            result[i] = "sealed_test"
    return result


def generate_corpus(n: int, out_dir: Path, seed: int = PROJECT_SEED) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "raw.pbn"
    manifest_path = out_dir / "manifest.jsonl"
    rng = random.Random(seed)
    split_map = split_assignment(n, seed)

    sha = hashlib.sha256()
    with raw_path.open("w", encoding="utf-8", newline="\n") as pbn, manifest_path.open("w", encoding="utf-8") as mf:
        for board in range(1, n + 1):
            cards = list(DECK)
            rng.shuffle(cards)
            hands = [cards[i * 13:(i + 1) * 13] for i in range(4)]
            _validate_hands(hands)
            dealer = SEATS[(board - 1) % 4]
            vul = VUL_CYCLE[(board - 1) % 16]
            deal_id = f"DDS-{seed}-{board:06d}"
            deal = "N:" + " ".join(_hand_to_pbn(h) for h in hands)
            block = (
                '[Event "DDS Training RAW"]\n'
                f'[Board "{board}"]\n'
                f'[Dealer "{dealer}"]\n'
                f'[Vulnerable "{vul}"]\n'
                f'[DealID "{deal_id}"]\n'
                f'[GeneratorSeed "{seed}"]\n'
                f'[Deal "{deal}"]\n\n'
            )
            pbn.write(block)
            sha.update(block.encode("utf-8"))
            mf.write(json.dumps({
                "deal_id": deal_id,
                "board": board,
                "dealer": dealer,
                "vulnerability": vul,
                "split": split_map[board],
            }, ensure_ascii=False) + "\n")

    summary = {
        "count": n,
        "seed": seed,
        "raw_pbn": str(raw_path),
        "manifest": str(manifest_path),
        "raw_sha256": sha.hexdigest(),
        "splits": {
            name: sum(1 for v in split_map.values() if v == name)
            for name in SPLIT_RATIOS
        },
    }
    (out_dir / "corpus_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def iter_pbn_records(path: Path):
    text = path.read_text(encoding="utf-8")
    for block in re.split(r"\n\s*\n", text):
        if not block.strip():
            continue
        dm = DEAL_RE.search(block)
        im = DEAL_ID_RE.search(block)
        bm = BOARD_RE.search(block)
        if dm and im:
            yield {
                "deal_id": im.group(1),
                "board": int(bm.group(1)) if bm else None,
                "deal": dm.group(1),
                "raw": block,
            }


def validate_pbn_corpus(path: Path, expected_count: int | None = None) -> dict:
    seen_ids: set[str] = set()
    count = 0
    for rec in iter_pbn_records(path):
        count += 1
        if rec["deal_id"] in seen_ids:
            raise ValueError(f"Duplicate DealID: {rec['deal_id']}")
        seen_ids.add(rec["deal_id"])
        prefix, hands_text = rec["deal"].split(":", 1)
        if prefix != "N":
            raise ValueError(f"Deal must start N:, got {prefix}")
        hands = hands_text.split()
        if len(hands) != 4:
            raise ValueError(f"Wrong hand count in {rec['deal_id']}")
        cards: list[str] = []
        for hand in hands:
            suits = hand.split(".")
            if len(suits) != 4:
                raise ValueError(f"Wrong suit count in {rec['deal_id']}")
            for suit, ranks in zip(SUITS, suits):
                cards.extend(suit + r for r in ranks)
        if len(cards) != 52 or len(set(cards)) != 52 or set(cards) != set(DECK):
            raise ValueError(f"Invalid 52-card deal: {rec['deal_id']}")
    if expected_count is not None and count != expected_count:
        raise ValueError(f"Expected {expected_count} deals, found {count}")
    return {"count": count, "unique_ids": len(seen_ids), "ok": True}
