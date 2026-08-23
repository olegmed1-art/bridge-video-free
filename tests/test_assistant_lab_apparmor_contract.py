from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "deploy/oracle-assistant-lab/assistant-lab-observer.service"
PROFILE = ROOT / "deploy/oracle-assistant-lab/bwrap.apparmor"
INSTALLER = ROOT / "ops/oracle_assistant_lab_observer_install.sh"


def test_observer_enters_bwrap_profile_without_disabling_no_new_privileges():
    text = UNIT.read_text(encoding="utf-8")
    assert "AppArmorProfile=bwrap" in text
    assert "NoNewPrivileges=true" in text
    assert "NoNewPrivileges=false" not in text
    assert "ProtectSystem=strict" in text
    assert "ProtectKernelTunables=true" not in text
    assert "ProtectKernelModules=true" in text


def test_bwrap_profile_grants_only_user_namespace_extension():
    text = PROFILE.read_text(encoding="utf-8")
    assert "profile bwrap /usr/bin/bwrap flags=(unconfined)" in text
    assert "userns," in text
    assert "network," not in text
    assert "capability," not in text


def test_installer_loads_profile_before_systemd_activation():
    text = INSTALLER.read_text(encoding="utf-8")
    profile_install = text.index('install -m 0644 -o root -g root "$APPARMOR_PROFILE_SRC"')
    profile_load = text.index('apparmor_parser -r "$APPARMOR_PROFILE_DST"')
    daemon_reload = text.index("systemctl daemon-reload")
    service_restart = text.index('systemctl restart "$SERVICE_NAME" "$CONTROL_SERVICE_NAME"')
    assert profile_install < profile_load < daemon_reload < service_restart
