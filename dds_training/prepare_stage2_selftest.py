from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import prepare_stage2 as target


DEAL = "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3"


def _write_fixture(work: Path, *, corpus_count: int) -> None:
    task = {
        "task_id": "S2-CT-1",
        "deal_id": "S2-DEAL-1",
        "board": 10_001,
        "split": "train",
        "task_type": "contract_tricks",
        "deal": DEAL,
        "declarer": 2,
        "strain": 4,
        "leader": 3,
    }
    (work / "blind_tasks.jsonl").write_text(
        json.dumps(task, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (work / "corpus_summary.json").write_text(
        json.dumps({"count": corpus_count}),
        encoding="utf-8",
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="dds-prepare-stage2-selftest-") as td:
        work = Path(td)
        _write_fixture(work, corpus_count=30_000)

        original_analyse = target.analyse_line
        original_readiness = target.audit_stage2_readiness
        try:
            def fake_analyse_line(*, deal: str, declarer: int, trump: int, opening_leader: int, cards: list[str]) -> dict:
                assert deal == DEAL
                assert declarer == 2 and trump == 4 and opening_leader == 3
                assert cards
                digest = hashlib.sha256(" ".join(cards).encode("utf-8")).hexdigest()
                return {
                    "projected_declarer_values": [7, 7],
                    "trajectory": {
                        "first_error": None,
                        "invariant_violations": [],
                        "line_sha256": digest,
                    },
                }

            def fake_readiness(path: Path) -> dict:
                assert path == work
                return {
                    "main_train": {"ready": True},
                    "holdout": {"ready": False},
                    "skill_claim": {"ready": False},
                }

            target.analyse_line = fake_analyse_line
            target.audit_stage2_readiness = fake_readiness
            result = target.prepare(
                work,
                folds=2,
                shard_tasks=2,
                preflight_count=1,
                line_cards=8,
            )
        finally:
            target.analyse_line = original_analyse
            target.audit_stage2_readiness = original_readiness

        assert result["corpus_deals"] == 30_000
        assert result["fresh_main_tasks"] == 1
        assert result["line_preflight_predictions"] == 1
        assert result["continuation_preflight_tasks"] >= 4
        assert result["trajectory_preflight"]["status"] == "ok"
        assert result["readiness"]["main_train"]["ready"] is True
        assert result["mass_training_started"] is False
        assert result["paid_api_used"] is False
        assert (work / "blind_tasks_crossfit_main.jsonl").is_file()
        assert (work / "shard_plan_main.json").is_file()
        assert (work / "stage2_line_preflight_predictions.jsonl").is_file()
        assert (work / "stage2_continuation_preflight.jsonl").is_file()
        assert (work / "family_model_selection_policy.json").is_file()
        assert (work / "stage2_readiness.json").is_file()

        # The production 30k corpus boundary remains fail-closed even though no DDS
        # computation is needed to exercise this safety check.
        blocked = Path(td) / "blocked"
        blocked.mkdir()
        _write_fixture(blocked, corpus_count=10_000)
        try:
            target.prepare(
                blocked,
                folds=2,
                shard_tasks=2,
                preflight_count=1,
                line_cards=8,
            )
        except RuntimeError as exc:
            assert "expanded to 30000 deals" in str(exc)
        else:
            raise AssertionError("Stage-2 preparation accepted a pilot-only corpus")

        print(json.dumps({
            "ok": True,
            "production_prepare_function_executed": True,
            "mass_training_started": False,
            "main_corpus_boundary_fail_closed": True,
            "crossfit_and_shards_written": True,
            "line_and_continuation_preflight_written": True,
        }, indent=2))


if __name__ == "__main__":
    main()
