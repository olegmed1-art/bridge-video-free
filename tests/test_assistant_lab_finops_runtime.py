from __future__ import annotations

from pathlib import Path
from unittest import mock

import psycopg

from assistant_lab.finops_runtime import record_missing_terminal_usage


def test_finops_db_failure_is_nonfatal() -> None:
    with mock.patch("assistant_lab.finops_runtime.psycopg.connect", side_effect=psycopg.OperationalError("down")):
        assert record_missing_terminal_usage("postgresql://invalid") == 0


def test_worker_calls_finops_before_and_after_job() -> None:
    text = (Path(__file__).resolve().parents[1] / "assistant_lab" / "worker.py").read_text(encoding="utf-8")
    block = text[text.index("def process_one"):text.index("def wait_for_wakeup")]
    assert block.count("record_missing_terminal_usage(config.dsn)") == 2
    assert block.index("record_missing_terminal_usage(config.dsn)") < block.index("job = claim_one(config)")
    assert block.rindex("record_missing_terminal_usage(config.dsn)") > block.index("mark_completed(job, result, config)")


def test_finops_writer_is_bounded_and_job_idempotent() -> None:
    text = (Path(__file__).resolve().parents[1] / "assistant_lab" / "finops_runtime.py").read_text(encoding="utf-8")
    assert "min(int(limit), 1000)" in text
    assert "NOT EXISTS" in text
    assert "WHERE f.job_id = j.job_id" in text
    assert "estimated_cost_usd" not in text
    assert "runtime_observed_cost_pending" in text
