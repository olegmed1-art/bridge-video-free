"""Evidence-recorder tests; tiny pixel arrays are not a card accuracy benchmark."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

RUNNER = Path(__file__).parents[1] / "tools" / "recognizer_compare.py"
spec = importlib.util.spec_from_file_location("comparison_runner", RUNNER)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def read_events(root):
    return [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()]


def fake_module():
    image = np.full((4, 5, 3), 73, dtype=np.uint8)
    class Selector:
        def observe(self, signature, timestamp):
            return None
    def write_png(path, frame):
        ok, data = cv2.imencode(".png", frame)
        assert ok
        Path(path).write_bytes(data.tobytes())
        return runner.digest(path)
    def recognize_video_primary():
        return None
    return SimpleNamespace(
        _write_png=write_png, _frame_at=lambda *_: image,
        _full_geometry_gate=lambda *_: None,
        EventFrameSelector=Selector,
        recognize_frames_with_original_gambler_deck=lambda *a, **k: {"status": "REJECTED"},
        _accepted_primary_result=lambda result: result["status"] == "ACCEPTED",
        recognize_video_primary=recognize_video_primary,
        rank_layout=SimpleNamespace(_pixel_runtime=lambda: (cv2, np)),
    )


@pytest.mark.parametrize("failure", [False, True])
def test_attempt_pixels_survive_temporary_cleanup_and_preserve_backend_result(tmp_path, failure):
    module = fake_module()
    sentinel = ValueError("duplicate decoded frame pixels")
    result = {"status": "ACCEPTED", "hands": {}}
    def backend(*args, **kwargs):
        if failure:
            raise sentinel
        return result
    module.recognize_frames_with_original_gambler_deck = backend
    out = tmp_path / "evidence"
    with runner.Recorder(module, out) as recorder:
        with tempfile.TemporaryDirectory(dir=tmp_path) as directory:
            paths = [Path(directory) / (str(i) + ".png") for i in range(2)]
            for i, path in enumerate(paths):
                module._write_png(path, np.full((4, 5, 3), i, dtype=np.uint8))
            hashes = [runner.digest(x) for x in paths]
            kwargs = {"expected_frame_sha256s": hashes, "observation_timestamps_ms": [1000, 1600]}
            if failure:
                with pytest.raises(ValueError) as error:
                    module.recognize_frames_with_original_gambler_deck(None, paths, None, **kwargs)
                assert error.value is sentinel
            else:
                returned = module.recognize_frames_with_original_gambler_deck(None, paths, None, **kwargs)
                assert returned is result
                assert module._accepted_primary_result(returned)
            assert [runner.digest(x) for x in paths] == hashes
        assert not paths[0].exists()
    pair = read_events(out)[0]
    assert pair["event"] == "PAIR_CAPTURED"
    assert [runner.digest(out / x["path"]) for x in pair["frames"]] == hashes
    events = read_events(out)
    assert events[-1]["event"] == ("BACKEND_ERROR" if failure else "ACCEPTANCE_RESULT")
    assert module.recognize_frames_with_original_gambler_deck is backend


def test_geometry_rejection_retains_decoded_frame_without_changing_pixels(tmp_path):
    module = fake_module()
    original = module._frame_at(None, 0)
    capture = SimpleNamespace(get=lambda _: 1234.5)
    with runner.Recorder(module, tmp_path / "evidence", offset=5000):
        frame = module._frame_at(capture, 1000)
        assert frame is original
        assert module._full_geometry_gate(frame, None, None) is None
        assert module.EventFrameSelector().observe(None, 1000) is None
    events = read_events(tmp_path / "evidence")
    assert events[0]["requested_source_ms"] == 6000
    assert events[0]["pts_verified"] is False
    assert events[0]["decoder_reported_position_ms"] == 1234.5
    assert events[1]["geometry"] is None
    retained = cv2.imread(str(tmp_path / "evidence" / events[0]["evidence"]["path"]))
    np.testing.assert_array_equal(retained, original)


def test_evidence_limit_escapes_historical_exception_handlers_and_restores_hooks(tmp_path):
    module = fake_module()
    before = module._frame_at
    with pytest.raises(runner.EvidenceError, match="frame evidence limit"):
        with runner.Recorder(module, tmp_path / "evidence", max_frames=0):
            try:
                module._frame_at(SimpleNamespace(get=lambda _: 0), 0)
            except Exception:
                pytest.fail("historical broad catch swallowed recorder failure")
    assert module._frame_at is before


def test_previous_attempt_remains_after_next_duplicate_rejection(tmp_path):
    module = fake_module()
    calls = []
    def backend(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise ValueError("duplicate decoded frame pixels")
        return {"status": "ACCEPTED"}
    module.recognize_frames_with_original_gambler_deck = backend
    frames = [tmp_path / (str(i) + ".png") for i in range(2)]
    for i, path in enumerate(frames):
        module._write_png(path, np.full((4, 5, 3), i, dtype=np.uint8))
    with runner.Recorder(module, tmp_path / "evidence"):
        for attempt in range(2):
            kwargs = dict(expected_frame_sha256s=[runner.digest(x) for x in frames],
                          observation_timestamps_ms=[attempt*1000, attempt*1000+600])
            if attempt:
                with pytest.raises(ValueError):
                    module.recognize_frames_with_original_gambler_deck(None, frames, None, **kwargs)
            else:
                value = module.recognize_frames_with_original_gambler_deck(None, frames, None, **kwargs)
                assert module._accepted_primary_result(value)
    assert (tmp_path / "evidence/attempts/00001/backend-result.json").is_file()
    assert (tmp_path / "evidence/attempts/00002/frame-0.png").is_file()


@pytest.fixture
def sealed_case(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "verify_checkout", lambda *args: None)
    baseline, candidate = tmp_path / "baseline-root", tmp_path / "candidate-root"
    baseline.mkdir()
    candidate.mkdir()
    inputs = {}
    for name in ("video", "reference", "profile", "sprite", "gold"):
        path = tmp_path / (name + ".dat")
        path.write_bytes((name + "-synthetic-test-only").encode())
        inputs[name] = {"path": str(path), "sha256": runner.digest(path)}
    manifest = dict(schema=runner.SCHEMA, case_id="sample", source_offset_ms=10000,
                    gold_frozen_before_outputs=True, inputs=inputs,
                    baseline={"root": str(baseline), "sha": "a"*40},
                    candidate={"root": str(candidate), "sha": "b"*40})
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path, manifest


def test_preflight_seals_gold_but_children_never_receive_it(sealed_case, tmp_path):
    path, manifest = sealed_case
    receipt, configs = runner.prepare(path, runner.digest(path), tmp_path / "out")
    assert receipt["gold_sha256"] == manifest["inputs"]["gold"]["sha256"]
    configs = [json.loads(x.read_text()) for x in configs.values()]
    assert configs[0]["job_id"] != configs[1]["job_id"]
    assert configs[0]["output"] != configs[1]["output"]
    assert configs[0]["inputs"] == configs[1]["inputs"]
    assert all("gold" not in x["inputs"] for x in configs)
    assert all(manifest["inputs"]["gold"]["path"] not in json.dumps(x) for x in configs)


@pytest.mark.parametrize("failure", ["manifest", "gold", "existing-output", "same-root", "unsealed"])
def test_preflight_refuses_invalid_comparisons_before_launch(sealed_case, tmp_path, failure):
    path, manifest = sealed_case
    output = tmp_path / "out"
    if failure == "gold":
        Path(manifest["inputs"]["gold"]["path"]).write_text("changed")
    elif failure == "existing-output":
        output.mkdir()
    elif failure == "same-root":
        manifest["candidate"]["root"] = manifest["baseline"]["root"]
    elif failure == "unsealed":
        manifest["gold_frozen_before_outputs"] = False
    path.write_text(json.dumps(manifest))
    seal = "0"*64 if failure == "manifest" else runner.digest(path)
    with pytest.raises(ValueError):
        runner.prepare(path, seal, output)


@pytest.mark.parametrize("baseline_status", ["ERROR", "RETURNED"])
def test_comparison_runs_both_processes_even_if_baseline_fails(sealed_case, tmp_path, monkeypatch, baseline_status):
    path, _ = sealed_case
    calls = []
    def run(command, **kwargs):
        config = json.loads(Path(command[-1]).read_text())
        calls.append(config)
        state = {"status": baseline_status if len(calls) == 1 else "RETURNED"}
        runner.write_json(Path(config["output"]) / "worker-status.json", state)
        return SimpleNamespace(returncode=1 if len(calls) == 1 else 0)
    monkeypatch.setattr(runner, "launch_worker", run)
    assert runner.compare(path, runner.digest(path), tmp_path / "out") == 1
    summary = json.loads((tmp_path / "out/comparison.json").read_text())
    assert [x["variant"] for x in calls] == ["baseline", "candidate"]
    assert summary["status"] == "REPLAY_ERROR"
    assert summary["accuracy_evaluated"] is False
    assert summary["promotion_allowed"] is False


@pytest.mark.parametrize("variant", ["baseline", "candidate"])
def test_real_pinned_runtime_installs_in_separate_offline_process(variant):
    root = os.environ.get("RECOGNIZER_" + variant.upper() + "_ROOT")
    if not root:
        pytest.skip("pinned comparison checkout not supplied")
    sha = runner.git(root, "rev-parse", "HEAD")
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(RUNNER), "inspect", "--root", root,
         "--sha", sha, "--variant", variant],
        text=True, capture_output=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout.strip().splitlines()[-1])
    assert receipt["pid"] != os.getpid()
    assert receipt["sha"] == sha
    assert receipt["media_processed"] is False
    assert receipt["module"] == runner.MODULES[variant]


def test_worker_audit_blocks_real_python_socket_and_direct_spawn_routes():
    script = """
import importlib.util, json, os, socket, subprocess, sys
spec = importlib.util.spec_from_file_location("runner_under_test", sys.argv[1])
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
# Create an unconnected UDP socket before installing the hook to exercise sendto.
udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sys.addaudithook(runner.offline_audit)
operations = {
    "socket": lambda: socket.socket(),
    "udp": lambda: udp.sendto(b"must-not-send", ("127.0.0.1", 9)),
    "popen": lambda: subprocess.run(["/nonexistent/blocked-command"]),
    "exec": lambda: os.execv("/nonexistent/blocked-command", ["blocked"]),
}
if hasattr(os, "posix_spawn"):
    operations["spawn"] = lambda: os.posix_spawn("/nonexistent/blocked-command", ["blocked"], {})
blocked = []
for name, operation in operations.items():
    try:
        operation()
    except runner.OfflineIOError:
        blocked.append(name)
    else:
        raise AssertionError("operation was not blocked: " + name)
udp.close()
print(json.dumps(blocked))
"""
    result = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(RUNNER)],
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    blocked = json.loads(result.stdout)
    assert set(blocked) >= {"socket", "udp", "popen", "exec"}
    if hasattr(os, "posix_spawn"):
        assert "spawn" in blocked


@pytest.mark.parametrize("variant,revision", [
    ("baseline", "3.1-free-r26.3"),
    ("candidate", "3.1-free-r26.3-observation-guards-candidate2"),
    ("candidate", "3.1-free-r26.3-auction-candidate3"),
])
def test_loader_sets_exact_allowed_revision_before_install(tmp_path, monkeypatch, variant, revision):
    root = tmp_path / variant
    module = SimpleNamespace(__file__=str(root / "bridge_vision" / "video.py"))
    installed = []
    def install(token):
        installed.append(os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"])
        with pytest.raises(runner.EvidenceError, match="must never request credentials"):
            token()
    runtime = SimpleNamespace(REVISION=revision, install=install)
    runtime_name = ("bridge_runtime_hardening_r26" if variant == "baseline"
                    else "bridge_runtime_hardening_r26_candidate")
    modules = {runtime_name: runtime, runner.MODULES[variant]: module}
    monkeypatch.setattr(runner.importlib, "import_module", modules.__getitem__)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setenv("BRIDGE_REQUESTED_ALGORITHM_REVISION", "unrelated")
    assert runner.load_runtime(root, variant) is module
    assert installed == [revision]
    assert os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] == revision


@pytest.mark.parametrize("variant,revision", [
    ("baseline", "3.1-free-r26.3-auction-candidate3"),
    ("candidate", "3.1-free-r26.3"),
    ("candidate", "3.1-free-r26.3-auction-candidate4"),
])
def test_loader_rejects_cross_variant_and_unknown_revisions_before_install(
        tmp_path, monkeypatch, variant, revision):
    installed = []
    runtime = SimpleNamespace(REVISION=revision, install=lambda _: installed.append(True))
    monkeypatch.setattr(runner.importlib, "import_module", lambda _: runtime)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setenv("BRIDGE_REQUESTED_ALGORITHM_REVISION", "unrelated")
    with pytest.raises(ValueError, match="unexpected runtime revision"):
        runner.load_runtime(tmp_path, variant)
    assert not installed


@pytest.mark.parametrize("reported", [False, True])
def test_compare_candidate3_embedded_profile_keeps_truthful_scope_and_raw_result(
        sealed_case, tmp_path, monkeypatch, reported):
    """Runner wiring only: fake recognition/launch, real loader/worker/recorder."""
    path, manifest = sealed_case
    embedded = {"schema": "synthetic-runner-wiring-only", "marker": "embedded-auction"}
    profile = Path(manifest["inputs"]["profile"]["path"])
    profile.write_text(json.dumps({"auction": embedded}), encoding="utf-8")
    manifest["inputs"]["profile"]["sha256"] = runner.digest(profile)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    modules, installations, launches, results = {}, [], [], {}
    for variant in ("baseline", "candidate"):
        module = fake_module()
        module.__file__ = str(Path(manifest[variant]["root"]) / "bridge_vision" / "video.py")
        module.PRIMARY_VIDEO_VERSION = "synthetic-" + variant
        revision = ("3.1-free-r26.3" if variant == "baseline"
                    else "3.1-free-r26.3-auction-candidate3")
        def recognize(video, *, profile_path, variant=variant, **kwargs):
            assert json.loads(profile_path.read_text(encoding="utf-8"))["auction"] == embedded
            assert runner.digest(profile_path) == manifest["inputs"]["profile"]["sha256"]
            result = {"status": "NO_FULL_LAYOUT_ACCEPTED", "deals": []}
            if variant == "candidate":
                result["auction_recognition"] = (
                    {"status": "OBSERVED", "auctions": [{"fixture_only": True}]}
                    if reported else {"status": "UNAVAILABLE", "reason": "SYNTHETIC_ABSTENTION",
                                      "auctions": []}
                )
            results[variant] = result
            return result
        module.recognize_video_primary = recognize
        def install(token, variant=variant, revision=revision):
            assert os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] == revision
            with pytest.raises(runner.EvidenceError):
                token()
            installations.append((variant, revision))
        name = ("bridge_runtime_hardening_r26" if variant == "baseline"
                else "bridge_runtime_hardening_r26_candidate")
        modules[name] = SimpleNamespace(REVISION=revision, install=install)
        modules[runner.MODULES[variant]] = module
    monkeypatch.setattr(runner.importlib, "import_module", modules.__getitem__)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setenv("BRIDGE_REQUESTED_ALGORITHM_REVISION", "unrelated")
    monkeypatch.setattr(runner, "video_metadata", lambda *_: {"synthetic": True})
    # Avoid a permanent audit hook in pytest. Real hooks are exercised separately.
    hooks = []
    monkeypatch.setattr(sys, "addaudithook", hooks.append)
    def launch(command, **kwargs):
        assert command[1:3] == ["-I", "-B"]
        config_path = Path(command[-1])
        config = json.loads(config_path.read_text())
        launches.append(config)
        assert "gold" not in config["inputs"]
        return SimpleNamespace(returncode=runner.worker(config_path))
    monkeypatch.setattr(runner, "launch_worker", launch)
    output = tmp_path / "out"
    assert runner.compare(path, runner.digest(path), output) == 0
    seal = json.loads((output / "seal.json").read_text())
    summary = json.loads((output / "comparison.json").read_text())
    assert summary["status"] == "CAPTURED_UNSCORED"
    expected_scope = "PRIMARY_VISUAL_WITH_OPTIONAL_EMBEDDED_PROFILE_AUCTION; NO_ASR_DDS_OR_PUBLISHER"
    for record in (seal, summary, *summary["runs"].values()):
        assert record["scope"] == expected_scope
        assert record["runner_version"] == "recognizer-comparison-v1-auction-scope-r3"
        assert "NO_ASR_AUCTION" not in record["scope"]
    assert summary["runs"]["baseline"]["auction_result_status"] == "NOT_REPORTED"
    assert summary["runs"]["candidate"]["auction_result_status"] == (
        "OBSERVED" if reported else "UNAVAILABLE")
    assert installations == [
        ("baseline", "3.1-free-r26.3"),
        ("candidate", "3.1-free-r26.3-auction-candidate3"),
    ]
    assert [config["variant"] for config in launches] == ["baseline", "candidate"]
    assert hooks == [runner.offline_audit, runner.offline_audit]
    for config in launches:
        result = json.loads((Path(config["output"]) / "result.json").read_text())
        assert result == results[config["variant"]]
    assert summary["accuracy_evaluated"] is False
    assert summary["promotion_allowed"] is False


def test_worker_launch_hook_does_not_intercept_git_probes(tmp_path, monkeypatch):
    """Exercise check_output -> shared subprocess.run with a worker hook installed."""
    original_run = subprocess.run
    def worker_only(*args, **kwargs):
        pytest.fail("Git probe reached the worker launch hook")
    monkeypatch.setattr(runner, "launch_worker", worker_only)
    assert subprocess.run is original_run
    assert runner.subprocess.run is original_run
    assert runner.git(tmp_path, "--version").startswith("git version ")
