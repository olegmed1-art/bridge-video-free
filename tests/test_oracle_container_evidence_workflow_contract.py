import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/oracle-universal-video-container-evidence.yml"


def test_bounded_diagnostic_accepts_runtime_json_format() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert 'python ops/bounded_container_log_diagnostic.py "$RUNNER_TEMP/container.log"' in text
    assert "python - <<'PY'" not in text


def test_bounded_parser_canonicalizes_runtime_json_without_leaking_raw_log() -> None:
    path = ROOT / "ops/bounded_container_log_diagnostic.py"
    spec = importlib.util.spec_from_file_location("bounded_container_log_diagnostic", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    result = module.bounded_diagnostics(
        [
            "token=must-not-leak",
            '{"status": "FAILED", "error_code": "UV_CONTAINER_MODEL_UNAVAILABLE"}',
            '{"status":"FAILED","error_code":"OTHER_FAILURE"}',
        ]
    )

    assert result == ['{"error_code":"UV_CONTAINER_MODEL_UNAVAILABLE","status":"FAILED"}']


def test_evidence_workflow_remains_non_activating_and_media_free() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "UNIVERSAL_VIDEO_CONTAINER_ACTIVATE=0" in text
    assert "video_job_submitted=false" in text
    assert "workflow_dispatch:" in text
    assert "paths:" in text
    assert "'universal_video/container_runtime.py'" in text
    assert "'ops/oracle_universal_video_container_install.sh'" in text
    assert "'ops/bounded_container_log_diagnostic.py'" in text
    assert "'ops/oracle_universal_video_run_command.sh'" in text
    assert "'ops/oracle_universal_video_container_promote.sh'" in text
    assert "'deploy/oracle-universal-video/Dockerfile'" in text
    assert "'.github/workflows/oracle-universal-video-container-promote.yml'" in text
    assert "UNIVERSAL_VIDEO_PROMOTION_ENTRYPOINT_ATTEST_PASS" in text
    assert "group: oracle-instance-workload-mutation" in text
    assert "'.github/workflows/oracle-universal-video-activation.yml'" in text
    assert "ORACLE_INSTANCE_RUNNING_PASS" in text
    assert "compute instance action --instance-id \"$INSTANCE_ID\" --action START" in text
    assert "'ops/bounded_container_log_diagnostic.py'" in text


def test_container_installer_reclaims_only_unused_video_images_before_build() -> None:
    installer = (ROOT / "ops/oracle_universal_video_container_install.sh").read_text(encoding="utf-8")

    assert 'UNIVERSAL_VIDEO_CONTAINER_MIN_FREE_KB:-8388608' in installer
    assert 'docker builder prune --all --force' in installer
    assert 'docker image prune --all --force' in installer
    assert 'docker image ls --filter "reference=$IMAGE_REPO:*"' in installer
    assert 'docker ps -aq --filter "ancestor=$old_image_id"' in installer
    assert 'UV_CONTAINER_DISK_INSUFFICIENT' in installer
    assert "docker system prune" not in installer


def test_bounded_parser_reports_build_and_disk_failures_without_raw_log() -> None:
    path = ROOT / "ops/bounded_container_log_diagnostic.py"
    spec = importlib.util.spec_from_file_location("bounded_container_log_diagnostic_disk", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    result = module.bounded_diagnostics(
        [
            "https://private.example/token=must-not-leak",
            "ERROR: container image build failed",
            "UNIVERSAL_VIDEO_CONTAINER_RESOURCE disk_available_kb=7340032 disk_required_kb=8388608",
            '{"status":"FAILED","error_code":"UV_CONTAINER_DISK_INSUFFICIENT"}',
        ]
    )

    assert result == [
        "ERROR: container image build failed",
        "UNIVERSAL_VIDEO_CONTAINER_RESOURCE disk_available_kb=7340032 disk_required_kb=8388608",
        '{"error_code":"UV_CONTAINER_DISK_INSUFFICIENT","status":"FAILED"}',
    ]


def test_bounded_storage_inventory_has_fixed_non_secret_areas() -> None:
    installer = (ROOT / "ops/oracle_universal_video_container_install.sh").read_text(encoding="utf-8")
    path = ROOT / "ops/bounded_container_log_diagnostic.py"
    spec = importlib.util.spec_from_file_location("bounded_container_storage_inventory", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert "for storage_area in spool output media model-cache" in installer
    assert module.bounded_diagnostics(
        [
            "UNIVERSAL_VIDEO_CONTAINER_STORAGE area=media used_kb=1048576",
            "UNIVERSAL_VIDEO_CONTAINER_STORAGE area=var-log used_kb=2048",
            "UNIVERSAL_VIDEO_CONTAINER_STORAGE area=root-cache used_kb=4096",
            "UNIVERSAL_VIDEO_CONTAINER_STORAGE area=uv-cache used_kb=3072",
            "UNIVERSAL_VIDEO_CONTAINER_STORAGE area=private-path used_kb=1",
        ]
    ) == [
        "UNIVERSAL_VIDEO_CONTAINER_STORAGE area=media used_kb=1048576",
        "UNIVERSAL_VIDEO_CONTAINER_STORAGE area=var-log used_kb=2048",
        "UNIVERSAL_VIDEO_CONTAINER_STORAGE area=root-cache used_kb=4096",
        "UNIVERSAL_VIDEO_CONTAINER_STORAGE area=uv-cache used_kb=3072",
    ]


def test_container_cleanup_is_limited_to_stale_regular_root_cache_files() -> None:
    installer = (ROOT / "ops/oracle_universal_video_container_install.sh").read_text(encoding="utf-8")

    assert 'root_cache=/root/.cache' in installer
    assert '[[ -d "$root_cache" && ! -L "$root_cache" ]]' in installer
    assert 'find "$root_cache" -xdev -type f -mtime +14 -delete' in installer
    assert 'find "$root_cache" -xdev -depth -type d -empty -delete' in installer
    assert "rm -rf /root/.cache" not in installer


def test_bounded_parser_reports_stale_cache_cleanup_only() -> None:
    path = ROOT / "ops/bounded_container_log_diagnostic.py"
    spec = importlib.util.spec_from_file_location("bounded_container_cleanup", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.bounded_diagnostics(
        [
            "UNIVERSAL_VIDEO_CONTAINER_CLEANUP area=root-cache age_days=14 files=8 freed_kb=1024",
            "UNIVERSAL_VIDEO_CONTAINER_CLEANUP area=secrets age_days=0 files=1 freed_kb=1",
        ]
    ) == ["UNIVERSAL_VIDEO_CONTAINER_CLEANUP area=root-cache age_days=14 files=8 freed_kb=1024"]
