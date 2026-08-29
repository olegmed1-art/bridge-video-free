from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/oracle-universal-video-activation.yml"
SCHEMA = ROOT / "ops/oracle-universal-video-request.schema.json"
OPERATOR_INSTALL = ROOT / "ops/install_universal_video_operator.sh"
RUN_COMMAND = ROOT / "ops/oracle_universal_video_run_command.sh"


def test_activation_workflow_is_fixed_scope_and_fail_closed():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "158.180.47.161" in text
    assert "ORACLE_USER: ubuntu" in text
    assert "StrictHostKeyChecking=yes" in text
    assert "StrictHostKeyChecking=no" not in text
    assert "sudo -n env UNIVERSAL_VIDEO_GIT_REF=" in text
    assert "ops/oracle_universal_video_run_command.sh" in text
    assert "ops/oracle_universal_video_spool_guard.sh" in text
    assert "ops/universal_video_receipt_reader.py" in text
    assert "UNIVERSAL_VIDEO_ORACLE_RUN_COMMAND_PASS" in text
    assert "UNIVERSAL_VIDEO_SYNTHETIC_SMOKE_PASS" in text
    assert "assistant_lab=active" in text
    assert "universal_video_enabled=enabled" in text
    assert "universal_video_active=active" in text
    assert "universal_video_admin=installed_revision_bound" in text
    assert "UNIVERSAL_VIDEO_OCARUN_BOUNDED_ADMIN_BOOTSTRAP_PASS" in text
    assert "UNIVERSAL_VIDEO_OCARUN_POST_BOOTSTRAP_AUDIT_PASS" in text
    assert "DDS3_AFTER_PASS" in text
    assert "workflow_dispatch" in text
    assert "ops/oracle-universal-video-requests/*.json" in text
    assert "run: ${{" not in text
    command = RUN_COMMAND.read_text(encoding="utf-8")
    assert "'kind':'oracle_drive_staged'" in command
    assert "'kind':'local_path'" not in command


def test_request_schema_rejects_arbitrary_host_command_and_unknown_fields():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert schema["properties"]["host"]["const"] == "158.180.47.161"
    assert schema["properties"]["user"]["const"] == "ubuntu"
    assert schema["properties"]["issue"]["const"] == 318
    assert schema["properties"]["mode"]["enum"] == ["probe", "activate", "smoke"]
    assert "command" not in schema["properties"]


def test_generic_operator_owns_a_dedicated_sudoers_file():
    installer = OPERATOR_INSTALL.read_text(encoding="utf-8")
    assert "readonly SUDOERS='/etc/sudoers.d/universal-video-operator-ocarun'" in installer
    assert "readonly SUDOERS='/etc/sudoers.d/universal-video-ocarun'" not in installer
    assert "ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video submit-drive-base64 *" in installer
    assert "ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video status *" in installer
    assert "/usr/local/sbin/universal-video-diana11" in installer
    for name in (
        "install_universal_video_diana11_operator.sh",
        "install_universal_video_diana11_002_operator.sh",
        "install_universal_video_diana11_003_operator.sh",
    ):
        retired = (ROOT / "ops" / name).read_text(encoding="utf-8")
        assert "RETIRED: use /usr/local/sbin/universal-video submit-drive-base64" in retired
        assert "exit 78" in retired


def test_activation_installs_export_boundary_from_the_exact_resolved_revision():
    command = RUN_COMMAND.read_text(encoding="utf-8")
    assert 'SOURCE_COMMIT="$RESOLVED_COMMIT"' in command
    assert 'bash "$SOURCE_DIR/ops/install_universal_video_ocarun_admin.sh"' in command
    assert "universal_video_admin=installed_revision_bound" in command
    assert command.index('SOURCE_COMMIT="$RESOLVED_COMMIT"') < command.index(
        "universal_video_admin=installed_revision_bound"
    )
    assert "UNIVERSAL_VIDEO_RUN_SMOKE=1" not in command
