from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from corpus import generate_corpus, validate_corpus
from dds_engine import (
    EXPECTED_DDS_SOURCE_COMMIT,
    contract_tricks_batch,
    engine_info,
)


def check(batch_deals: int = 40, generated: int = 64) -> dict:
    if batch_deals < 1 or batch_deals > 40:
        raise ValueError("DDS batch size must be in [1,40]")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        summary = generate_corpus(generated, root)
        audit = validate_corpus(root / "raw.pbn", root / "manifest.jsonl")
        assert audit["count"] == generated

        deals = []
        for line in (root / "manifest.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            deals.append(row["deal"])
            if len(deals) == batch_deals:
                break
        tables = contract_tricks_batch(deals)
        assert len(tables) == batch_deals
        for table in tables:
            assert len(table) == 5
            assert all(len(row) == 4 for row in table)
            assert all(0 <= int(value) <= 13 for row in table for value in row)

        try:
            contract_tricks_batch((deals * 2)[:41])
        except Exception:
            over_limit_blocked = True
        else:
            over_limit_blocked = False
        assert over_limit_blocked

        import dds3

        context = dds3.SolverContext() if hasattr(dds3, "SolverContext") else None
        if context is not None:
            first = dds3.calc_all_tables_pbn(
                deals[:1],
                mode=-1,
                trump_filter=(0, 0, 0, 0, 0),
                context=context,
            )
            second = dds3.calc_all_tables_pbn(
                deals[:1],
                mode=-1,
                trump_filter=(0, 0, 0, 0, 0),
                context=context,
            )
            assert first["tables"][0]["res_table"] == second["tables"][0]["res_table"]

        info = engine_info()
        assert info["solver_context"] is True, info
        assert info["dds_source_commit"] == EXPECTED_DDS_SOURCE_COMMIT, info
        assert info["dds_provenance_verified"] is True, info
        assert info["dds_wheel_sha256"], info

        return {
            "ok": True,
            "generated_and_validated": generated,
            "dds_batch_tables": len(tables),
            "dds_batch_limit": 40,
            "dds_over_limit_blocked": over_limit_blocked,
            "dds_reported_no_of_boards": int(
                dds3.calc_all_tables_pbn(
                    deals,
                    mode=-1,
                    trump_filter=(0, 0, 0, 0, 0),
                )["no_of_boards"]
            ),
            "context_reuse": context is not None,
            "corpus_sha256": summary["raw_sha256"],
            "engine": info,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Preflight local DDS3 environment")
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    if shutil.which("git") is None:
        raise SystemExit("git is required")
    report = check(batch_deals=40, generated=64 if args.quick else 200)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
