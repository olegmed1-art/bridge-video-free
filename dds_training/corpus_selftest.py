from __future__ import annotations

import json
import tempfile
from pathlib import Path

from config import PROJECT_SEED
from corpus import generate_corpus, validate_pbn_corpus


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / "work"
        pilot = generate_corpus(10_000, work, PROJECT_SEED)
        pilot_raw = (work / "raw.pbn").read_bytes()
        pilot_manifest = (work / "manifest.jsonl").read_bytes()
        assert pilot["splits"] == {"train": 7000, "validation": 1500, "sealed_test": 1500}

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
            "shrink_blocked": shrink_blocked,
            "seed_change_blocked": seed_change_blocked,
        }, indent=2))


if __name__ == "__main__":
    main()
