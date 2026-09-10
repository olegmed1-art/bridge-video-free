import hashlib
import json
import os
from pathlib import Path

import pytest

import bridge_vision.bridgit_holdout_runner as runner


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stub_runtime(monkeypatch) -> None:
    monkeypatch.setattr(
        runner,
        "_installed_runtime_versions",
        lambda: dict(runner.PINNED_RUNTIME_VERSIONS),
    )


def _make_package(root: Path, *, case_count: int = 1) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    profile = root / "profile.json"
    reference = root / "reference.png"
    profile.write_bytes(b"profile")
    reference.write_bytes(b"reference")
    cases = []
    for index in range(case_count):
        frame = root / f"frame-{index + 1:03d}.png"
        frame.write_bytes(f"frame-{index + 1}".encode())
        cases.append(
            {
                "case_id": f"holdout-{index + 1:03d}",
                "profile_id": "bridgit.desktop.test.v1",
                "profile_ref": {"path": "profile.json", "sha256": _sha(profile)},
                "reference_frame_ref": {
                    "path": "reference.png",
                    "sha256": _sha(reference),
                },
                "frame_refs": [
                    {
                        "path": frame.name,
                        "sha256": _sha(frame),
                        "timestamp_ms": 1000 + index,
                    }
                ],
                "teacher_pointer_events": [],
            }
        )
    package = {
        "schema": runner.RUNNER_INPUT_SCHEMA,
        "runner_version": runner.RUNNER_VERSION,
        "recognizer_head_git_sha": runner.ALGORITHM_BASELINE_GIT_SHA,
        "recognizer_version": runner.BACKEND_VERSION,
        "recognizer_artifact_sha256": runner.frozen_recognizer_artifact_sha256(),
        "cases": cases,
    }
    path = root / "RECOGNIZER_HOLDOUT_V1.json"
    path.write_text(json.dumps(package, sort_keys=True), encoding="utf-8")
    return path


def _fake_receipt(_job):
    return {
        "result": {
            "status": "PENDING_TEMPORAL_CONSENSUS",
            "result_scope": "SHADOW_ONLY",
            "canonical_promotion_allowed": False,
            "deal_evidence_report": {
                "status": "PARTIAL",
                "card_records": [
                    {
                        "seat": "N",
                        "suit": "H",
                        "rank": "A",
                        "source": "VISUAL",
                        "frame_sha256": "1" * 64,
                        "confidence": 0.91,
                        "recognizer_version": runner.BACKEND_VERSION,
                    },
                    {
                        "seat": "N",
                        "suit": None,
                        "rank": None,
                        "source": "UNKNOWN",
                        "frame_sha256": None,
                        "confidence": 0.0,
                        "recognizer_version": runner.BACKEND_VERSION,
                    },
                ],
                "conflicts": [],
            },
        }
    }


def test_relative_package_is_portable_and_algorithm_output_hash_is_stable(
    tmp_path, monkeypatch
):
    _stub_runtime(monkeypatch)
    jobs = []

    def execute(job):
        jobs.append(job)
        return _fake_receipt(job)

    monkeypatch.setattr(runner, "execute_shadow_job", execute)
    first = runner.run_package(_make_package(tmp_path / "oracle"))
    second = runner.run_package(_make_package(tmp_path / "ibm"))

    assert first["portable_input_sha256"] == second["portable_input_sha256"]
    assert first["deterministic_run_sha256"] == second["deterministic_run_sha256"]
    assert (
        first["cases"][0]["portable_case_output_sha256"]
        == second["cases"][0]["portable_case_output_sha256"]
    )
    assert jobs[0]["input_root"] != jobs[1]["input_root"]
    assert jobs[0]["production_write"] is False
    assert jobs[0]["allow_hidden_information"] is False
    assert Path(jobs[0]["profile_ref"]["path"]).is_absolute()


def test_output_records_match_contract_and_ram_is_run_level(tmp_path, monkeypatch):
    _stub_runtime(monkeypatch)
    monkeypatch.setattr(runner, "execute_shadow_job", _fake_receipt)
    report = runner.run_package(_make_package(tmp_path / "run"))
    case = report["cases"][0]

    assert set(case["card_records"][0]) == {
        "seat",
        "suit",
        "rank",
        "confidence",
        "provenance",
        "frame_sha256",
        "recognizer_version",
        "UNKNOWN",
    }
    assert case["UNKNOWN_count"] == 1
    assert case["duplicate_card_count"] == 0
    assert case["card_records"][1]["UNKNOWN"] is True
    assert case["card_records"][1]["rank"] is None
    assert case["card_records"][1]["suit"] is None
    assert case["runtime_metrics"]["wall_seconds"] >= 0
    assert case["runtime_metrics"]["cpu_seconds"] >= 0
    assert "peak_rss_bytes" not in case["runtime_metrics"]
    assert case["runtime_metrics"]["peak_temp_disk_bytes"] >= 0
    assert report["runtime_metrics"]["peak_rss_bytes"] > 0


def test_package_path_escape_and_artifact_mismatch_fail_closed(tmp_path, monkeypatch):
    _stub_runtime(monkeypatch)
    package_path = _make_package(tmp_path / "case")
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["cases"][0]["profile_ref"]["path"] = "../outside.json"
    package_path.write_text(json.dumps(package), encoding="utf-8")
    with pytest.raises(runner.HoldoutRunnerError, match="escapes package root"):
        runner.run_package(package_path)

    package_path = _make_package(tmp_path / "mismatch")
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["recognizer_artifact_sha256"] = "0" * 64
    package_path.write_text(json.dumps(package), encoding="utf-8")
    monkeypatch.setattr(runner, "execute_shadow_job", _fake_receipt)
    with pytest.raises(runner.HoldoutRunnerError, match="artifact does not match"):
        runner.run_package(package_path)


def test_runner_rejects_nonbaseline_head_and_runtime_version_mismatch(
    tmp_path, monkeypatch
):
    package_path = _make_package(tmp_path / "head")
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["recognizer_head_git_sha"] = "a" * 40
    package_path.write_text(json.dumps(package), encoding="utf-8")
    _stub_runtime(monkeypatch)
    with pytest.raises(runner.HoldoutRunnerError, match="frozen algorithm baseline"):
        runner.run_package(package_path)

    package_path = _make_package(tmp_path / "runtime")
    monkeypatch.setattr(
        runner,
        "_installed_runtime_versions",
        lambda: {"numpy": "2.3.1", "opencv-python-headless": "5.0.0.93"},
    )
    with pytest.raises(runner.HoldoutRunnerError, match="runtime versions"):
        runner.run_package(package_path)


def test_runner_rejects_modified_algorithm_source_identity(tmp_path, monkeypatch):
    _stub_runtime(monkeypatch)
    package_path = _make_package(tmp_path / "source")
    altered = dict(runner._BASELINE_BLOB_SHAS)
    altered["bridge_vision/bridgit_rank_layout.py"] = "0" * 40
    monkeypatch.setattr(runner, "_current_baseline_blob_shas", lambda: altered)
    with pytest.raises(runner.HoldoutRunnerError, match="source does not match"):
        runner.run_package(package_path)


def test_package_reader_rejects_fifo_and_oversized_file(tmp_path):
    if hasattr(os, "mkfifo"):
        fifo = tmp_path / "package.fifo"
        os.mkfifo(fifo)
        with pytest.raises(runner.HoldoutRunnerError, match="regular file"):
            runner._load_package(fifo)

    oversized = tmp_path / "oversized.json"
    with oversized.open("wb") as stream:
        stream.seek(runner.MAX_PACKAGE_BYTES)
        stream.write(b"x")
    with pytest.raises(runner.HoldoutRunnerError, match="exceeds size limit"):
        runner._load_package(oversized)


def test_cli_rejects_output_aliases_before_execution(tmp_path, monkeypatch):
    _stub_runtime(monkeypatch)
    monkeypatch.setattr(runner, "execute_shadow_job", _fake_receipt)

    package_path = _make_package(tmp_path / "package-alias")
    original = package_path.read_bytes()
    assert runner.main(["--package", str(package_path), "--output", str(package_path)]) == 2
    assert package_path.read_bytes() == original

    package_path = _make_package(tmp_path / "hardlink-alias")
    frame = package_path.parent / "frame-001.png"
    alias = package_path.parent / "output.json"
    os.link(frame, alias)
    original_frame = frame.read_bytes()
    assert runner.main(["--package", str(package_path), "--output", str(alias)]) == 2
    assert frame.read_bytes() == original_frame


def test_cli_writes_output_atomically_without_changing_deterministic_hash(
    tmp_path, monkeypatch
):
    _stub_runtime(monkeypatch)
    monkeypatch.setattr(runner, "execute_shadow_job", _fake_receipt)
    package_path = _make_package(tmp_path / "ok")
    output = tmp_path / "result" / "run.json"
    assert runner.main(["--package", str(package_path), "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["runner_version"] == runner.RUNNER_VERSION
    assert report["deterministic_run_sha256"]
