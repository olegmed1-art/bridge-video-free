"""Emit reproducible before/after teacher decisions for synthetic own hands."""
import json
import sys

from bridge_school_api.l1_canonical_runtime_v4 import evaluate
from .consumer import MODE, PROFILE, decide


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    cases = [
        ("singleton_hearts", dict(task="assess_call", actor="S", auction=["1NT","PASS"],
         proposed_call="3H", hand="S2 S3 S4 H2 D2 D3 D4 D5 C2 C3 C4 C5 C6".split())),
        ("major_rebid", dict(task="recommend", actor="N", auction=["1H","PASS","1NT","PASS"],
         hand="S2 S3 S4 H2 H3 H4 H5 H6 D2 D3 D4 C2 C3".split(),
         school_points={"value":16,"basis":"SYNTHETIC_SCHOOL_POINTS"})),
        ("overlap_requires_selection_policy", dict(task="recommend", actor="S", auction=["1NT","PASS"],
         hand="S2 S3 S4 S5 H2 H3 H4 D2 D3 D4 C2 C3 C4".split(),
         school_points={"value":8,"basis":"SYNTHETIC_SCHOOL_POINTS"})),
    ]
    output = []
    for name, context in cases:
        request = dict(mode=MODE, system_profile=PROFILE, dealer="N", **context)
        after = decide(request, enabled=True)
        rule_id = after["checks"][0]["rule_id"]
        baseline = evaluate(rule_id, {})
        output.append(dict(case=name, request=request,
                           before_l1={"status":baseline.status,"action":baseline.action},
                           before_test_mode=decide(request), after=after))
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
