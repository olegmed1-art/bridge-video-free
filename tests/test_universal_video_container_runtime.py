from __future__ import annotations

import ast
from pathlib import Path

import pytest

from universal_video.container_runtime import ContainerRuntimeUnavailable, validate_container_runtime


def _environment(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setenv("UNIVERSAL_VIDEO_SOURCE_COMMIT", "a" * 40)
    for name in ("UNIVERSAL_VIDEO_SPOOL_ROOT", "UNIVERSAL_VIDEO_OUTPUT_ROOT", "UNIVERSAL_VIDEO_MEDIA_ROOT", "HF_HOME"):
        directory = root / name.lower()
        directory.mkdir()
        if name == "UNIVERSAL_VIDEO_SPOOL_ROOT":
            for leaf in ("inbox", "running", "done", "failed", "results", "progress"):
                (directory / leaf).mkdir()
        monkeypatch.setenv(name, str(directory))


def test_container_rejects_missing_provenance(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _environment(monkeypatch, tmp_path)
    monkeypatch.delenv("UNIVERSAL_VIDEO_SOURCE_COMMIT")
    with pytest.raises(ContainerRuntimeUnavailable) as error:
        validate_container_runtime()
    assert error.value.error_code == "UV_CONTAINER_PROVENANCE_INVALID"


def test_container_rejects_unwritable_mount_before_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _environment(monkeypatch, tmp_path)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "missing"))
    with pytest.raises(ContainerRuntimeUnavailable) as error:
        validate_container_runtime()
    assert error.value.error_code == "UV_CONTAINER_MOUNT_UNAVAILABLE"


def test_container_runtime_reports_ready_without_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _environment(monkeypatch, tmp_path)
    monkeypatch.setattr("universal_video.container_runtime.validate_video_runtime", lambda: {"ffmpeg": "/usr/bin/ffmpeg", "ffprobe": "/usr/bin/ffprobe", "asr": "faster_whisper"})
    monkeypatch.setattr("universal_video.container_runtime._load_model", lambda: object())
    report = validate_container_runtime()
    assert report["status"] == "READY"
    assert report["fallback_used"] is False


def test_container_accepts_protected_spool_root_with_writable_leaves(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _environment(monkeypatch, tmp_path)
    spool = tmp_path / "universal_video_spool_root"
    from universal_video import container_runtime

    real_probe = container_runtime._write_probe

    def protected_root_probe(path: Path) -> None:
        if path == spool:
            raise PermissionError("protected spool root")
        real_probe(path)

    monkeypatch.setattr("universal_video.container_runtime._write_probe", protected_root_probe)
    monkeypatch.setattr(
        "universal_video.container_runtime.validate_video_runtime",
        lambda: {"ffmpeg": "/usr/bin/ffmpeg", "ffprobe": "/usr/bin/ffprobe", "asr": "faster_whisper"},
    )
    monkeypatch.setattr("universal_video.container_runtime._load_model", lambda: object())

    report = validate_container_runtime()

    assert report["status"] == "READY"


def test_container_image_keeps_credentials_and_media_out_of_layers() -> None:
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "deploy/oracle-universal-video/Dockerfile").read_text(encoding="utf-8")
    assert "HF_HUB_OFFLINE=1" in dockerfile
    assert "GOOGLE_DRIVE_OAUTH" not in dockerfile
    assert "COPY universal_video" in dockerfile
    assert "USER universal-video:universal-video" in dockerfile


def test_container_image_contains_neon_processor_dependency_closure() -> None:
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "deploy/oracle-universal-video/Dockerfile").read_text(encoding="utf-8")
    assert "requirements-worker.txt" in dockerfile
    local_modules: dict[str, Path] = {}
    for path in root.rglob("*.py"):
        if any(part in {".git", "tests", "__pycache__"} for part in path.parts):
            continue
        parts = list(path.relative_to(root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        if parts:
            local_modules[".".join(parts)] = path

    pending = ["bridge_worker_3_1_free", "bridge_runtime_hardening_r25_16", "route_drive_job_outputs"]
    required: set[str] = set()

    def enqueue(module: str) -> None:
        parts = module.split(".")
        for length in range(1, len(parts) + 1):
            candidate = ".".join(parts[:length])
            if candidate in local_modules and candidate not in required:
                pending.append(candidate)

    while pending:
        module = pending.pop()
        if module in required:
            continue
        required.add(module)
        path = local_modules[module]
        tree = ast.parse(path.read_text(encoding="utf-8"))
        package = module if path.name == "__init__.py" else module.rpartition(".")[0]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    enqueue(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    package_parts = package.split(".") if package else []
                    base_parts = package_parts[: max(0, len(package_parts) - (node.level - 1))]
                    if node.module:
                        base_parts.extend(node.module.split("."))
                    base = ".".join(base_parts)
                else:
                    base = node.module or ""
                if base:
                    enqueue(base)
                for alias in node.names:
                    if alias.name != "*":
                        enqueue(".".join(part for part in (base, alias.name) if part))

    required_packages = {
        module.split(".", 1)[0]
        for module in required
        if "." in module or local_modules[module].name == "__init__.py"
    }
    required_files = {module for module in required if module not in required_packages and "." not in module}
    missing = sorted(
        [f"{module}.py" for module in required_files if f"{module}.py" not in dockerfile]
        + [package for package in required_packages if f"COPY {package} ./{package}" not in dockerfile]
    )
    assert missing == []


def test_oracle_container_service_is_read_only_and_explicitly_activated() -> None:
    root = Path(__file__).resolve().parents[1]
    service = (root / "deploy/oracle-universal-video/universal-video-container.service").read_text(encoding="utf-8")
    installer = (root / "ops/oracle_universal_video_container_install.sh").read_text(encoding="utf-8")
    assert "--read-only" in service
    assert "--memory=8g" in service
    assert "UNIVERSAL_VIDEO_CONTAINER_ACTIVATE:-0" in installer
    assert "UNIVERSAL_VIDEO_CONTAINER_BUILD:-1" in installer
    assert "docker run --rm" in installer
    assert "UNIVERSAL_VIDEO_STATUS_PATH=/run/bridge-school/universal-video-status.json" in installer
    assert '--mount "type=bind,src=$STATUS_DIR,dst=/run/bridge-school"' in installer
    assert "--mount type=bind,src=/run/bridge-school,dst=/run/bridge-school" in service
    assert "ReadWritePaths=/run/bridge-school" in service
    assert "UV_CONTAINER_SERVICE_ACTIVATION_FAILED" in installer
    assert "UV_CONTAINER_SERVICE_INACTIVE" in installer
    assert "-p Result -p ExecMainCode -p ExecMainStatus -p NRestarts" in installer
    assert "service_exec_status ExecStartPre ExecStartPre" in installer
    assert "service_exec_status ExecStart ExecStart" in installer
    assert 'runtime_fail(){ printf \'{"error_code":"%s","status":"FAILED"}' in installer
