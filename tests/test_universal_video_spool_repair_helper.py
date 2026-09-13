from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "ops/universal_video_spool_repair.sh"
INSTALLER = ROOT / "ops/install_universal_video_ocarun_admin.sh"


def test_spool_repair_helper_is_fixed_and_argument_free():
    text = HELPER.read_text(encoding="utf-8")
    assert "[[ $(id -u) -eq 0 ]]" in text
    assert "inbox running done failed results progress" in text
    assert "/usr/sbin/runuser -u universal-video" in text
    assert "UNIVERSAL_VIDEO_SPOOL_RUNTIME_REPAIR_PASS" in text
    assert "$1" not in text
    assert "eval " not in text


def test_bootstrap_grants_only_exact_helper_not_shell():
    text = INSTALLER.read_text(encoding="utf-8")
    assert "NOPASSWD: /usr/local/sbin/universal-video-spool-repair" in text
    assert "NOPASSWD: /bin/sh" not in text
    assert "NOPASSWD:ALL" in text  # documented/prohibited pattern
    assert "grep -Eq 'NOPASSWD:[[:space:]]*ALL'" in text
    assert "install -o root -g root -m 0755 \"$tmp/repair\" \"$REPAIR_TARGET\"" in text
