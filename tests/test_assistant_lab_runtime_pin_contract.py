from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PIN = (ROOT / "assistant_lab/ORACLE_RUNTIME_PIN").read_text(encoding="utf-8").strip()
STACK = (ROOT / "ops/cloud_shell_activate_assistant_lab_stack.sh").read_text(encoding="utf-8")
OBSERVER = (ROOT / "ops/cloud_shell_activate_assistant_lab_observer.sh").read_text(encoding="utf-8")
CONTROL = (ROOT / "ops/cloud_shell_activate_assistant_lab_control_bridge.sh").read_text(encoding="utf-8")


def test_canonical_runtime_pin_is_single_immutable_commit():
    assert re.fullmatch(r"[0-9a-f]{40}", PIN)
    assert f"readonly RUNTIME_COMMIT='{PIN}'" in STACK
    assert "97f20e5a4ad5d6229d2d55db0558aa7edf1f3f99" not in STACK
    assert "3fe5874699d5d5cbc2bffb324dee458bdc5b0fce" not in STACK


def test_legacy_launchers_delegate_to_one_canonical_launcher():
    for text in (OBSERVER, CONTROL):
        assert "cloud_shell_activate_assistant_lab_stack.sh" in text
        assert "exec bash" in text
        assert "readonly RUNTIME_COMMIT=" not in text


def test_stack_preserves_control_boundaries():
    assert "127.0.0.1:8765" in STACK
    assert "0.0.0.0:8765" in STACK
    assert "arbitrary_shell" in STACK
    assert "video_analyzer_result_access" in STACK
    assert "other_oracle_result_access" in STACK
    assert "assistant-lab-control-bridge.service" in STACK
    assert "git merge-base --is-ancestor" in STACK


def test_stack_has_bounded_rpc_only_rollout_mode():
    assert "probe|status|rollout|activate" in STACK
    assert "assistant-lab-control-rpc-rollout" in STACK
    assert "git status --porcelain --untracked-files=no" in STACK
    assert "assistant_lab.claim_control_command" in STACK
    assert "assistant_lab.finish_control_command" in STACK
    assert "systemctl restart assistant-lab-control-bridge.service" in STACK
    assert "ASSISTANT_LAB_CONTROL_RPC_ROLLOUT_PASS" in STACK
