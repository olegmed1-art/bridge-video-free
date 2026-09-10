import base64
import hashlib
import json
import os
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
    def __init__(self, root, entry):
        self.root = root
        self.files = [entry]

    def locate_file(self, entry):
        return self.root / str(entry)


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
        roots[distribution_name] = path
        modules[module_name] = SimpleNamespace(__file__=str(path))
        distributions[distribution_name] = _Distribution(tmp_path, entry)

    monkeypatch.setattr(
        runner.metadata, "distribution", lambda name: distributions[name]
    )
    monkeypatch.setattr(runner.importlib, "import_module", lambda name: modules[name])

    assert runner._verify_imported_runtime_modules() == {
        name: relative
        for name, (_, relative) in runner.PINNED_RUNTIME_MODULES.items()
    }

    roots["opencv-python-headless"].write_bytes(b"shadowed-cv2-module")
    with pytest.raises(runner.HoldoutRunnerError, match="does not match RECORD"):
        runner._verify_imported_runtime_modules()


def test_frozen_artifact_identity_binds_runtime_module_paths(monkeypatch):
    baseline = runner.frozen_recognizer_artifact_sha256()
    changed = dict(runner.PINNED_RUNTIME_MODULES)
    changed["numpy"] = ("numpy", "shadow/numpy/__init__.py")
    monkeypatch.setattr(runner, "PINNED_RUNTIME_MODULES", changed)
    assert runner.frozen_recognizer_artifact_sha256() != baseline


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
