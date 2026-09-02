from pathlib import Path


def test_control_worker_uses_constrained_rpc_only():
    schema = Path("assistant_lab/control_schema.sql").read_text(encoding="utf-8")
    bridge = Path("assistant_lab/control_bridge.py").read_text(encoding="utf-8")

    assert "REVOKE ALL ON assistant_lab.control_command FROM assistant_lab_worker;" in schema
    assert "GRANT SELECT, UPDATE ON assistant_lab.control_command TO assistant_lab_worker" not in schema
    assert schema.count("SECURITY DEFINER") >= 3
    assert schema.count("SET search_path = pg_catalog, assistant_lab") >= 3
    assert "GRANT EXECUTE ON FUNCTION assistant_lab.claim_control_command(text)" in schema
    assert "GRANT EXECUTE ON FUNCTION assistant_lab.finish_control_command(uuid,text,text,jsonb,text)" in schema
    assert "GRANT EXECUTE ON FUNCTION assistant_lab.recover_stale_control_commands(integer)" in schema

    assert "assistant_lab.claim_control_command" in bridge
    assert "assistant_lab.finish_control_command" in bridge
    assert "assistant_lab.recover_stale_control_commands" in bridge
    assert "UPDATE assistant_lab.control_command" not in bridge
    assert "FROM assistant_lab.control_command" not in bridge
