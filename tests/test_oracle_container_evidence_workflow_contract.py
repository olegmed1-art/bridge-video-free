import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/oracle-universal-video-container-evidence.yml"


def test_legacy_evidence_workflow_is_pr_only_and_static() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "pull_request:" in text
    assert "workflow_dispatch:" not in text
    assert "push:" not in text
    assert text.startswith(
        "name: Retired Oracle Universal Video Container Evidence Contract\n"
    )
    assert "name: Oracle Universal Video Container Evidence\n" not in text
    assert "  retired-evidence-contract:" in text
    assert "name: Retired legacy evidence entrypoint contract" in text
    assert "head.repo.full_name" not in text
    assert "UNIVERSAL_VIDEO_LEGACY_CONTAINER_EVIDENCE_RETIRED=true" in text
    assert ".github/workflows/issue-881-authoritative-external-evidence.yml" in text


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


def test_legacy_evidence_workflow_has_no_external_mutation_capability() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    for forbidden in (
        "secrets.",
        "oci ",
        "ssh ",
        "systemctl ",
        "docker ",
        "UNIVERSAL_VIDEO_ACTIVATE",
        "UNIVERSAL_VIDEO_SOURCE_ONLY",
        "oracle-instance-workload-mutation",
    ):
        assert forbidden not in text


def test_container_installer_reclaims_only_unused_video_images_before_build() -> None:
    installer = (ROOT / "ops/oracle_universal_video_container_install.sh").read_text(encoding="utf-8")

    assert 'UNIVERSAL_VIDEO_CONTAINER_MIN_FREE_KB:-8388608' in installer
    assert 'docker builder prune --all --force' in installer
    assert 'docker image prune --all --force' not in installer
    assert 'docker image ls --no-trunc --filter "reference=$IMAGE_REPO:*"' in installer
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
    promotion = (ROOT / ".github/workflows/oracle-universal-video-container-promote.yml").read_text(encoding="utf-8")

    assert 'SOURCE_ONLY="${UNIVERSAL_VIDEO_SOURCE_ONLY:-0}"' in run_command
    assert '[[ "$SOURCE_ONLY" =~ ^[01]$ ]]' in run_command
    assert 'if [[ "$SOURCE_ONLY" == "1" ]]; then' in run_command
    assert "UNIVERSAL_VIDEO_SOURCE_ONLY_PREPARE_PASS" in run_command
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


def test_status_mount_is_refreshed_after_long_image_build_before_readiness() -> None:
    installer = (ROOT / "ops/oracle_universal_video_container_install.sh").read_text(encoding="utf-8")

    build = installer.index('docker build --pull')
    refresh = installer.index("Refresh ephemeral host status mount immediately before readiness")
    readiness = installer.index("docker run --rm", refresh)
    assert build < refresh < readiness
    assert installer.count('install -d -o "$USER_NAME" -g "$GROUP_NAME" -m 0750 "$STATUS_DIR"') >= 2
    assert '[[ ! -L "$STATUS_DIR" ]]' in installer[refresh:readiness]


def test_container_image_build_has_its_own_graceful_timeout() -> None:
    installer = (ROOT / "ops/oracle_universal_video_container_install.sh").read_text(encoding="utf-8")

    assert "UNIVERSAL_VIDEO_CONTAINER_BUILD_TIMEOUT_SECONDS:-1200" in installer
    assert 'timeout --foreground --signal=TERM --kill-after=30s "$BUILD_TIMEOUT_SECONDS" docker build' in installer
    assert "build_rc == 124 || build_rc == 137" in installer
    assert "UV_CONTAINER_IMAGE_BUILD_TIMEOUT" in installer


def test_legacy_evidence_workflow_cannot_restart_from_commit_messages() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "UV_EVIDENCE_RECOVER_STUCK" not in text
    assert "github.event.head_commit" not in text
    assert "cancel-in-progress: true" not in text


def test_container_activation_restarts_already_active_service_for_exact_image() -> None:
    installer = (ROOT / "ops/oracle_universal_video_container_install.sh").read_text(encoding="utf-8")

    assert 'systemctl enable "$SERVICE_NAME"' in installer
    assert 'systemctl restart "$SERVICE_NAME"' in installer
    assert 'systemctl enable --now "$SERVICE_NAME"' not in installer
