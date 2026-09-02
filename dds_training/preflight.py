from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from corpus import generate_corpus, iter_pbn_records, validate_pbn_corpus
from dds_engine import (
    EXPECTED_DDS_SOURCE_COMMIT,
    contract_tricks_batch,
    engine_info,
)


def _semantic_future_tricks(result: dict) -> dict:
    cards = int(result["cards"])
    return {
        "cards": cards,
        "suit": [int(v) for v in result["suit"][:cards]],
        "rank": [int(v) for v in result["rank"][:cards]],
        "equals": [int(v) for v in result["equals"][:cards]],
        "score": [int(v) for v in result["score"][:cards]],
    }


def check(batch_deals: int = 40, generated: int = 64) -> dict:
    if batch_deals < 1 or batch_deals > 40:
        raise ValueError("DDS batch size must be in [1,40]")
    if generated < batch_deals:
        raise ValueError("Generated corpus must contain at least batch_deals positions")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        summary = generate_corpus(generated, root)
        audit = validate_pbn_corpus(root / "raw.pbn", generated)
        assert audit["count"] == generated
        assert audit["unique_ids"] == generated
        assert audit["ok"] is True

        deals: list[str] = []
        for record in iter_pbn_records(root / "raw.pbn"):
            deals.append(record["deal"])
            if len(deals) == batch_deals:
                break
        assert len(deals) == batch_deals

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
        assert context is not None, "Pinned DDS3 runtime must expose SolverContext"

        # The pinned v3.0.0 Python API supports context reuse on solve_board_pbn,
        # not calc_all_tables_pbn. Exercise the supported API twice on the exact
        # same PBN/context and compare only semantic FutureTricks output.
        first = dds3.solve_board_pbn(
            deals[0],
            trump=4,
            first=0,
            target=-1,
            solutions=3,
            mode=0,
            context=context,
        )
        second = dds3.solve_board_pbn(
            deals[0],
            trump=4,
            first=0,
            target=-1,
            solutions=3,
            mode=0,
            context=context,
        )
        first_semantic = _semantic_future_tricks(first)
        second_semantic = _semantic_future_tricks(second)
        assert first_semantic == second_semantic
        nodes_first = int(first.get("nodes", 0))
        nodes_second = int(second.get("nodes", 0))

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
            "context_reuse": True,
            "context_semantic_repeat_equal": True,
            "context_nodes_first": nodes_first,
            "context_nodes_second": nodes_second,
            "corpus_sha256": summary["raw_sha256"],
            "engine": info,
        }


def run_quick() -> dict:
    """Stable programmatic quick preflight API used by the DDS-backed self-test."""
    return check(batch_deals=40, generated=64)


def main() -> None:
    parser = argparse.ArgumentParser(description="Preflight local DDS3 environment")
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    if shutil.which("git") is None:
        raise SystemExit("git is required")
    report = run_quick() if args.quick else check(batch_deals=40, generated=200)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
