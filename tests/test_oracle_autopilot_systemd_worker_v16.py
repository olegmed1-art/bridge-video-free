from pathlib import Path


UNIT = Path("deploy/oracle-autopilot/school-autopilot-shadow.service")


def test_shadow_service_uses_ibf_aware_worker_v16_entrypoint():
    text = UNIT.read_text(encoding="utf-8")
    assert "Environment=AUTOPILOT_RUNTIME_MODE=SHADOW" in text
    assert (
        "ExecStart=/opt/bridge-school/school-autopilot/.venv/bin/python "
        "-m oracle_autopilot.worker_v16"
    ) in text
    assert "-m oracle_autopilot.worker\n" not in text


def test_shadow_service_keeps_fail_closed_hardening():
    text = UNIT.read_text(encoding="utf-8")
    for required in (
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "PrivateDevices=true",
        "Restart=always",
        "ReadWritePaths=/opt/bridge-school/school-autopilot/runtime",
    ):
        assert required in text
