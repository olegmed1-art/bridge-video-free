from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops/cloud_shell_activate_universal_video.sh"
RUNBOOK = ROOT / "docs/UNIVERSAL_VIDEO_ORACLE_CLOUD_SHELL_RUNBOOK_RU.md"


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_launcher_has_fixed_identity_key_and_runtime_pins():
    text = _text()
    assert "readonly ORACLE_HOST='158.180.47.161'" in text
    assert "readonly ORACLE_USER='ubuntu'" in text
    assert 'readonly SSH_KEY_PATH="$HOME/.ssh/bridge_school_dds3_oracle"' in text
    assert "SHA256:UGJo5yPdnk/wf8DVrzvXt2xJkE9GJ8+3IIcQ2vA+mkc" in text
    assert "59377de601c1586ae9914a51a340dc72ac2007ce" in text
    assert "bbf4dc5779726fca415f641b90d017a802daaabf" in text
    assert "git hash-object" in text


def test_launcher_accepts_only_four_bounded_modes():
    text = _text()
    assert "probe|status|activate|smoke" in text
    assert '[[ "$#" -eq 1 ]]' in text
    assert "unsupported mode" in text
    assert "--host" not in text
    assert "--user" not in text
    assert "--command" not in text
    assert "eval " not in text


def test_launcher_is_strict_about_host_identity_and_ssh():
    text = _text()
    assert "ssh-keyscan -T 10 -t ed25519" in text
    assert "StrictHostKeyChecking=yes" in text
    assert "StrictHostKeyChecking=no" not in text
    assert "IdentitiesOnly=yes" in text
    assert "UserKnownHostsFile=" in text
    assert "private key must not be accessible by group or others" in text


def test_activation_is_pinned_side_by_side_and_has_acceptance_markers():
    text = _text()
    assert "raw.githubusercontent.com/$REPOSITORY/$RUNTIME_COMMIT/$PAYLOAD_PATH" in text
    assert "ops/oracle_universal_video_run_command.sh" in text
    assert "UNIVERSAL_VIDEO_GIT_REF='$RUNTIME_COMMIT'" in text
    assert "UNIVERSAL_VIDEO_RUN_SMOKE='$smoke'" in text
    assert "UNIVERSAL_VIDEO_ACTIVATE=1" in text
    assert "nice -n 10 bash -s" in text
    assert "UNIVERSAL_VIDEO_ORACLE_RUN_COMMAND_PASS" in text
    assert "UNIVERSAL_VIDEO_SYNTHETIC_SMOKE_PASS" in text
    assert "assistant_lab=active" in text
    assert "universal_video_enabled=enabled" in text
    assert "universal_video_active=active" in text
    assert "DDS3_AFTER_PASS" in text
    assert "ORACLE_DDS3_EXTERNAL_NONREGRESSION_PASS" in text


def test_launcher_does_not_control_protected_services_or_real_video():
    text = _text()
    forbidden = [
        "systemctl stop assistant-lab",
        "systemctl restart assistant-lab",
        "systemctl disable assistant-lab",
        "systemctl stop dds3",
        "systemctl restart dds3",
        "systemctl disable dds3",
        "GOOGLE_DRIVE_OAUTH_JSON",
        "client_secret",
        "refresh_token",
        "diana",
    ]
    for token in forbidden:
        assert token not in text
    assert "No real video is submitted" in text
    assert "3-second synthetic job" in text


def test_runbook_preserves_probe_then_activate_then_status_then_smoke_order():
    text = RUNBOOK.read_text(encoding="utf-8")
    positions = [
        text.index(" probe"),
        text.index(" activate"),
        text.index(" status"),
        text.index(" smoke"),
    ]
    assert positions == sorted(positions)
    assert "реальное видео не запускается" in text.lower()
    assert "~/.ssh/bridge_school_dds3_oracle" in text
    assert "issue #318" in text.lower()
