from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "ops" / "cloud_shell_bootstrap_dds3_mass.sh"


def test_launcher_is_bounded_and_does_not_start_mass():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "probe|install|status" in text
    assert "158.180.47.161" in text
    assert "54cecba85485f8c4cf3dcc91e592db36cdbd2226" in text
    assert "DDS3_MASS_BOOTSTRAP=1 DDS3_MASS_ACTIVATE=1" in text
    assert "systemctl start dds3-mass@10000" not in text
    assert "! systemctl is-active --quiet dds3-mass@10000.service" in text
    assert "fallback_used') is False" in text
    assert "1CVInlmO73-BvdIpJM1ZGvoUegiKTnjYU" in text
    assert "ef126c6842dda691b08325392b9d7fe5319acdba34b2db6b8981f03d56f8e130" in text
    assert "8a21cf06ab7ac424ee0f245ccf274e6d6f4f7135fa9b8c4c0e52c595c0da5996" in text
