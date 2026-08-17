from __future__ import annotations

import json
import tempfile
from pathlib import Path

from config import PROJECT_SEED
from corpus import generate_corpus, iter_pbn_records, validate_pbn_corpus


def _write_malformed_14_12(source_pbn: Path, out: Path) -> None:
    rec = next(iter(iter_pbn_records(source_pbn)))
    prefix, rest = rec["deal"].split(":", 1)
    hands = [h.split(".") for h in rest.split()]
    moved = False
    for suit in range(4):
        if hands[1][suit]:
            rank = hands[1][suit][0]
            hands[1][suit] = hands[1][suit][1:]
            hands[0][suit] += rank
            moved = True
            break
    assert moved
    deal = prefix + ":" + " ".join(".".join(h) for h in hands)
    out.write_text(
        '[Event "Malformed"]\n[Board "1"]\n[DealID "BAD-14-12"]\n'
        f'[Deal "{deal}"]\n\n',
        encoding="utf-8",
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        work = root / "work"
        pilot = generate_corpus(10_000, work, PROJECT_SEED)
        pilot_raw = (work / "raw.pbn").read_bytes()
        pilot_manifest = (work / "manifest.jsonl").read_bytes()
        assert pilot["splits"] == {"train": 7000, "validation": 1500, "sealed_test": 1500}

        malformed = root / "malformed.pbn"
        _write_malformed_14_12(work / "raw.pbn", malformed)
        malformed_blocked = False
        try:
            validate_pbn_corpus(malformed, 1)
        except ValueError as exc:
            malformed_blocked = "Invalid hand size" in str(exc)
        assert malformed_blocked, "14/12/13/13 PBN unexpectedly passed validation"

        same = generate_corpus(10_000, work, PROJECT_SEED)
        assert same["reused_existing"] is True
        assert (work / "raw.pbn").read_bytes() == pilot_raw
        assert (work / "manifest.jsonl").read_bytes() == pilot_manifest

        main = generate_corpus(30_000, work, PROJECT_SEED)
        assert main["expanded_from"] == 10_000
        assert main["splits"] == {"train": 21_000, "validation": 4500, "sealed_test": 4500}
        assert (work / "raw.pbn").read_bytes().startswith(pilot_raw)
        assert (work / "manifest.jsonl").read_bytes().startswith(pilot_manifest)
        validate_pbn_corpus(work / "raw.pbn", 30_000)

        shrink_blocked = seed_change_blocked = False
        try:
            generate_corpus(10_000, work, PROJECT_SEED)
        except ValueError:
            shrink_blocked = True
        try:
            generate_corpus(30_000, work, PROJECT_SEED + 1)
        except ValueError:
            seed_change_blocked = True
        assert shrink_blocked and seed_change_blocked

        print(json.dumps({
            "ok": True,
            "pilot_to_main_prefix_preserved": True,
            "pilot": pilot["splits"],
            "main": main["splits"],
            "malformed_14_12_blocked": malformed_blocked,
            "shrink_blocked": shrink_blocked,
            "seed_change_blocked": seed_change_blocked,
        }, indent=2))


if __name__ == "__main__":
    main()
