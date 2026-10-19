"""I2 identity counterexample regressions through the actual Teacher controller."""
from copy import deepcopy
import pytest
from bridge_school_api import teacher_entry_blocking_reader_path as reader_path
from bridge_school_api import teacher_v04_integration as teacher

def observation():
    return {"schema":"teacher-symbolic-entry-v1","profile":"SCHOOL_L1_DB_V1",
            "role":"declarer","phase":"suit_timing","trump":"NT","declarer":"N","dummy":"S",
            "dummy_visible":True,"target_suit":"S",
            "hand_a":{"seat":"N","honors":["A"],"length":3,"low_cards":["3","2"]},
            "hand_b":{"seat":"S","honors":["Q","J"],"length":4,"low_cards":["5","4"]},
            "side_entries_to_a":"NONE","leader":"N","current_trick":[],"request":"CONCRETE_CARD"}

def records():
    reader = reader_path.load_prepared_test_reader(test_only=True)
    return reader.read_eligible_skills(reader_path._request())

@pytest.mark.parametrize("identity_case",["same-version","different-version","UUID-case"])
def test_duplicate_immutable_identity_withholds_through_controller(identity_case):
    values = records()
    for value in values:
        value["item_id"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    if identity_case == "different-version":
        values[1]["version_no"] = 2
    elif identity_case == "UUID-case":
        values[1]["item_id"] = values[1]["item_id"].upper()
    reads = []
    def fetch(query):
        reads.append(deepcopy(query))
        return deepcopy(values)
    result = teacher.answer_entry_order(observation(),fetch,test_only=True)
    assert result["status"] == "ABSTAIN"
    assert result["reason"] == "DUPLICATE_KNOWLEDGE_ITEM_ID"
    assert len(reads) == 1
    for name in ("answer","decision","proposal","binding"):
        assert result[name] is None
    assert result["trace"] == result["knowledge_versions"] == []

def test_distinct_synthetic_UUID_versions_preserve_two_read_decision():
    values = records()
    values[0]["item_id"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    values[1]["item_id"] = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    reads = []
    def fetch(query):
        reads.append(deepcopy(query))
        return deepcopy(values)
    result = teacher.answer_entry_order(observation(),fetch,test_only=True)
    assert len(reads) == 2
    assert result["status"] == "CONDITIONAL_RECOMMENDATION"
    assert result["proposal"]["card"] == "S2"
    assert result["answer"] is not None and result["trace"]
    assert len({d["item_id"] for d in result["binding"]["dependencies"]}) == 2
