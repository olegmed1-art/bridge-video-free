from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database/migrations/0057_universal_video_terminal_evidence_gate.sql"
SQL_REGRESSION = ROOT / "database/tests/041_universal_video_queue.sql"


def test_terminal_gate_is_forward_only_and_precedes_mutation() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    gate = "PERFORM video_queue.assert_terminal_evidence(p_output,v_job,v_batch)"
    assert gate in sql
    assert sql.index(gate) < sql.index("UPDATE video_queue.job SET status=p_outcome")
    assert "0056_universal_video_queue.sql" not in sql
    assert "0057_universal_video_terminal_evidence_gate" in sql


def test_gate_requires_drive_contract_and_keeps_helpers_private() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    for required in (
        "source_identity_verified",
        "route_readback_verified",
        "result_readback_verified",
        "checksum_verified",
        "application/pdf",
        "application/json",
        "manifest_sha256",
        "evidence_sha256",
        "terminal_evidence_sha256",
    ):
        assert required in sql
    assert "REVOKE ALL ON FUNCTION video_queue.canonical_json(jsonb)" in sql
    assert "REVOKE ALL ON FUNCTION video_queue.assert_terminal_evidence" in sql
    assert "GRANT EXECUTE ON FUNCTION video_queue.finish_job" in sql


def test_sql_regression_proves_fail_closed_atomicity() -> None:
    sql = SQL_REGRESSION.read_text(encoding="utf-8")
    assert "minimal terminal evidence was accepted" in sql
    assert "failed terminal evidence mutated queue state" in sql
    assert "contradictory artifact evidence was accepted" in sql
    assert "successful canary did not release remaining jobs" in sql
