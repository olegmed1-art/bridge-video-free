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
    assert "valid_acting_hand" in sql
    assert "valid_public_robot_payload" in sql
    assert "contains_nonpublic_card_material" in sql
    assert "BID_WORLD_ROBOT_TRACE_INCOMPLETE_OR_UNPINNED" in sql
    assert "key_name IN ('bid','action','calls')" in sql
    assert "key_name IN ('inputfingerprint','rawresponsesha256','inputhash','outputhash','modelhash','configurationhash')" in sql
    assert "WITH ORDINALITY AS s(step,ord)" in sql
    assert "step->>'input_hash' !~ '^[0-9a-f]{64}$'" in sql
    assert "canon_activation" not in sql.lower()


def test_world_guards_have_ephemeral_database_behavioral_suite():
    smoke = Path("database/tests/101_world_knowledge_v0.sql").read_text()
    assert "WORLD_SMOKE_FALLBACK_WITHOUT_SELECTION_ACCEPTED" in smoke
    assert "WORLD_SMOKE_PROFILE_MISMATCH_ACCEPTED" in smoke
    assert "WORLD_SMOKE_HIDDEN_DEAL_ACCEPTED" in smoke
    assert "WORLD_SMOKE_PACKED_CARD_TOKENS_ACCEPTED" in smoke
    assert "WORLD_SMOKE_NESTED_AUCTION_MATERIAL_ACCEPTED" in smoke
    assert "WORLD_SMOKE_UNPINNED_REVERSED_TRACE_ACCEPTED" in smoke
    assert "WORLD_SMOKE_DECISION_MUTATION_ACCEPTED" in smoke
