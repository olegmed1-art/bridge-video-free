from __future__ import annotations

import json

from oracle_cost_gate import CostGateError, validate_30k_budget


def reject(value, fragment: str) -> None:
    try:
        validate_30k_budget(value)
    except CostGateError as exc:
        assert fragment in str(exc), (fragment, str(exc))
    else:
        raise AssertionError("unsafe budget was accepted")


def main() -> None:
    gate = validate_30k_budget({"max_runtime_seconds": 10800, "max_cost_usd": 2.0})
    assert gate.hourly_retail_usd == 0.2673624
    assert gate.estimated_ceiling_usd == 0.802087
    reject(None, "budget object")
    reject({"max_runtime_seconds": 10801, "max_cost_usd": 2.0}, "1..10800")
    reject({"max_runtime_seconds": 10800, "max_cost_usd": 2.01}, "<= 2.00")
    reject({"max_runtime_seconds": 10800, "max_cost_usd": 0.5}, "below runtime ceiling")
    print(json.dumps({"ok": True, "fail_closed": True, "max_runtime_seconds": 10800,
                      "max_cost_usd": 2.0, "retail_ceiling_usd": gate.estimated_ceiling_usd}))


if __name__ == "__main__":
    main()
