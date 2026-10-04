"""Replay every mapped decision's cases, emit evidence, fail on any mismatch."""
import json
from pathlib import Path
import sys

from .consumer import decide
from .scenarios import CASES, scenarios
from bridge_school_api.l1_canonical_runtime_v4 import evaluate


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    mapping = json.loads(Path(__file__).with_name("decision_mapping.json").read_text(encoding="utf-8"))
    evidence = []
    for row in mapping:
        examples = []
        for case in CASES:
            if "RULE-TOUR-"+case[0] not in row["rule_ids"]:
                continue
            for category, request, expected in scenarios(case):
                result = decide(request,enabled=True)
                if result["status"] != expected:
                    raise AssertionError((row["decision_id"],case[0],category,result))
                before = evaluate("RULE-TOUR-"+case[0],{})
                if before.status != "BLOCK" or before.action != "UNKNOWN_RULE_ID":
                    raise AssertionError((case[0],before))
                examples.append(dict(category=category, expected=expected, request=request,
                                     before_l1={"status":before.status,"action":before.action},
                                     before_test_mode=decide(request)["reason"],result=result))
        if not examples:
            raise AssertionError(row["decision_id"])
        evidence.append(dict(mapping=row, scenarios=examples))
    print(json.dumps(evidence,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
