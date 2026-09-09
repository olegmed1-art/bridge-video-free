import hashlib
import json
from pathlib import Path

import pytest

import bridge_vision.bridgit_holdout_runner as runner


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_package(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    profile = root / "profile.json"
    reference = root / "reference.png"
    frame = root / "frame-001.png"
    profile.write_bytes(b"profile")
    reference.write_bytes(b"reference")
    frame.write_bytes(b"frame")
    package = {
        "schema": runner.RUNNER_INPUT_SCHEMA,
        "recognizer_head_git_sha": "3d8175efffe7c693451033b6bc11ff059a8e367d",
        "recognizer_version": runner.BACKEND_VERSION,
        "recognizer_artifact_sha256": runner.recognizer_artifact_sha256(),
        "cases": [
            {
                "case_id": "holdout-001",
                "profile_id": "bridgit.desktop.test.v1",
                "profile_ref": {"path": "profile.json", "sha256": _sha(profile)},
                "reference_frame_ref": {"path": "reference.png", "sha256": _sha(reference)},
                "frame_refs": [
                    {"path": "frame-001.png", "sha256": _sha(frame), "timestamp_ms": 1000}
                ],
                "teacher_pointer_events": [],
            }
        ],
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


def test_relative_package_is_portable_and_algorithm_output_hash_is_stable(tmp_path, monkeypatch):
    jobs = []

    def execute(job):
        jobs.append(job)
        return _fake_receipt(job)

    monkeypatch.setattr(runner, "execute_shadow_job", execute)
    first = runner.run_package(_make_package(tmp_path / "oracle"))
    second = runner.run_package(_make_package(tmp_path / "ibm"))

    assert first["portable_input_sha256"] == second["portable_input_sha256"]
    assert first["deterministic_run_sha256"] == second["deterministic_run_sha256"]
    assert first["cases"][0]["portable_case_output_sha256"] == second["cases"][0]["portable_case_output_sha256"]
    assert jobs[0]["input_root"] != jobs[1]["input_root"]
    assert jobs[0]["production_write"] is False
    assert jobs[0]["allow_hidden_information"] is False
    assert Path(jobs[0]["profile_ref"]["path"]).is_absolute()


def test_output_records_match_holdout_contract_and_runtime_metrics_exist(tmp_path, monkeypatch):
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
    assert case["runtime_metrics"]["peak_rss_bytes"] > 0
    assert case["runtime_metrics"]["peak_temp_disk_bytes"] >= 0


def test_package_path_escape_and_artifact_mismatch_fail_closed(tmp_path, monkeypatch):
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
