from __future__ import annotations

import json
import os
from pathlib import Path

from independent_crosscheck import crosscheck


def main() -> None:
    solver_text = os.environ.get("DDS_INDEPENDENT_SOLVER", "").strip()
    if not solver_text:
        raise SystemExit("DDS_INDEPENDENT_SOLVER must point to the pinned independent solver binary")
    solver = Path(solver_text).resolve()
    assert solver.is_file(), solver
    result = crosscheck(solver, count=1, seed=20309914)
    assert result["ok"] is True, result
    assert result["checked_cells"] == 20, result
    assert result["mismatched_deals"] == 0, result
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
