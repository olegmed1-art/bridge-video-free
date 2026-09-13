from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

from config import PROJECT_SEED
from corpus import generate_corpus, iter_pbn_records
from dds_engine import contract_tricks_batch
from variants import _parse_deal

# macroxue/bridge-solver prints strain lines as N,S,H,D,C. Its four result
# columns correspond to declarers South, North, West, East because it solves
# opening leaders West, East, North, South respectively.
MACRO_STRAIN_TO_DDS = {"S": 0, "H": 1, "D": 2, "C": 3, "N": 4}
MACRO_COLUMN_TO_DDS_SEAT = (2, 0, 3, 1)  # S,N,W,E -> DDS N,E,S,W indexes
RESULT_RE = re.compile(r"^\s*([NSHDC])\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+", re.MULTILINE)


def _macro_hand(suits: list[str]) -> str:
    return " ".join(cards if cards else "-" for cards in suits)


def _write_macro_deal(deal_pbn: str, path: Path) -> None:
    hands = _parse_deal(deal_pbn)  # 0=N,1=E,2=S,3=W
    north = _macro_hand(hands[0])
    west = _macro_hand(hands[3])
    east = _macro_hand(hands[1])
    south = _macro_hand(hands[2])
    # Four spaces are significant: bridge-solver uses them to split W/E hands.
    path.write_text(f"{north}\n{west}    {east}\n{south}\n", encoding="utf-8")


def solve_independent(solver: Path, deal_pbn: str) -> list[list[int]]:
    with tempfile.TemporaryDirectory() as td:
        deal_file = Path(td) / "deal.txt"
        _write_macro_deal(deal_pbn, deal_file)
        proc = subprocess.run(
            [str(solver), "-f", str(deal_file), "-m", "0"],
            check=True,
            text=True,
            capture_output=True,
            timeout=60,
        )
    rows = RESULT_RE.findall(proc.stdout)
    if len(rows) != 5:
        raise RuntimeError(f"Independent solver returned {len(rows)} result rows, expected 5:\n{proc.stdout}\n{proc.stderr}")

    table = [[-1] * 4 for _ in range(5)]
    for label, *values in rows:
        strain = MACRO_STRAIN_TO_DDS[label]
        vals = [int(v) for v in values]
        for macro_col, dds_seat in enumerate(MACRO_COLUMN_TO_DDS_SEAT):
            table[strain][dds_seat] = vals[macro_col]
    if any(v < 0 for row in table for v in row):
        raise RuntimeError(f"Incomplete independent table: {table}")
    return table


def crosscheck(solver: Path, count: int, seed: int) -> dict:
    if count < 1:
        raise ValueError("count must be >= 1")
    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / "corpus"
        generate_corpus(count, work, seed)
        records = list(iter_pbn_records(work / "raw.pbn"))
        deals = [r["deal"] for r in records]
        dds_tables = contract_tricks_batch(deals)
        mismatches = []
        for rec, dds in zip(records, dds_tables):
            independent = solve_independent(solver, rec["deal"])
            cells = []
            for strain in range(5):
                for seat in range(4):
                    a = int(dds[strain][seat])
                    b = int(independent[strain][seat])
                    if a != b:
                        cells.append({"strain": strain, "seat": seat, "dds3": a, "independent": b})
            if cells:
                mismatches.append({"deal_id": rec["deal_id"], "cells": cells})
    return {
        "ok": not mismatches,
        "count": count,
        "seed": seed,
        "checked_cells": count * 20,
        "mismatched_deals": len(mismatches),
        "mismatches": mismatches[:10],
        "independent_solver": str(solver),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Cross-check DDS3 against an independent local bridge solver")
    p.add_argument("--solver", type=Path, required=True)
    p.add_argument("--count", type=int, default=4)
    p.add_argument("--seed", type=int, default=PROJECT_SEED ^ 0xC055)
    args = p.parse_args()
    result = crosscheck(args.solver.resolve(), args.count, args.seed)
    print(json.dumps(result, indent=2))
    if not result["ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
