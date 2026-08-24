from assistant_lab.contract import ALLOWED_KINDS


def test_assistant_lab_kinds_cover_unified_compute_chain():
    assert ALLOWED_KINDS == {"DDS3_COMPUTE", "BEN_COMPUTE", "NOOP"}
