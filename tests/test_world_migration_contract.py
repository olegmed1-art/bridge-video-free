from pathlib import Path


def test_world_trace_validates_both_authority_arrays():
    sql = Path("database/migrations/0201_world_knowledge_v0.sql").read_text()
    assert "BID_WORLD_TRACE_CANON_CANDIDATE_NOT_CANON" in sql
    assert "BID_WORLD_TRACE_WORLD_CANDIDATE_NOT_EXTERNAL" in sql
    assert "BID_WORLD_TRACE_WORLD_BEFORE_CANON_GAP" in sql


def test_robot_paths_are_information_firewalled_and_not_canon_activations():
    sql = Path("database/migrations/0201_world_knowledge_v0.sql").read_text()
    assert "ROBOT_RECONSTRUCTED_SURFACE" in sql
    assert "ROBOT_LIVE_DECISION" in sql
    assert sql.count("NOT bidding.contains_forbidden_hidden_key") >= 10
    assert "canon_activation" not in sql.lower()
