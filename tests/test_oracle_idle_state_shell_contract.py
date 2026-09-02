from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_shell_is_read_only_and_routes_through_collector_and_evaluator() -> None:
    text = (ROOT / "ops" / "oracle_idle_state.sh").read_text(encoding="utf-8")
    assert "oracle_idle_collect.py" in text
    assert "oracle_idle_guard.py" in text
    assert "ORACLE_IDLE_STATE=UNKNOWN" in text
    assert '"schema":"oracle-idle-verdict-v1"' in text
    assert '"evidence":{}' in text
    assert 'printf \'%s\\n\' "$json_line"' in text
    lowered = text.lower()
    assert "oci compute" not in lowered
    assert "instance action" not in lowered
    assert "--action stop" not in lowered
