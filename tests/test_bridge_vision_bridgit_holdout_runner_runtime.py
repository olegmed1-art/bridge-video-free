import base64
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import bridge_vision.bridgit_holdout_runner as runner


class _RecordEntry:
    def __init__(self, relative: str, payload: bytes):
        self.relative = relative
        digest = hashlib.sha256(payload).digest()
        value = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        self.hash = SimpleNamespace(mode="sha256", value=value)

    def __str__(self) -> str:
        return self.relative


class _Distribution:
    def __init__(self, root, *entries):
        self.root = root
        self.files = list(entries)

    def locate_file(self, entry):
        return self.root / str(entry)


def _probe(distributions, modules):
    return {
        distribution_name: {
            "entry_module": modules[module_name].__file__,
            "loaded_native_files": [
                str(
                    distributions[distribution_name].locate_file(
                        next(
                            entry
                            for entry in distributions[distribution_name].files
                            if runner._is_native_runtime_path(str(entry))
                        )
                    ).resolve()
                )
            ],
        }
        for distribution_name, (module_name, _) in runner.PINNED_RUNTIME_MODULES.items()
    }


def test_imported_pixel_modules_are_bound_to_distribution_record(tmp_path, monkeypatch):
    roots = {}
    modules = {}
    distributions = {}
    for distribution_name, (module_name, relative) in runner.PINNED_RUNTIME_MODULES.items():
        payload = f"{distribution_name}-baseline".encode()
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        entry = _RecordEntry(relative, payload)
        native_relative = (
            "numpy/_core/_multiarray_umath.test.so"
            if distribution_name == "numpy"
            else "cv2/cv2.test.so"
        )
        native_payload = f"{distribution_name}-native-baseline".encode()
        native_path = tmp_path / native_relative
        native_path.parent.mkdir(parents=True, exist_ok=True)
        native_path.write_bytes(native_payload)
        native_entry = _RecordEntry(native_relative, native_payload)
        roots[distribution_name] = path
        modules[module_name] = SimpleNamespace(__file__=str(path))
        distributions[distribution_name] = _Distribution(
            tmp_path, entry, native_entry
        )

    monkeypatch.setattr(
        runner.metadata, "distribution", lambda name: distributions[name]
    )
    monkeypatch.setattr(
        runner, "_isolated_runtime_probe", lambda: _probe(distributions, modules)
    )

    assert runner._verify_imported_runtime_modules() == {
        name: {
            "entry_module": relative,
            "native_files": [
                {
                    "path": (
                        "numpy/_core/_multiarray_umath.test.so"
                        if name == "numpy"
                        else "cv2/cv2.test.so"
                    ),
                    "sha256": hashlib.sha256(
                        f"{name}-native-baseline".encode()
                    ).hexdigest(),
                }
            ],
            "loaded_native_files": [
                "numpy/_core/_multiarray_umath.test.so"
                if name == "numpy"
                else "cv2/cv2.test.so"
            ],
        }
        for name, (_, relative) in runner.PINNED_RUNTIME_MODULES.items()
    }

    roots["opencv-python-headless"].write_bytes(b"shadowed-cv2-module")
    with pytest.raises(runner.HoldoutRunnerError, match="does not match RECORD"):
        runner._verify_imported_runtime_modules()


def test_native_runtime_files_are_bound_to_distribution_record(tmp_path, monkeypatch):
    modules = {}
    distributions = {}
    native_paths = {}
    for distribution_name, (module_name, relative) in runner.PINNED_RUNTIME_MODULES.items():
        wrapper_payload = f"{distribution_name}-wrapper".encode()
        wrapper_path = tmp_path / relative
        wrapper_path.parent.mkdir(parents=True, exist_ok=True)
        wrapper_path.write_bytes(wrapper_payload)
        wrapper_entry = _RecordEntry(relative, wrapper_payload)

        native_relative = f"{module_name}/{module_name}.abi3.so"
        native_payload = f"{distribution_name}-native".encode()
        native_path = tmp_path / native_relative
        native_path.parent.mkdir(parents=True, exist_ok=True)
        native_path.write_bytes(native_payload)
        native_entry = _RecordEntry(native_relative, native_payload)

        modules[module_name] = SimpleNamespace(__file__=str(wrapper_path))
        distributions[distribution_name] = _Distribution(
            tmp_path, wrapper_entry, native_entry
        )
        native_paths[distribution_name] = native_path

    monkeypatch.setattr(
        runner.metadata, "distribution", lambda name: distributions[name]
    )
    monkeypatch.setattr(
        runner, "_isolated_runtime_probe", lambda: _probe(distributions, modules)
    )

    runner._verify_imported_runtime_modules()
    native_paths["opencv-python-headless"].write_bytes(b"replaced-native-module")
    with pytest.raises(runner.HoldoutRunnerError, match="native file does not match RECORD"):
        runner._verify_imported_runtime_modules()


def test_frozen_artifact_identity_binds_runtime_module_paths(monkeypatch):
    baseline = runner.frozen_recognizer_artifact_sha256("a" * 64)
    changed = dict(runner.PINNED_RUNTIME_MODULES)
    changed["numpy"] = ("numpy", "shadow/numpy/__init__.py")
    monkeypatch.setattr(runner, "PINNED_RUNTIME_MODULES", changed)
    assert runner.frozen_recognizer_artifact_sha256("a" * 64) != baseline


def test_frozen_artifact_identity_binds_native_record_policy(monkeypatch):
    baseline = runner.frozen_recognizer_artifact_sha256("a" * 64)
    monkeypatch.setattr(
        runner, "RUNTIME_NATIVE_RECORD_POLICY", "entry-modules-only-legacy"
    )
    assert runner.frozen_recognizer_artifact_sha256("a" * 64) != baseline


def test_frozen_artifact_identity_binds_native_manifest():
    assert runner.frozen_recognizer_artifact_sha256(
        "a" * 64
    ) != runner.frozen_recognizer_artifact_sha256("b" * 64)


def test_loaded_native_file_must_belong_to_frozen_distribution(
    tmp_path, monkeypatch
):
    modules = {}
    distributions = {}
    for distribution_name, (module_name, relative) in runner.PINNED_RUNTIME_MODULES.items():
        wrapper_payload = f"{distribution_name}-wrapper".encode()
        wrapper_path = tmp_path / relative
        wrapper_path.parent.mkdir(parents=True, exist_ok=True)
        wrapper_path.write_bytes(wrapper_payload)
        native_relative = f"{module_name}/{module_name}.abi3.so"
        native_path = tmp_path / native_relative
        native_path.parent.mkdir(parents=True, exist_ok=True)
        native_path.write_bytes(b"owned-native")
        modules[module_name] = SimpleNamespace(__file__=str(wrapper_path))
        distributions[distribution_name] = _Distribution(
            tmp_path,
            _RecordEntry(relative, wrapper_payload),
            _RecordEntry(native_relative, b"owned-native"),
        )
    shadow = tmp_path / "shadow-cv2.so"
    shadow.write_bytes(b"shadow")
    probe = _probe(distributions, modules)
    probe["opencv-python-headless"]["loaded_native_files"] = [str(shadow)]
    monkeypatch.setattr(
        runner.metadata, "distribution", lambda name: distributions[name]
    )
    monkeypatch.setattr(runner, "_isolated_runtime_probe", lambda: probe)
    with pytest.raises(runner.HoldoutRunnerError, match="not owned"):
        runner._verify_imported_runtime_modules()


def test_isolated_probe_rejects_loader_injection(monkeypatch):
    monkeypatch.setenv("LD_PRELOAD", "/tmp/shadow.so")
    with pytest.raises(runner.HoldoutRunnerError, match="loader injection"):
        runner._isolated_runtime_probe()


def test_isolated_probe_uses_file_limited_outputs(tmp_path, monkeypatch):
    for key in runner.LOADER_INJECTION_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    expected = {
        name: {
            "entry_module": f"/runtime/{module_name}/__init__.py",
            "loaded_native_files": [f"/runtime/{module_name}/native.so"],
        }
        for name, (module_name, _) in runner.PINNED_RUNTIME_MODULES.items()
    }

    def fake_run(_argv, **kwargs):
        assert "capture_output" not in kwargs
        assert kwargs["stdout"].name.endswith("stdout")
        assert kwargs["stderr"].name.endswith("stderr")
        assert kwargs["preexec_fn"] is not None
        kwargs["stdout"].write(json.dumps(expected).encode())
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner.tempfile, "gettempdir", lambda: str(tmp_path))
    assert runner._isolated_runtime_probe() == expected


def test_case_execution_uses_clean_isolated_process(tmp_path, monkeypatch):
    for key in runner.LOADER_INJECTION_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    receipt = {"result": {"status": "PENDING_TEMPORAL_CONSENSUS"}}

    def fake_run(argv, **kwargs):
        assert argv[:3] == [runner.sys.executable, "-I", "-c"]
        assert kwargs["stdout"] is runner.subprocess.DEVNULL
        assert kwargs["stderr"] is runner.subprocess.DEVNULL
        assert kwargs["preexec_fn"] is not None
        assert not any(
            kwargs["env"].get(key) for key in runner.LOADER_INJECTION_ENV_VARS
        )
        output_path = Path(argv[-1])
        output_path.write_text(json.dumps(receipt), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert runner._execute_case_isolated({"job_type": "test"}) == receipt


def test_cli_keeps_output_parent_pinned_across_symlink_retarget(tmp_path, monkeypatch):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink support required")
    safe = tmp_path / "safe"
    hostile = tmp_path / "hostile"
    safe.mkdir()
    hostile.mkdir()
    link = tmp_path / "output-link"
    link.symlink_to(safe, target_is_directory=True)
    output = link / "receipt.json"
    protected = hostile / "receipt.json"
    protected.write_text("sealed-input", encoding="utf-8")

    monkeypatch.setattr(runner, "_validate_output_target", lambda *_args: None)

    def fake_run(_package):
        link.unlink()
        link.symlink_to(hostile, target_is_directory=True)
        return {"schema": runner.RUNNER_OUTPUT_SCHEMA, "ok": True}

    monkeypatch.setattr(runner, "run_package", fake_run)
    package = tmp_path / "unused-package.json"

    assert runner.main(["--package", str(package), "--output", str(output)]) == 0
    assert protected.read_text(encoding="utf-8") == "sealed-input"
    assert json.loads((safe / "receipt.json").read_text(encoding="utf-8"))["ok"] is True
