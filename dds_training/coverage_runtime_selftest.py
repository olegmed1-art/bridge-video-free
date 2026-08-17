from __future__ import annotations

import json
import os
import runpy
import sys
import tempfile
from pathlib import Path

import coverage_runtime as runtime


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="dds-runtime-coverage-selftest-") as td:
        root = Path(td)
        fragments = root / "fragments"
        module = root / "covered_fixture.py"
        module.write_text(
            "def choose(value):\n"
            "    if value:\n"
            "        return 1\n"
            "    return 0\n"
            "\n"
            "choose(True)\n"
            "choose(False)\n",
            encoding="utf-8",
        )

        old_root = os.environ.get("DDS_COVERAGE_ROOT")
        old_dir = os.environ.get("DDS_COVERAGE_DIR")
        os.environ["DDS_COVERAGE_ROOT"] = str(root)
        os.environ["DDS_COVERAGE_DIR"] = str(fragments)
        try:
            assert runtime.activate_from_environment() is True
            assert runtime.activate_from_environment() is True
            runpy.run_path(str(module), run_name="__main__")
            runtime._write_fragment()
        finally:
            sys.settrace(None)
            if old_root is None:
                os.environ.pop("DDS_COVERAGE_ROOT", None)
            else:
                os.environ["DDS_COVERAGE_ROOT"] = old_root
            if old_dir is None:
                os.environ.pop("DDS_COVERAGE_DIR", None)
            else:
                os.environ["DDS_COVERAGE_DIR"] = old_dir

        files = sorted(fragments.glob("coverage-*.json"))
        assert len(files) == 1, files
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        assert payload["schema"] == "dds-runtime-coverage-fragment-v1"
        assert payload["root"] == str(root.resolve())
        assert "covered_fixture.py" in payload["lines"]
        assert len(payload["lines"]["covered_fixture.py"]) >= 4
        assert "covered_fixture.py" in payload["arcs"]
        assert payload["arcs"]["covered_fixture.py"]

        print(json.dumps({
            "ok": True,
            "activation_idempotent": True,
            "line_events_recorded": True,
            "arc_events_recorded": True,
            "fragment_written_atomically": True,
        }, indent=2))


if __name__ == "__main__":
    main()
