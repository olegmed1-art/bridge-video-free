import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from assistant_lab.observer import ObserverConfig, append_knowledge, run_daemon, submit_job, validate_job


def source_fixture(tmp_path: Path) -> tuple[Path, str]:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    source = source_root / "test-video.bin"
    source.write_bytes(b"fixed-test-video")
    return source, hashlib.sha256(source.read_bytes()).hexdigest()


def valid_job(source: Path, digest: str) -> dict:
    return {
        "experiment_id": "EXP-1", "tool_id": "oracle-tool-a",
        "source": {"path": str(source), "sha256": digest}, "command": ["true"],
    }


def require_working_bwrap() -> None:
    try:
        probe = subprocess.run(
            ["bwrap", "--die-with-parent", "--ro-bind", "/", "/", "--", "true"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
    except FileNotFoundError:
        pytest.skip("bubblewrap is not installed")
    if probe.returncode != 0:
        pytest.skip("this container does not permit bubblewrap namespaces")


def test_validate_job_rejects_shell_string_and_path_escape(tmp_path: Path):
    source, digest = source_fixture(tmp_path)
    job = valid_job(source, digest)
    job["command"] = "echo unsafe"
    with pytest.raises(ValueError):
        validate_job(job)
    job = valid_job(source, digest)
    job["experiment_id"] = "../escape"
    with pytest.raises(ValueError):
        validate_job(job)


def test_validate_job_requires_exact_source_and_rejects_reserved_env(tmp_path: Path):
    source, digest = source_fixture(tmp_path)
    job = valid_job(source, digest)
    job["source"]["path"] += "*"
    with pytest.raises(ValueError):
        validate_job(job)
    job = valid_job(source, digest)
    job["env"] = {"HOME": "/shared"}
    with pytest.raises(ValueError):
        validate_job(job)


def test_observer_separates_results_and_archives_sealed_run(tmp_path: Path):
    pytest.importorskip("psutil")
    require_working_bwrap()
    source, digest = source_fixture(tmp_path)
    archive = tmp_path / "durable-archive"
    cfg = ObserverConfig(
        tmp_path / "state", source_root=source.parent, archive_root=archive,
        require_archive=True, poll_seconds=0.01, sample_seconds=0.02,
    )
    code = (
        "from pathlib import Path; import os; "
        "assert Path(os.environ['ASSISTANT_LAB_SOURCE_PATH']).read_bytes() == b'fixed-test-video'; "
        "Path('tool-artifact.txt').write_text('tool'); "
        "Path(os.environ['ASSISTANT_LAB_TOOL_OUTPUT_DIR']).joinpath('result.txt').write_text('result')"
    )
    submit_job(cfg, [sys.executable, "-c", code], "EXP-test-1", 30, "isolation test",
               "oracle-tool-a", str(source), digest)
    assert run_daemon(cfg, once=True) == 0

    exp = cfg.experiments / "EXP-test-1"
    manifest = json.loads((exp / "manifest.json").read_text())
    report = json.loads((exp / "observer" / "observer_report.json").read_text())
    assert manifest["source"]["sha256"] == digest
    assert manifest["separation"]["video_analyzer_result_consumed"] is False
    assert manifest["separation"]["other_oracle_tool_results_consumed"] is False
    assert (exp / "oracle_tool" / "tool-artifact.txt").read_text() == "tool"
    assert (exp / "output" / "result.txt").read_text() == "result"
    assert report["exit_code"] == 0 and report["sealed"] is True
    assert report["archive_status"] == "PENDING"
    assert (exp / "SEALED.json").is_file()
    assert (archive / "EXP-test-1" / "SEALED.json").is_file()
    assert (cfg.done / "EXP-test-1.json").exists()
    result = json.loads((cfg.done / "EXP-test-1.result.json").read_text())
    assert result["archive_status"] == "COPIED"

    note = append_knowledge(
        cfg, "EXP-test-1", "INFERRED", "The tool likely uses a staged transform.",
        ["telemetry/events.jsonl#process_started"], "Internal implementation is not observable.",
    )
    assert note.name == "inferred.jsonl"


def test_source_outside_root_and_wrong_hash_fail_closed(tmp_path: Path):
    pytest.importorskip("psutil")
    source, digest = source_fixture(tmp_path)
    cfg = ObserverConfig(tmp_path / "state", source_root=tmp_path / "different", poll_seconds=0.01)
    submit_job(cfg, ["true"], "EXP-outside", 10, "", "tool", str(source), digest)
    run_daemon(cfg, once=True)
    assert (cfg.failed / "EXP-outside.error.json").is_file()

    cfg2 = ObserverConfig(tmp_path / "state-2", source_root=source.parent, poll_seconds=0.01)
    submit_job(cfg2, ["true"], "EXP-hash", 10, "", "tool", str(source), "0" * 64)
    run_daemon(cfg2, once=True)
    error = json.loads((cfg2.failed / "EXP-hash.error.json").read_text())
    assert "SHA-256 mismatch" in error["error"]


def test_archive_requirement_and_existing_experiment(tmp_path: Path):
    pytest.importorskip("psutil")
    source, digest = source_fixture(tmp_path)
    cfg = ObserverConfig(tmp_path / "state", source_root=source.parent, require_archive=True)
    submit_job(cfg, ["true"], "EXP-no-archive", 10, "", "tool", str(source), digest)
    with pytest.raises(RuntimeError, match="durable observer archive"):
        run_daemon(cfg, once=True)
    (cfg.experiments / "EXP-existing").mkdir(parents=True)
    with pytest.raises(RuntimeError):
        submit_job(cfg, ["true"], "EXP-existing", 10, "", "tool", str(source), digest)
