from __future__ import annotations

import json

from bridge_school_api.l1_canonical_registry import ACTIVE_DOMAIN_RULE_IDS
from bridge_school_api.l1_canonical_runtime_v2 import evaluate as evaluate_v2
from bridge_school_api.l1_canonical_runtime_v3 import EXTRA_SOURCE_EXPLICIT_RULE_IDS


def v2_bounded_rule_ids() -> tuple[str, ...]:
    bounded: list[str] = []
    for rule_id in ACTIVE_DOMAIN_RULE_IDS:
        result = evaluate_v2(rule_id, {})
        if not (
            result.status == "BLOCK"
            and result.action == "KNOWN_RULE_NOT_EXECUTABLE"
        ):
            bounded.append(rule_id)
    return tuple(bounded)


def coverage_snapshot() -> dict[str, object]:
    active = set(ACTIVE_DOMAIN_RULE_IDS)
    v2 = set(v2_bounded_rule_ids())
    v3_extra = set(EXTRA_SOURCE_EXPLICIT_RULE_IDS)
    executable = v2 | v3_extra
    remaining = active - executable
    overlap = v2 & v3_extra
    return {
        "active_domain_rules": len(active),
        "v2_bounded_rules": len(v2),
        "v3_extra_source_explicit_rules": len(v3_extra),
        "v2_v3_overlap": len(overlap),
        "v3_total_bounded_rules": len(executable),
        "remaining_known_unbounded_rules": len(remaining),
        "remaining_rule_ids": sorted(remaining),
        "overlap_rule_ids": sorted(overlap),
    }


def main() -> None:
    print(json.dumps(coverage_snapshot(), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
