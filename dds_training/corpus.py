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


def _write_corpus(n: int, raw_path: Path, manifest_path: Path, seed: int) -> dict:
    rng = random.Random(seed)
    split_map = split_assignment(n, seed)
    sha = hashlib.sha256()
    with raw_path.open("w", encoding="utf-8", newline="\n") as pbn, manifest_path.open("w", encoding="utf-8", newline="\n") as mf:
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
    return {
        "count": n,
        "seed": seed,
        "raw_sha256": sha.hexdigest(),
        "splits": {
            name: sum(1 for v in split_map.values() if v == name)
            for name in SPLIT_RATIOS
        },
    }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def generate_corpus(n: int, out_dir: Path, seed: int = PROJECT_SEED) -> dict:
    """Create or safely expand the deterministic raw corpus.

    Existing work is never silently shrunk or regenerated with another seed.
    Expansion is first built into temporary files, and the old raw PBN/manifest
    must be exact byte prefixes of the new corpus before atomic replacement.
    This lets pilot -> main reuse one work directory and keep its SQLite
    experience/checkpoints while guaranteeing that the original 10k benchmark
    assignments did not change.
    """
    if n < 1:
        raise ValueError("Corpus size must be positive")
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "raw.pbn"
    manifest_path = out_dir / "manifest.jsonl"
    summary_path = out_dir / "corpus_summary.json"

    old_summary = None
    if summary_path.exists() or raw_path.exists() or manifest_path.exists():
        if not (summary_path.exists() and raw_path.exists() and manifest_path.exists()):
            raise ValueError("Partial existing corpus detected; restore all raw/manifest/summary files before expansion")
        old_summary = json.loads(summary_path.read_text(encoding="utf-8"))
        old_n = int(old_summary.get("count", -1))
        old_seed = int(old_summary.get("seed", -1))
        if old_seed != seed:
            raise ValueError(f"Existing corpus seed {old_seed} differs from requested seed {seed}")
        if n < old_n:
            raise ValueError(f"Refusing to shrink deterministic corpus from {old_n} to {n}; use a new work directory")
        validate_pbn_corpus(raw_path, old_n)
        actual_old_sha = _sha256(raw_path)
        if actual_old_sha != old_summary.get("raw_sha256"):
            raise ValueError("Existing raw.pbn SHA-256 does not match corpus_summary.json")
        if n == old_n:
            return {
                **old_summary,
                "raw_pbn": str(raw_path),
                "manifest": str(manifest_path),
                "reused_existing": True,
            }

    tmp_raw = out_dir / ".raw.pbn.new"
    tmp_manifest = out_dir / ".manifest.jsonl.new"
    try:
        generated = _write_corpus(n, tmp_raw, tmp_manifest, seed)
        validate_pbn_corpus(tmp_raw, n)
        if _sha256(tmp_raw) != generated["raw_sha256"]:
            raise ValueError("New corpus SHA-256 mismatch before commit")

        if old_summary is not None:
            old_raw_bytes = raw_path.read_bytes()
            old_manifest_bytes = manifest_path.read_bytes()
            if not tmp_raw.read_bytes().startswith(old_raw_bytes):
                raise ValueError("Expanded raw corpus does not preserve the existing corpus byte-for-byte as a prefix")
            if not tmp_manifest.read_bytes().startswith(old_manifest_bytes):
                raise ValueError("Expanded split manifest changes an existing assignment; expansion aborted")

        tmp_raw.replace(raw_path)
        tmp_manifest.replace(manifest_path)
    finally:
        tmp_raw.unlink(missing_ok=True)
        tmp_manifest.unlink(missing_ok=True)

    summary = {
        **generated,
        "raw_pbn": str(raw_path),
        "manifest": str(manifest_path),
        "reused_existing": False,
        "expanded_from": None if old_summary is None else int(old_summary["count"]),
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
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
        for hand_index, hand in enumerate(hands):
            suits = hand.split(".")
            if len(suits) != 4:
                raise ValueError(f"Wrong suit count in {rec['deal_id']}")
            hand_cards: list[str] = []
            for suit, ranks in zip(SUITS, suits):
                if any(rank not in RANKS for rank in ranks):
                    raise ValueError(f"Invalid rank in {rec['deal_id']} hand {hand_index}")
                hand_cards.extend(suit + r for r in ranks)
            if len(hand_cards) != 13:
                raise ValueError(
                    f"Invalid hand size in {rec['deal_id']} hand {hand_index}: expected 13, got {len(hand_cards)}"
                )
            cards.extend(hand_cards)
        if len(cards) != 52 or len(set(cards)) != 52 or set(cards) != set(DECK):
            raise ValueError(f"Invalid 52-card deal: {rec['deal_id']}")
    if expected_count is not None and count != expected_count:
        raise ValueError(f"Expected {expected_count} deals, found {count}")
    return {"count": count, "unique_ids": len(seen_ids), "ok": True}
