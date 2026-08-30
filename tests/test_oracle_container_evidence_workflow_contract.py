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


def test_activation_does_not_run_build_disk_cleanup_or_delete_attested_image() -> None:
    installer = (ROOT / "ops/oracle_universal_video_container_install.sh").read_text(encoding="utf-8")

    build_guard = 'if [[ "$BUILD_IMAGE" == 1 ]]; then'
    disk_probe = 'disk_available_kb="$(df -Pk "$BASE_DIR"'
    image_inspect = 'docker image inspect "$image" >/dev/null'
    first_guard = installer.index(build_guard)
    first_disk_probe = installer.index(disk_probe)
    build_branch = installer.rindex(build_guard)
    activation_inspect = installer.index(image_inspect)
    assert first_guard < first_disk_probe < build_branch < activation_inspect
    assert installer.count('docker builder prune --all --force') == 1
    assert 'docker image prune --all' not in installer


def test_evidence_bounds_source_prepare_failure_before_container_build() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "prepare_rc=$?" in text
    assert 'bounded_container_log_diagnostic.py "$RUNNER_TEMP/prepare.log"' in text
    assert "UV_CONTAINER_SOURCE_PREPARE_FAILED" in text
    assert "disk_available_kb=" in text
    assert 'cat "$RUNNER_TEMP/prepare.log"' not in text


def test_source_prepare_emits_only_fixed_bounded_stages() -> None:
    run_command = (ROOT / "ops/oracle_universal_video_run_command.sh").read_text(encoding="utf-8")
    parser_path = ROOT / "ops/bounded_container_log_diagnostic.py"
    spec = importlib.util.spec_from_file_location("bounded_prepare_stages", parser_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    for stage in (
        "protected-preflight",
        "service-quiesce",
        "source-checkout",
        "legacy-install",
        "operator-install",
        "protected-postflight",
        "complete",
    ):
        assert f"UNIVERSAL_VIDEO_PREPARE_STAGE stage={stage}" in run_command
    assert module.bounded_diagnostics(
        [
            "UNIVERSAL_VIDEO_PREPARE_STAGE stage=source-checkout",
            "UNIVERSAL_VIDEO_PREPARE_STAGE stage=private-secret",
        ]
    ) == ["UNIVERSAL_VIDEO_PREPARE_STAGE stage=source-checkout"]


def test_container_gates_prepare_source_without_reinstalling_legacy_runtime() -> None:
    run_command = (ROOT / "ops/oracle_universal_video_run_command.sh").read_text(encoding="utf-8")
    evidence = WORKFLOW.read_text(encoding="utf-8")
    promotion = (ROOT / ".github/workflows/oracle-universal-video-container-promote.yml").read_text(encoding="utf-8")

    assert 'SOURCE_ONLY="${UNIVERSAL_VIDEO_SOURCE_ONLY:-0}"' in run_command
    assert '[[ "$SOURCE_ONLY" =~ ^[01]$ ]]' in run_command
    assert 'if [[ "$SOURCE_ONLY" == "1" ]]; then' in run_command
    assert "UNIVERSAL_VIDEO_SOURCE_ONLY_PREPARE_PASS" in run_command
    assert "UNIVERSAL_VIDEO_SOURCE_ONLY=1" in evidence
    assert "UNIVERSAL_VIDEO_SOURCE_ONLY=1" in promotion


def test_bounded_parser_maps_docker_errors_without_leaking_raw_daemon_text() -> None:
    path = ROOT / "ops/bounded_container_log_diagnostic.py"
    spec = importlib.util.spec_from_file_location("bounded_docker_readiness", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    result = module.bounded_diagnostics(
        [
            "docker: Error response from daemon: invalid mount config at /private/token",
            "docker: Error response from daemon: opaque unexpected detail secret=hidden",
        ]
    )
    assert result == [
        '{"error_code":"UV_CONTAINER_DOCKER_MOUNT_FAILED","status":"FAILED"}',
        '{"error_code":"UV_CONTAINER_DOCKER_RUN_FAILED","status":"FAILED"}',
    ]
    assert all("private" not in line and "secret" not in line for line in result)


def test_docker_mount_diagnostic_prioritizes_permission_and_fixed_area() -> None:
    path = ROOT / "ops/bounded_container_log_diagnostic.py"
    spec = importlib.util.spec_from_file_location("bounded_mount_area", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    result = module.bounded_diagnostics(
        [
            "docker: Error response from daemon: mount /run/bridge-school: operation not permitted secret=x",
            "docker: Error response from daemon: invalid mount /opt/bridge-school/universal-video/model-cache private=x",
        ]
    )
    assert result == [
        '{"error_code":"UV_CONTAINER_DOCKER_PERMISSION_DENIED","status":"FAILED"}',
        '{"error_code":"UV_CONTAINER_DOCKER_MOUNT_MODEL_CACHE_FAILED","status":"FAILED"}',
    ]
