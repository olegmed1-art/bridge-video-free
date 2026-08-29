from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRY = (ROOT / "ops/universal_video_evidence_export_entrypoint.sh").read_text(encoding="utf-8")
INSTALL = (ROOT / "ops/install_universal_video_ocarun_admin.sh").read_text(encoding="utf-8")


def test_export_entrypoint_is_fixed_root_owned_and_argument_free():
    assert "[[ $(id -u) -eq 0 ]]" in ENTRY
    assert "[[ $# -eq 0 ]]" in ENTRY
    assert "usage: universal-video-evidence-export" in ENTRY
    assert "EXPECTED_SOURCE_COMMIT='edbb4cae625323146fcab3ad4f80ed3d9a9abc90'" in ENTRY
    assert "MAX_REQUEST_BYTES=4096" in ENTRY
    assert "spool/running" in ENTRY
    assert "running job guard unavailable" in ENTRY
    assert 'mv -f "$request_tmp" "$REQUEST_PATH"' in ENTRY
    assert "runuser -u universal-video" in ENTRY
    assert "universal_video_resident_evidence_export.py" in ENTRY
    assert "eval " not in ENTRY
    assert "bash -c" not in ENTRY
    assert "sh -c" not in ENTRY


def test_installer_grants_only_the_exact_argument_free_export_command():
    exact = 'ocarun ALL=(root) NOPASSWD: /usr/local/sbin/universal-video-evidence-export ""'
    assert exact in INSTALL
    assert "/usr/local/sbin/universal-video-evidence-export *" not in INSTALL
    assert "install -o root -g root -m 0755 \"$tmp/export\" \"$EXPORT_TARGET\"" in INSTALL
    assert "sudo -u ocarun sudo -n \"$EXPORT_TARGET\" unexpected" in INSTALL
