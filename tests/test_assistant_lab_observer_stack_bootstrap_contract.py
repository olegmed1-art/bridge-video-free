from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "ops/cloud_shell_activate_assistant_lab_observer.sh"


def test_cloud_shell_observer_launcher_repairs_the_complete_stack():
    text = LAUNCHER.read_text(encoding="utf-8")
    assert "97f20e5a4ad5d6229d2d55db0558aa7edf1f3f99" in text
    assert "ASSISTANT_LAB_OBSERVER_ARCHIVE_ROOT='$ARCHIVE_DIR'" in text
    assert "ASSISTANT_LAB_OBSERVER_ACTIVATE=1" in text
    assert "ASSISTANT_LAB_CONTROL_BRIDGE_ACTIVATE=1" in text
    assert "assistant-lab-control-bridge.service" in text
    assert "git merge --ff-only '$RUNTIME_COMMIT'" in text
    assert "arbitrary remote command" in text
