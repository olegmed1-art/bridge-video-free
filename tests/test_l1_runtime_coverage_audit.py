from __future__ import annotations

from tools.l1_runtime_coverage_audit import coverage_snapshot


def test_l1_runtime_coverage_partition_is_sound() -> None:
    snapshot = coverage_snapshot()
    assert snapshot["active_domain_rules"] == 111
    assert snapshot["v2_bounded_rules"] == 40
    assert snapshot["v3_extra_source_explicit_rules"] == 40
    assert snapshot["v4_extra_procedural_rules"] == 30
    assert snapshot["v4_total_bounded_rules"] == 110
    assert snapshot["remaining_known_unbounded_rules"] == 1
    overlaps = snapshot["overlap_rule_ids"]
    assert overlaps == {"v2_v3": [], "v2_v4": [], "v3_v4": []}


def test_only_qualitative_competitive_principle_remains_unbounded() -> None:
    snapshot = coverage_snapshot()
    assert snapshot["remaining_rule_ids"] == ["RULE-L1-COMPETITIVE-STRENGTH-PRINCIPLE"]
