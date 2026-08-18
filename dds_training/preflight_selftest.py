from __future__ import annotations

import json

from preflight import run_quick


def main() -> None:
    result = run_quick()
    assert result["ok"] is True, result
    assert result["generated_and_validated"] == 64, result
    assert result["dds_batch_tables"] == result["dds_batch_limit"] == 40, result
    assert result["dds_over_limit_blocked"] is True, result
    assert result["context_reuse"] is True, result
    assert result["engine"]["solver_context"] is True, result
    print(json.dumps({
        "ok": True,
        "batch_limit": result["dds_batch_limit"],
        "over_limit_blocked": result["dds_over_limit_blocked"],
        "context_reuse": result["context_reuse"],
        "real_dds": True,
    }, indent=2))


if __name__ == "__main__":
    main()
