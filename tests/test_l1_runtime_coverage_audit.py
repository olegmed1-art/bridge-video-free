from __future__ import annotations

from tools.l1_runtime_coverage_audit import coverage_snapshot


def test_l1_runtime_coverage_partition_is_sound() -> None:
    snapshot = coverage_snapshot()
    assert snapshot["active_domain_rules"] == 111
    assert snapshot["v3_extra_source_explicit_rules"] == 40
    assert snapshot["v2_v3_overlap"] == 0
    assert snapshot["v3_total_bounded_rules"] + snapshot["remaining_known_unbounded_rules"] == 111


def test_remaining_rule_ids_are_stable_and_l1_only() -> None:
    snapshot = coverage_snapshot()
    remaining = snapshot["remaining_rule_ids"]
    assert isinstance(remaining, list)
    assert all(rule_id.startswith("RULE-L1-") for rule_id in remaining)
    assert not any(rule_id.startswith("RULE-TOUR-") for rule_id in remaining)
