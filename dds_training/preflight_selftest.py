from __future__ import annotations

import json

from preflight import run_quick


def main() -> None:
    report = run_quick()
    assert report["ok"] is True, report
    assert report["generated_and_validated"] == 64, report
    assert report["dds_batch_tables"] == report["dds_batch_limit"] == 40, report
    assert report["dds_over_limit_blocked"] is True, report
    assert report["context_reuse"] is True, report
    assert report["engine"]["solver_context"] is True, report
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
