from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from corpus import generate_corpus, iter_pbn_records, validate_pbn_corpus
from dds_engine import engine_info


def run_quick() -> dict:
    import dds3

    with tempfile.TemporaryDirectory(prefix="dds-preflight-") as td:
        root = Path(td)
        generate_corpus(64, root)
        validate_pbn_corpus(root / "raw.pbn", 64)
        recs = list(iter_pbn_records(root / "raw.pbn"))
        deals = [r["deal"] for r in recs[:8]]
        assert len(deals) == 8
        assert all(d.startswith("N:") and len(d.split()) == 4 for d in deals)

        # DDS3 batch table smoke test.
        tables = dds3.calc_all_tables_pbn(deals[:4], mode=-1, trump_filter=(0, 0, 0, 0, 0))
        assert int(tables["no_of_boards"]) == 4
        assert len(tables["tables"]) == 4
        for t in tables["tables"]:
            matrix = t["res_table"]
            assert len(matrix) == 5 and all(len(row) == 4 for row in matrix)
            assert all(0 <= int(v) <= 13 for row in matrix for v in row)

        # Context-aware solve smoke test. Reusing a context is required by the
        # training algorithm for repeated branches of the same deal.
        assert hasattr(dds3, "SolverContext"), "DDS3 SolverContext is required"
        ctx = dds3.SolverContext()
        r1 = dds3.solve_board_pbn(deals[0], trump=4, first=0, solutions=3, context=ctx)
        r2 = dds3.solve_board_pbn(deals[0], trump=4, first=0, solutions=3, context=ctx)
        assert int(r1["cards"]) > 0 and int(r2["cards"]) > 0
        n = int(r1["cards"])
        assert list(r1["score"])[:n] == list(r2["score"])[:n]

        return {
            "ok": True,
            "generated_and_validated": 64,
            "dds_batch_tables": 4,
            "context_reuse": True,
            "engine": engine_info(),
        }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--quick", action="store_true", help="Run non-training technical smoke checks")
    args = p.parse_args()
    if not args.quick:
        raise SystemExit("Only --quick preflight is implemented; this command never starts training")
    print(json.dumps(run_quick(), indent=2))


if __name__ == "__main__":
    main()
