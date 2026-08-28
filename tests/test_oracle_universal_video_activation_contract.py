from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/oracle-universal-video-activation.yml"
SCHEMA = ROOT / "ops/oracle-universal-video-request.schema.json"
OPERATOR_INSTALL = ROOT / "ops/install_universal_video_operator.sh"


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
    assert "DDS3_AFTER_PASS" in text
    assert "workflow_dispatch" in text
    assert "ops/oracle-universal-video-requests/*.json" in text
    assert "run: ${{" not in text


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
    assert "ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video submit-base64 *" in installer
    assert "ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video status *" in installer
