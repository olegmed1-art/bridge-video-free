import json
import sys
from pathlib import Path

import pytest

from assistant_lab.observer import ObserverConfig, run_daemon, submit_job, validate_job


def test_validate_job_rejects_shell_string():
    with pytest.raises(ValueError):
        validate_job({"experiment_id": "EXP-1", "command": "echo unsafe"})


def test_validate_job_rejects_path_escape_id():
    with pytest.raises(ValueError):
        validate_job({"experiment_id": "../escape", "command": ["true"]})


def test_observer_keeps_tool_and_observer_results_separate(tmp_path: Path):
    pytest.importorskip("psutil")
    cfg = ObserverConfig(tmp_path, poll_seconds=0.01, sample_seconds=0.02)
    code = (
        "from pathlib import Path; import os; "
        "Path('tool-artifact.txt').write_text('tool'); "
        "Path(os.environ['ASSISTANT_LAB_TOOL_OUTPUT_DIR']).joinpath('result.txt').write_text('result')"
    )
    submit_job(cfg, [sys.executable, "-c", code], "EXP-test-1", 30, "isolation test")
    assert run_daemon(cfg, once=True) == 0

    exp = cfg.experiments / "EXP-test-1"
    manifest = json.loads((exp / "manifest.json").read_text())
    report = json.loads((exp / "observer" / "observer_report.json").read_text())

    assert manifest["separation"]["video_analyzer_result_consumed"] is False
    assert manifest["separation"]["other_oracle_tool_results_consumed"] is False
    assert (exp / "oracle_tool" / "tool-artifact.txt").read_text() == "tool"
    assert (exp / "output" / "result.txt").read_text() == "result"
    assert report["exit_code"] == 0
    assert report["tool_result_location"].endswith("/output")
    assert report["observer_result_location"].endswith("/observer")
    assert (exp / "telemetry" / "events.jsonl").exists()
    assert (cfg.done / "EXP-test-1.json").exists()


def test_existing_experiment_is_not_overwritten(tmp_path: Path):
    pytest.importorskip("psutil")
    cfg = ObserverConfig(tmp_path)
    (cfg.experiments / "EXP-existing").mkdir(parents=True)
    with pytest.raises(RuntimeError):
        submit_job(cfg, ["true"], "EXP-existing", 10, "")
